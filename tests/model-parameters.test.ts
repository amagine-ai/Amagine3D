import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { promisify } from 'node:util';

import {
  parameterModelsForWorkspace,
  rebuildModelWithParameters,
} from '../server/model-parameters.ts';
import { parameterBuildRequestSchema } from '../server/trpc/schemas.ts';

const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
const PROJECT_ROOT = resolve(import.meta.dirname, '..');
const VENV_PYTHON =
  process.platform === 'win32'
    ? join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
    : join(PROJECT_ROOT, '.venv', 'bin', 'python');
const execFileAsync = promisify(execFile);

function modelSource(): string {
  return `import hashlib
import json
import os
from pathlib import Path

def parameter(parameter_id, default, **metadata):
    overrides = json.loads(os.environ.get("AMAGINE3D_PARAMETER_OVERRIDES", "{}"))
    return overrides.get(parameter_id, default)

NAME = "model"
SIZE = parameter(
    "local-offset",
    -2.5,
    min_value=-5.0,
    max_value=5.0,
    step=0.5,
    unit="mm",
    label="Local offset",
    label_zh="局部偏移",
    group="Feature",
    group_zh="局部特征",
    affects=("mounting-hole",),
)

if SIZE > 3:
    raise RuntimeError("simulated topology failure")

output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", "."))
output.mkdir(parents=True, exist_ok=True)
stl = output / "model.stl"
assemble_step = output / "model-assemble.step"
display_glb = output / "model-display.glb"
stl.write_text(f"solid {SIZE}\\nendsolid model\\n", encoding="utf-8")
assemble_step.write_text(f"ASSEMBLE STEP {SIZE}\\n", encoding="utf-8")
display_glb.write_bytes(b"glTF" + bytes(str(SIZE), encoding="utf-8"))
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
report = {
    "schema": "evidence-cad-build/v4",
    "part": NAME,
    "source": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__))},
    "artifacts": {
        "stl": {"path": str(stl.resolve()), "sha256": digest(stl)},
        "step:assemble": {"path": str(assemble_step.resolve()), "sha256": digest(assemble_step)},
        "glb:display": {"path": str(display_glb.resolve()), "sha256": digest(display_glb)},
    },
    "parameters": {
        "local-offset": {"default": -2.5, "value": SIZE},
    },
}
(output / "model_report.json").write_text(json.dumps(report), encoding="utf-8")
print(json.dumps(report))
`;
}

async function writeInitialBuild(root: string): Promise<void> {
  const sourcePath = join(root, 'model.py');
  const stlPath = join(root, 'model.stl');
  const assembleStepPath = join(root, 'model-assemble.step');
  const displayGlbPath = join(root, 'model-display.glb');
  await writeFile(sourcePath, modelSource());
  await writeFile(stlPath, 'solid -2.5\nendsolid model\n');
  await writeFile(assembleStepPath, 'ASSEMBLE STEP -2.5\n');
  await writeFile(displayGlbPath, 'glTF -2.5\n');
  await writeFile(
    join(root, 'model_report.json'),
    JSON.stringify({
      artifacts: {
        stl: { path: stlPath, sha256: 'initial' },
        'step:assemble': { path: assembleStepPath, sha256: 'initial' },
        'glb:display': { path: displayGlbPath, sha256: 'initial' },
      },
      part: 'model',
      schema: 'evidence-cad-build/v4',
      source: { path: sourcePath, sha256: 'initial' },
    }),
  );
}

test('discovers only explicit parameter() declarations, including negative defaults', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-parameters-'));
  try {
    await writeInitialBuild(root);
    const [model] = await parameterModelsForWorkspace(root, PYTHON);
    assert.equal(model?.primaryPreviewPath, 'model.stl');
    assert.equal(model?.displayPreviewPath, 'model-display.glb');
    assert.equal(model?.parameters.length, 1);
    assert.deepEqual(model?.parameters[0], {
      affects: ['mounting-hole'],
      defaultValue: -2.5,
      group: 'Feature',
      groupZh: '局部特征',
      id: 'local-offset',
      kind: 'number',
      label: 'Local offset',
      labelZh: '局部偏移',
      maximum: 5,
      minimum: -5,
      name: 'SIZE',
      step: 0.5,
      unit: 'mm',
      value: -2.5,
    });

    await writeFile(
      join(root, 'model.py'),
      modelSource().replace('label_zh="局部偏移"', 'label_zh=42'),
    );
    const [fallbackModel] = await parameterModelsForWorkspace(root, PYTHON);
    assert.equal(fallbackModel?.parameterError, undefined);
    assert.equal(fallbackModel?.parameters[0]?.label, 'Local offset');
    assert.equal(fallbackModel?.parameters[0]?.labelZh, undefined);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rebuilds the complete model in staging and commits source only after success', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-parameter-build-'));
  try {
    await writeInitialBuild(root);
    const [model] = await parameterModelsForWorkspace(root, PYTHON);
    assert.ok(model);
    await rebuildModelWithParameters({
      pythonExecutable: PYTHON,
      request: {
        primaryPreviewPath: model.primaryPreviewPath,
        sourceHash: model.sourceHash,
        sourcePath: model.sourcePath,
        values: { 'local-offset': -1.5 },
      },
      workspaceRoot: root,
    });
    assert.match(await readFile(join(root, 'model.py'), 'utf8'), /\n\s+-1\.5,/u);
    assert.match(await readFile(join(root, 'model.stl'), 'utf8'), /-1\.5/u);
    const report = JSON.parse(
      await readFile(join(root, 'model_report.json'), 'utf8'),
    ) as { source: { path: string; sha256: string } };
    assert.equal(report.source.path, join(root, 'model.py'));
    assert.match(report.source.sha256, /^[a-f0-9]{64}$/u);

    const committedSource = await readFile(join(root, 'model.py'), 'utf8');
    const committedStl = await readFile(join(root, 'model.stl'), 'utf8');
    const [committedModel] = await parameterModelsForWorkspace(root, PYTHON);
    assert.ok(committedModel);
    await assert.rejects(
      rebuildModelWithParameters({
        pythonExecutable: PYTHON,
        request: {
          primaryPreviewPath: committedModel.primaryPreviewPath,
          sourceHash: committedModel.sourceHash,
          sourcePath: committedModel.sourcePath,
          values: { 'local-offset': 4 },
        },
        workspaceRoot: root,
      }),
      /simulated topology failure/u,
    );
    assert.equal(await readFile(join(root, 'model.py'), 'utf8'), committedSource);
    assert.equal(await readFile(join(root, 'model.stl'), 'utf8'), committedStl);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects malformed parameter build requests', () => {
  assert.equal(parameterBuildRequestSchema.safeParse({ values: {} }).success, false);
  assert.equal(
    parameterBuildRequestSchema.safeParse({
      primaryPreviewPath: 'model.stl',
      sourceHash: 'a'.repeat(64),
      sourcePath: 'model.py',
      values: { size: Number.NaN },
    }).success,
    false,
  );
});

test(
  'rebuilds a real build123d model through the checked helper runtime',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-build123d-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d');
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Box
from cad_helpers import export_part, observe, parameter

NAME = "parametric_box"
WIDTH = parameter(
    "overall-width", 20.0,
    min_value=10.0, max_value=30.0, step=0.5,
    unit="mm", label="Overall width", label_zh="总体宽度",
    group="Envelope", group_zh="外形尺寸",
    affects=("primary-envelope",),
)
DEPTH = 12.0
HEIGHT = 6.0
body = Box(WIDTH, DEPTH, HEIGHT)
observe(body, "primary-envelope", "envelope")

if __name__ == "__main__":
    export_part(body, NAME, source_path=__file__)
`;
      await writeFile(join(root, 'parametric_box.py'), source);
      await execFileAsync(VENV_PYTHON, ['parametric_box.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      assert.equal(model.primaryPreviewPath, 'parametric_box.stl');
      assert.equal(model.displayPreviewPath, 'parametric_box-display.glb');
      assert.equal(model.parameters[0]?.labelZh, '总体宽度');
      assert.equal(model.parameters[0]?.groupZh, '外形尺寸');
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'overall-width': 24 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'parametric_box_report.json'), 'utf8'),
      ) as {
        parameters: Record<string, { group_zh?: string; label_zh?: string }>;
        shape: { bbox_mm: { size: number[] } };
      };
      assert.deepEqual(report.shape.bbox_mm.size, [24, 12, 6]);
      assert.equal(report.parameters['overall-width']?.label_zh, '总体宽度');
      assert.equal(report.parameters['overall-width']?.group_zh, '外形尺寸');
      assert.match(
        await readFile(join(root, 'parametric_box.py'), 'utf8'),
        /"overall-width", 24\.0/u,
      );
      const [rebuiltModel] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.equal(rebuiltModel?.parameters[0]?.kind, 'number');
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'treats the 3MF as the adjustable top-level multi-color print root',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-color-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d-color');
      await writeFile(
        join(root, 'color_bar_intent.json'),
        `${JSON.stringify({
          color_regions: [
            {
              hex: '#F05A35',
              material: { transmission: 'opaque' },
              name: 'left',
            },
            {
              hex: '#171717',
              material: { transmission: 'opaque' },
              name: 'right',
            },
          ],
          coordinate_system: {
            back: 'y-max',
            bottom: 'z-min',
            front: 'y-min',
            left: 'x-min',
            right: 'x-max',
            top: 'z-max',
            x_positive: 'right',
            y_positive: 'back',
            z_positive: 'top',
          },
          features: [
            { id: 'complete-parent' },
            { id: 'left-region' },
            { id: 'right-region' },
          ],
          part: 'color_bar',
          printability: {
            critical_features: [
              'complete-parent',
              'left-region',
              'right-region',
            ],
          },
          schema: 'evidence-color-intent/v3',
        })}\n`,
        'utf8',
      );
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Box, Pos
from cad_helpers import export_regions, observe, parameter

NAME = "color_bar"
INTENT = "color_bar_intent.json"
WIDTH = parameter(
    "overall-width", 20.0,
    min_value=10.0, max_value=30.0, step=0.5,
    unit="mm", label="Overall width", group="Envelope",
    affects=("complete-parent", "left-region", "right-region"),
)
left = Box(WIDTH / 2, 10, 5)
right = Pos(WIDTH / 2, 0, 0) * Box(WIDTH / 2, 10, 5)
parent = left + right
observe(parent, "complete-parent", "parent")
observe(left, "left-region", "color-region")
observe(right, "right-region", "color-region")
regions = {
    "left": (left, "#F05A35"),
    "right": (right, "#171717"),
}

if __name__ == "__main__":
    export_regions(regions, NAME, parent=parent, intent_path=INTENT, source_path=__file__)
`;
      await writeFile(join(root, 'color_bar.py'), source);
      await execFileAsync(VENV_PYTHON, ['color_bar.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      assert.equal(model.primaryPreviewPath, 'color_bar.3mf');
      assert.equal(model.displayPreviewPath, 'color_bar-display.glb');
      assert.deepEqual(
        model.artifactPaths.slice().sort(),
        [
          'color_bar.3mf',
          'color_bar.stl',
          'color_bar-assemble.step',
          'color_bar-display.glb',
        ].sort(),
      );
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'overall-width': 24 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'color_bar_report.json'), 'utf8'),
      ) as { features: { 'complete-parent': { bbox_mm: { size: number[] } } } };
      assert.deepEqual(
        report.features['complete-parent'].bbox_mm.size,
        [24, 10, 5],
      );
      assert.ok((await readFile(join(root, 'color_bar.3mf'))).byteLength > 0);
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'treats the STL as the adjustable top-level single-color print root',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-assembly-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d');
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Align, Box, Pos
from cad_helpers import export_assembly, observe, parameter

NAME = "shell_case"
INTENT = "shell_case_intent.json"
WIDTH = parameter(
    "overall-width", 20.0,
    min_value=10.0, max_value=30.0, step=0.5,
    unit="mm", label="Overall width", group="Envelope",
    affects=("lower-shell", "top-lid"),
)
lower_shell = Box(WIDTH, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
top_lid = Pos(0, 0, 6) * Box(WIDTH, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
observe(lower_shell, "lower-shell", "part", part_name="lower-shell")
observe(top_lid, "top-lid", "part", part_name="top-lid")

if __name__ == "__main__":
    export_assembly(
        {"lower-shell": lower_shell, "top-lid": top_lid},
        NAME,
        intent_path=INTENT,
        source_path=__file__,
    )
`;
      await writeFile(join(root, 'shell_case.py'), source);
      await writeFile(
        join(root, 'shell_case_intent.json'),
        JSON.stringify({
          schema: 'evidence-cad-intent/v4',
          part: 'shell_case',
          coordinate_system: {
            back: 'y-max',
            bottom: 'z-min',
            front: 'y-min',
            left: 'x-min',
            right: 'x-max',
            top: 'z-max',
            x_positive: 'right',
            y_positive: 'back',
            z_positive: 'top',
          },
          manufacturing: {
            mode: 'multipart',
            parts: [
              {
                acceptance: 'one lower shell',
                name: 'lower-shell',
                role: 'base',
              },
              {
                acceptance: 'one top lid',
                name: 'top-lid',
                role: 'cover',
              },
            ],
            interfaces: [
              {
                acceptance: 'named parts form one case',
                assembly_axis: '+Z',
                between: ['lower-shell', 'top-lid'],
                clearance_mm: 0,
                connection: 'glue-face',
                engagement_mm: 2,
                features: ['lower-shell', 'top-lid'],
                id: 'case-seam',
              },
            ],
          },
        }),
      );
      await execFileAsync(VENV_PYTHON, ['shell_case.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      assert.equal(model.primaryPreviewPath, 'shell_case.stl');
      assert.equal(model.displayPreviewPath, 'shell_case-display.glb');
      assert.deepEqual(
        model.artifactPaths.slice().sort(),
        [
          'shell_case-lower-shell.stl',
          'shell_case-top-lid.stl',
          'shell_case.stl',
          'shell_case-assemble.step',
          'shell_case-display.glb',
        ].sort(),
      );
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'overall-width': 24 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'shell_case_report.json'), 'utf8'),
      ) as {
        assembly: {
          shape: { bbox_mm: { size: number[] }; solid_count: number };
        };
        parts: Record<string, { bbox_mm: { size: number[] } }>;
        print_plate: { bbox_mm: { size: number[] }; solid_count: number };
      };
      assert.equal(report.assembly.shape.solid_count, 2);
      assert.deepEqual(report.assembly.shape.bbox_mm.size, [24, 10, 8]);
      assert.equal(report.print_plate.solid_count, 2);
      assert.deepEqual(report.print_plate.bbox_mm.size, [53, 10, 4]);
      assert.deepEqual(report.parts['top-lid']?.bbox_mm.size, [24, 10, 2]);
      assert.match(
        await readFile(join(root, 'shell_case.py'), 'utf8'),
        /"overall-width", 24\.0/u,
      );
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);
