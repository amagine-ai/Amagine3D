import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { promisify } from 'node:util';
import { unzipSync } from 'fflate';

import {
  parameterModelsForWorkspace,
  rebuildModelWithParameters,
} from '../server/model-parameters.ts';
import { parameterBuildRequestSchema } from '../server/trpc/schemas.ts';
import { scanArtifacts } from '../server/artifacts.ts';
import { discoverModelBuilds } from '../server/model-builds.ts';

const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
const PROJECT_ROOT = resolve(import.meta.dirname, '..');
const VENV_PYTHON =
  process.platform === 'win32'
    ? join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
    : join(PROJECT_ROOT, '.venv', 'bin', 'python');
const execFileAsync = promisify(execFile);
const PROFILE_PATH = join(
  PROJECT_ROOT,
  'skills',
  'text-a3d',
  'examples',
  'bambu-a1-mini-0.4-standard.example.json',
);
const COORDINATE_SYSTEM = {
  back: 'y-max',
  bottom: 'z-min',
  front: 'y-min',
  left: 'x-min',
  right: 'x-max',
  top: 'z-max',
  x_positive: 'right',
  y_positive: 'back',
  z_positive: 'top',
};

test('parameter rebuilds support single to multiple plates and back without promoting stale files',
  { skip: !existsSync(VENV_PYTHON) }, async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-plate-parameter-'));
    try {
      const inputs = await writeEvidenceInputs({
        name: 'plate_pair', root, parts: ['lower', 'upper'], dimensionsMm: [170, 170, 60],
        features: ['lower', 'upper'].map((part) => ({ id: part, kind: 'interface', part })),
        manufacturing: {
          mode: 'multipart',
          parts: ['lower', 'upper'].map((name) => ({ name, role: name, acceptance: 'separate solid' })),
          interfaces: [{ id: 'seam', connection: 'glue-face', assembly_axis: '+Z', engagement_mm: 1,
            features: ['lower', 'upper'], between: ['lower', 'upper'], acceptance: 'paired assembly faces' }],
        },
      });
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(join(PROJECT_ROOT, 'skills', 'text-a3d'))})
from build123d import Box, Pos
from cad_helpers import export_assembly, observe, parameter
SIZE = parameter("size", 40.0, min_value=40, max_value=170, step=10, unit="mm", label="Size", affects=("lower", "upper"))
# Opposite envelope corners stay fixed while the two internal footprints grow.
offset = (170 - SIZE) / 2
parts = {"lower": Pos(-offset,-offset,15)*Box(SIZE,SIZE,30), "upper": Pos(offset,offset,45)*Box(SIZE,SIZE,30)}
for name, shape in parts.items():
    observe(shape, name, "solid", part_name=name)
export_assembly(parts, "plate_pair", source_path=__file__, intent_path=${JSON.stringify(inputs.intent)}, scene_path=${JSON.stringify(inputs.scene)})
`;
      await writeFile(join(root, 'plate_pair.py'), source);
      await execFileAsync(VENV_PYTHON, ['plate_pair.py'], { cwd: root });
      for (const [size, expectedCount] of [[170, 2], [40, 1]] as const) {
        const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
        assert.ok(model);
        await rebuildModelWithParameters({ pythonExecutable: VENV_PYTHON, workspaceRoot: root,
          request: { sourcePath: model.sourcePath, sourceHash: model.sourceHash,
            primaryPreviewPath: model.primaryPreviewPath, values: { size } } });
        const [build] = await discoverModelBuilds(root, await scanArtifacts(root));
        assert.ok(build, 'promoted report must retain valid hashes and bindings');
        assert.equal(build.printPlates?.length ?? 1, expectedCount);
        assert.equal(build.topLevelArtifactPaths.length, 1 + expectedCount * 2);
        assert.equal(build.primaryPreviewPath, expectedCount === 1 ? 'plate_pair.3mf' : 'plate_pair-plate-01.3mf');
        assert.ok(build.artifactPaths.every((path) => !path.startsWith('.amagine-state')));
        if (build.printPlates) {
          const owners: string[] = [];
          for (const plate of build.printPlates) {
            const archive = unzipSync(await readFile(join(root, plate.threeMfPath)));
            const xml = new TextDecoder().decode(archive['3D/3dmodel.model']);
            const names = [...xml.matchAll(/<object\b[^>]*\bname="([^"]+)"/gu)].map((match) => match[1]!);
            assert.equal(names.length, 1, 'each independent archive contains only its assigned solid');
            owners.push(...names);
          }
          assert.deepEqual(owners.sort(), ['lower', 'upper']);
        }
      }
      assert.ok(existsSync(join(root, 'plate_pair-plate-02.stl')), 'old files may remain on disk');
      const [final] = await discoverModelBuilds(root, await scanArtifacts(root));
      assert.ok(!final!.topLevelArtifactPaths.includes('plate_pair-plate-02.stl'), 'orphaned plates are not offered as current outputs');
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  });

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex');
}

interface EvidenceFeature {
  id: string;
  kind: 'envelope' | 'interface' | 'region';
  part?: string;
}

async function writeEvidenceInputs(options: {
  colorRegions?: Array<{
    hex: string;
    name: string;
  }>;
  dimensionsMm?: [number, number, number];
  features: EvidenceFeature[];
  manufacturing: Record<string, unknown>;
  name: string;
  parts: string[];
  root: string;
}): Promise<{ intent: string; scene: string }> {
  const {
    colorRegions,
    dimensionsMm = [20, 10, 8],
    features,
    manufacturing,
    name,
    parts,
    root,
  } = options;
  const profileBytes = await readFile(PROFILE_PATH);
  const intentPath = join(root, `${name}_intent.json`);
  const scenePath = join(root, `${name}_scene.json`);
  const intent = {
    assumptions: [],
    ...(colorRegions
      ? {
          color_regions: colorRegions.map((region) => ({
            boundary: `${region.name} occupies its declared volumetric region`,
            evidence: `${region.name} is required by the test specification`,
            hex: region.hex,
            name: region.name,
            part: name,
            purpose: `${region.name} print color`,
          })),
          palette_reduction: {
            applied: false,
            reason:
              'The two declared regions map directly to two print colors.',
          },
        }
      : {}),
    coordinate_system: COORDINATE_SYSTEM,
    dimensions_mm: {
      x: { confidence: 'high', source: 'user', value: dimensionsMm[0] },
      y: { confidence: 'high', source: 'user', value: dimensionsMm[1] },
      z: { confidence: 'high', source: 'user', value: dimensionsMm[2] },
    },
    features: features.map((feature) => ({
      acceptance: `${feature.id} is present in the generated solid`,
      evidence: `${feature.id} is required by the test specification`,
      ...feature,
    })),
    manufacturing,
    part: name,
    printability: {
      bed_contact: 'z-min',
      build_axis: '+Z',
      critical_features: features.map(({ id }) => id),
      minimum_wall_target_mm: 0.9,
      ...(colorRegions ? { print_package_mode: 'co_print_body' } : {}),
      profile: {
        path: PROFILE_PATH,
        sha256: sha256(profileBytes),
      },
      support_policy: 'support-free',
    },
    reference_files: [],
    representation: 'full-3d',
    schema: 'evidence-cad-intent/v5',
    task_mode: 'specification',
    visual: {
      landmarks: ['The generated envelope is visible in the preview.'],
      reference_view: 'front',
      required: true,
    },
  };
  const intentJson = JSON.stringify(intent);
  const featureOwners = new Map(
    features.map((feature) => [
      feature.id,
      feature.part ?? (parts.length === 1 ? parts[0] : undefined),
    ]),
  );
  const declaredInterfaces = Array.isArray(manufacturing.interfaces)
    ? (manufacturing.interfaces as Array<Record<string, unknown>>)
    : [];
  const sceneInterfaces = declaredInterfaces.flatMap((entry) => {
    const interfaceFeatures = Array.isArray(entry.features)
      ? entry.features.filter(
          (value): value is string => typeof value === 'string',
        )
      : [];
    if (
      typeof entry.id !== 'string' ||
      typeof entry.connection !== 'string' ||
      interfaceFeatures.length !== 2
    ) {
      return [];
    }
    const [maleFeature, femaleFeature] = interfaceFeatures as [string, string];
    const between = Array.isArray(entry.between)
      ? entry.between.filter(
          (value): value is string => typeof value === 'string',
        )
      : [];
    const malePart = featureOwners.get(maleFeature) ?? between[0];
    const femalePart = featureOwners.get(femaleFeature) ?? between[1];
    if (!malePart || !femalePart) {
      return [];
    }
    const clearances =
      typeof entry.clearances_mm === 'object' && entry.clearances_mm !== null
        ? (entry.clearances_mm as Record<string, unknown>)
        : {};
    const clearance =
      typeof clearances.width === 'number' ? clearances.width : undefined;
    return [
      {
        female: {
          ...(clearance === undefined
            ? {}
            : {
                derivedDimensionsMm: {
                  width: { from: 'male.width', offsetMm: clearance },
                },
              }),
          dimensionsMm: { width: dimensionsMm[0] + (clearance ?? 0) },
          featureId: femaleFeature,
          partId: femalePart,
        },
        id: entry.id,
        kind: entry.connection,
        male: {
          dimensionsMm: { width: dimensionsMm[0] },
          featureId: maleFeature,
          partId: malePart,
        },
      },
    ];
  });
  await writeFile(intentPath, intentJson);
  await writeFile(
    scenePath,
    JSON.stringify({
      coordinateSystem: { handedness: 'right', up: 'Z' },
      intentRef: {
        path: intentPath,
        schema: 'evidence-cad-intent/v5',
        sha256: sha256(intentJson),
      },
      interfaces: sceneInterfaces,
      nodes: features.map((feature) => ({
        featureId: feature.id,
        id: `${feature.id}-body`,
        operation: 'union',
        partId: feature.part ?? parts[0],
        recipe: { kind: 'build123dSource', parameters: {} },
        role: 'solid',
      })),
      parts: parts.map((id) => ({ id, representationMaster: 'brep' })),
      revision: 'parameter-test-r1',
      schema: 'evidence-semantic-scene/v1',
      units: 'mm',
    }),
  );
  return { intent: intentPath, scene: scenePath };
}

function modelSource(): string {
  return `import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
step = output / "model.step"
display_glb = output / "model-display.glb"
environment = (
    f"PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING')};"
    f"PYTHONUTF8={os.environ.get('PYTHONUTF8')}"
)
stl.write_text(
    f"solid {SIZE} {environment}\\nendsolid model\\n",
    encoding="utf-8",
)
step.write_text(f"ISO-10303-21 {SIZE}\\n", encoding="utf-8")
display_glb.write_bytes(b"glTF" + bytes(str(SIZE), encoding="utf-8"))
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
source = Path(__file__).resolve()
source_dir = source.parent
intent = source_dir / "model_intent.json"
profile = source_dir / "model_profile.json"
scene = source_dir / "model_scene.json"
expected_geometry = {
    "bodyCount": 1,
    "boundsMm": {"min": [0, 0, 0], "max": [1, 1, 1], "size": [1, 1, 1]},
    "valid": True,
    "volumeMm3": 1.0,
}
part_geometry = {**expected_geometry, "isVolume": True}
observed_stl = {
    **expected_geometry,
    "faceCount": 12,
    "vertexCount": 8,
    "watertight": True,
    "windingConsistent": True,
}
export_audit = {
    "artifacts": {
        "stl:model": {
            "errors": [], "expected": expected_geometry, "observed": observed_stl,
            "pass": True, "path": str(stl.resolve()), "sha256": digest(stl), "type": "stl",
        },
        "step:model": {
            "errors": [], "expected": expected_geometry, "observed": expected_geometry,
            "pass": True, "path": str(step.resolve()), "reader": "build123d-occt",
            "sha256": digest(step), "type": "step",
        },
        "glb:display": {
            "errors": [], "expectedNodes": [NAME],
            "observed": {"geometryCount": 1, "nodes": [NAME]},
            "pass": True, "path": str(display_glb.resolve()), "reader": "trimesh-gltf",
            "sha256": digest(display_glb), "type": "glb",
        },
    },
    "errors": [],
    "pass": True,
    "schema": "evidence-export-audit/v1",
}
export_audit_path = output / "model_export-audit.json"
export_audit_path.write_text(json.dumps(export_audit), encoding="utf-8")
identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
report = {
    "artifactMatrix": {
        "parts": {
            NAME: {
                "glb": "required",
                "step": "required",
                "stl": "required",
                "threeMf": "not-applicable",
            },
        },
    },
    "artifacts": {
        "stl:model": {"coordinateFrame": "part-print", "path": str(stl.resolve()), "sha256": digest(stl)},
        "step:model": {"coordinateFrame": "semantic", "path": str(step.resolve()), "sha256": digest(step)},
        "glb:display": {"coordinateFrame": "semantic", "path": str(display_glb.resolve()), "sha256": digest(display_glb)},
        "exportAudit": {"path": str(export_audit_path.resolve()), "sha256": digest(export_audit_path)},
    },
    "autoScale": False,
    "backend": "brep-part",
    "backendData": {
        "exportAudit": export_audit,
        "parameters": {
            "local-offset": {
                "affects": ["mounting-hole"],
                "default": -2.5,
                "group": "Feature",
                "group_zh": "局部特征",
                "label": "Local offset",
                "label_zh": "局部偏移",
                "maximum": 5.0,
                "minimum": -5.0,
                "step": 0.5,
                "unit": "mm",
                "value": SIZE,
            },
        },
        "printOrientation": {
            "candidates": [{"name": "as-modeled"}],
            "selected": {"name": "as-modeled"},
            "strategy": "fixture",
        },
        "semanticAssembly": {
            "boundsMm": part_geometry["boundsMm"],
            "intentSha256": digest(intent),
        },
    },
    "builtAt": datetime.now(timezone.utc).isoformat(),
    "coordinateFrames": {
        "semantic": {"scale": 1.0, "units": "mm", "up": "Z"},
        "part-print": {"partTransforms": {NAME: identity}, "scale": 1.0, "units": "mm"},
        "plate-print": {"partTransforms": {NAME: identity}, "scale": 1.0, "units": "mm"},
    },
    "events": [],
    "features": {},
    "inputs": {
        "intent": {"path": str(intent), "schema": "evidence-cad-intent/v5", "sha256": digest(intent)},
        "profile": {"path": str(profile), "schema": "evidence-bambu-printer-profile/v1", "sha256": digest(profile)},
        "scene": {"path": str(scene), "revision": "parameter-fixture-r1", "schema": "evidence-semantic-scene/v1", "sha256": digest(scene)},
        "source": {"path": str(source), "schema": "python-source/v1", "sha256": digest(source)},
    },
    "part": NAME,
    "parts": {
        NAME: {
            "print": part_geometry,
            "representationMaster": "brep",
            "semantic": part_geometry,
        },
    },
    "pass": True,
    "revision": "parameter-fixture-r1",
    "runId": str(uuid4()),
    "scale": 1.0,
    "schema": "evidence-a3d-build/v1",
    "warnings": [],
}
(output / "model_report.json").write_text(json.dumps(report), encoding="utf-8")
print(json.dumps(report))
`;
}

async function writeInitialBuild(root: string): Promise<void> {
  const sourcePath = join(root, 'model.py');
  await writeFile(sourcePath, modelSource());
  const profilePath = join(root, 'model_profile.json');
  const profilePayload = JSON.stringify({
    schema: 'evidence-bambu-printer-profile/v1',
  });
  await writeFile(
    profilePath,
    profilePayload,
  );
  const intentPath = join(root, 'model_intent.json');
  const intentPayload = JSON.stringify({
    dimensions_mm: Object.fromEntries(
      ['x', 'y', 'z'].map((axis) => [
        axis,
        { confidence: 'high', source: 'user', value: 1 },
      ]),
    ),
    printability: {
      profile: { path: profilePath, sha256: sha256(profilePayload) },
    },
    schema: 'evidence-cad-intent/v5',
  });
  await writeFile(intentPath, intentPayload);
  await writeFile(
    join(root, 'model_scene.json'),
    JSON.stringify({
      intentRef: {
        path: intentPath,
        schema: 'evidence-cad-intent/v5',
        sha256: sha256(intentPayload),
      },
      revision: 'parameter-fixture-r1',
      schema: 'evidence-semantic-scene/v1',
    }),
  );
  await execFileAsync(PYTHON, ['model.py'], {
    cwd: root,
    env: { ...process.env, AMAGINE3D_OUTPUT_DIR: root },
  });
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

    const modifiedSource = modelSource().replace(
      'label_zh="局部偏移"',
      'label_zh=42',
    );
    await writeFile(join(root, 'model.py'), modifiedSource);
    const reportPath = join(root, 'model_report.json');
    const report = JSON.parse(await readFile(reportPath, 'utf8')) as {
      inputs: { source: { sha256: string } };
    };
    report.inputs.source.sha256 = sha256(modifiedSource);
    await writeFile(reportPath, JSON.stringify(report));
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
    assert.match(
      await readFile(join(root, 'model.py'), 'utf8'),
      /\n\s+-1\.5,/u,
    );
    assert.match(await readFile(join(root, 'model.stl'), 'utf8'), /-1\.5/u);
    assert.match(await readFile(join(root, 'model.step'), 'utf8'), /-1\.5/u);
    const report = JSON.parse(
      await readFile(join(root, 'model_report.json'), 'utf8'),
    ) as {
      inputs: { source: { path: string; schema: string; sha256: string } };
    };
    assert.equal(report.inputs.source.path, await realpath(join(root, 'model.py')));
    assert.equal(report.inputs.source.schema, 'python-source/v1');
    assert.match(report.inputs.source.sha256, /^[a-f0-9]{64}$/u);

    const committedSource = await readFile(join(root, 'model.py'), 'utf8');
    const committedStl = await readFile(join(root, 'model.stl'), 'utf8');
    const committedStep = await readFile(join(root, 'model.step'), 'utf8');
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
    assert.equal(
      await readFile(join(root, 'model.py'), 'utf8'),
      committedSource,
    );
    assert.equal(await readFile(join(root, 'model.stl'), 'utf8'), committedStl);
    assert.equal(
      await readFile(join(root, 'model.step'), 'utf8'),
      committedStep,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('scopes protocol encoding without changing model build environment', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-parameter-encoding-'));
  const previousIoEncoding = process.env.PYTHONIOENCODING;
  const previousUtf8 = process.env.PYTHONUTF8;
  try {
    process.env.PYTHONIOENCODING = 'ascii';
    delete process.env.PYTHONUTF8;
    await writeInitialBuild(root);
    const [model] = await parameterModelsForWorkspace(root, PYTHON);
    assert.ok(model);
    assert.equal(model.parameters[0]?.labelZh, '局部偏移');
    assert.equal(model.parameters[0]?.groupZh, '局部特征');
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
    assert.match(
      await readFile(join(root, 'model.stl'), 'utf8'),
      /solid -1\.5 PYTHONIOENCODING=ascii;PYTHONUTF8=None/u,
    );
  } finally {
    if (previousIoEncoding === undefined) delete process.env.PYTHONIOENCODING;
    else process.env.PYTHONIOENCODING = previousIoEncoding;
    if (previousUtf8 === undefined) delete process.env.PYTHONUTF8;
    else process.env.PYTHONUTF8 = previousUtf8;
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects malformed parameter build requests', () => {
  assert.equal(
    parameterBuildRequestSchema.safeParse({ values: {} }).success,
    false,
  );
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

test('does not expose a non-editable Hybrid build as a parameter model', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-hybrid-parameters-'));
  try {
    const stlPath = join(root, 'mesh.stl');
    const threeMfPath = join(root, 'mesh.3mf');
    const displayPath = join(root, 'mesh-display.glb');
    await writeFile(stlPath, 'solid mesh\nendsolid mesh\n');
    await writeFile(threeMfPath, '3MF');
    await writeFile(displayPath, 'glTF');
    await writeFile(
      join(root, 'mesh_report.json'),
      JSON.stringify({
        artifactMatrix: {
          parts: {
            mesh: {
              glb: 'required',
              step: 'not-applicable',
              stl: 'required',
              threeMf: 'required',
            },
          },
        },
        artifacts: {
          '3mf': { path: threeMfPath },
          'glb:display': { path: displayPath },
          stl: { path: stlPath },
        },
        backend: 'hybrid-mesh',
        inputs: {},
        part: 'mesh',
        pass: true,
        schema: 'evidence-a3d-build/v1',
      }),
    );
    assert.deepEqual(await parameterModelsForWorkspace(root, PYTHON), []);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test(
  'rebuilds a real build123d model through the checked helper runtime',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-build123d-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d');
      const inputs = await writeEvidenceInputs({
        dimensionsMm: [20, 12, 6],
        features: [
          { id: 'primary-envelope', kind: 'envelope' },
          { id: 'top-pocket', kind: 'region' },
        ],
        manufacturing: { mode: 'single-part' },
        name: 'parametric_box',
        parts: ['parametric_box'],
        root,
      });
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Align, Box, Pos
from cad_helpers import export_part, observe, parameter

NAME = "parametric_box"
INTENT = ${JSON.stringify(inputs.intent)}
SCENE = ${JSON.stringify(inputs.scene)}
POCKET_DEPTH = parameter(
    "pocket-depth", 1.0,
    min_value=0.5, max_value=2.5, step=0.5,
    unit="mm", label="Pocket depth", label_zh="凹槽深度",
    group="Feature", group_zh="局部特征",
    affects=("top-pocket",),
)
body = Box(20, 12, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
pocket = Pos(0, 0, 6 - POCKET_DEPTH / 2) * Box(6, 4, POCKET_DEPTH)
body = body - pocket
observe(body, "primary-envelope", "envelope")
observe(pocket, "top-pocket", "recess")

if __name__ == "__main__":
    export_part(
        body,
        NAME,
        intent_path=INTENT,
        scene_path=SCENE,
        source_path=__file__,
    )
`;
      await writeFile(join(root, 'parametric_box.py'), source);
      await execFileAsync(VENV_PYTHON, ['parametric_box.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      assert.equal(model.primaryPreviewPath, 'parametric_box.3mf');
      assert.equal(model.displayPreviewPath, 'parametric_box-display.glb');
      assert.equal(model.parameters[0]?.labelZh, '凹槽深度');
      assert.equal(model.parameters[0]?.groupZh, '局部特征');
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'pocket-depth': 2 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'parametric_box_report.json'), 'utf8'),
      ) as {
        backendData: {
          parameters: Record<string, { group_zh?: string; label_zh?: string }>;
        };
        parts: {
          parametric_box: { semantic: { boundsMm: { size: number[] } } };
        };
      };
      assert.deepEqual(
        report.parts.parametric_box.semantic.boundsMm.size,
        [20, 12, 6],
      );
      assert.equal(
        report.backendData.parameters['pocket-depth']?.label_zh,
        '凹槽深度',
      );
      assert.equal(
        report.backendData.parameters['pocket-depth']?.group_zh,
        '局部特征',
      );
      assert.match(
        await readFile(join(root, 'parametric_box.py'), 'utf8'),
        /"pocket-depth", 2\.0/u,
      );
      const [rebuiltModel] = await parameterModelsForWorkspace(
        root,
        VENV_PYTHON,
      );
      assert.equal(rebuiltModel?.parameters[0]?.kind, 'number');
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'rejects an envelope-changing parameter rebuild without rewriting intent',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-envelope-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d');
      const inputs = await writeEvidenceInputs({
        dimensionsMm: [20, 12, 6],
        features: [{ id: 'primary-envelope', kind: 'envelope' }],
        manufacturing: { mode: 'single-part' },
        name: 'resizable_box',
        parts: ['resizable_box'],
        root,
      });
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Box
from cad_helpers import export_part, observe, parameter

NAME = "resizable_box"
INTENT = ${JSON.stringify(inputs.intent)}
SCENE = ${JSON.stringify(inputs.scene)}
WIDTH = parameter(
    "overall-width", 20.0,
    min_value=10.0, max_value=30.0, step=0.5,
    unit="mm", label="Overall width", group="Envelope",
    affects=("primary-envelope",),
)
body = Box(WIDTH, 12, 6)
observe(body, "primary-envelope", "envelope")

if __name__ == "__main__":
    export_part(
        body,
        NAME,
        intent_path=INTENT,
        scene_path=SCENE,
        source_path=__file__,
    )
`;
      const sourcePath = join(root, 'resizable_box.py');
      await writeFile(sourcePath, source);
      await execFileAsync(VENV_PYTHON, ['resizable_box.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      const retainedPaths = [
        sourcePath,
        join(root, 'resizable_box.stl'),
        join(root, 'resizable_box.step'),
        join(root, 'resizable_box-display.glb'),
        join(root, 'resizable_box_report.json'),
      ];
      const retained = await Promise.all(retainedPaths.map((path) => readFile(path)));

      await assert.rejects(
        rebuildModelWithParameters({
          pythonExecutable: VENV_PYTHON,
          request: {
            primaryPreviewPath: model.primaryPreviewPath,
            sourceHash: model.sourceHash,
            sourcePath: model.sourcePath,
            values: { 'overall-width': 24 },
          },
          workspaceRoot: root,
        }),
        /semantic envelope dimension x differs from intent/u,
      );
      for (const [index, path] of retainedPaths.entries()) {
        assert.deepEqual(
          await readFile(path),
          retained[index],
          `${path} changed after a rejected envelope rebuild`,
        );
      }
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
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d', 'color');
      const inputs = await writeEvidenceInputs({
        colorRegions: [
          { hex: '#F05A35', name: 'left' },
          { hex: '#171717', name: 'right' },
        ],
        dimensionsMm: [20, 10, 5],
        features: [
          { id: 'complete-parent', kind: 'envelope' },
          { id: 'left-region', kind: 'region' },
          { id: 'right-region', kind: 'region' },
        ],
        manufacturing: { mode: 'single-part' },
        name: 'color_bar',
        parts: ['color_bar'],
        root,
      });
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Box, Pos
from cad_helpers import export_regions, observe, parameter

NAME = "color_bar"
INTENT = ${JSON.stringify(inputs.intent)}
SCENE = ${JSON.stringify(inputs.scene)}
SPLIT_OFFSET = parameter(
    "split-offset", 0.0,
    min_value=-4.0, max_value=4.0, step=0.5,
    unit="mm", label="Color split offset", group="Regions",
    affects=("left-region", "right-region"),
)
left = Box(10 + SPLIT_OFFSET, 10, 5)
right = Pos(10, 0, 0) * Box(10 - SPLIT_OFFSET, 10, 5)
parent = left + right
observe(parent, "complete-parent", "parent")
observe(left, "left-region", "color-region")
observe(right, "right-region", "color-region")
regions = {
    "left": (left, "#F05A35"),
    "right": (right, "#171717"),
}

if __name__ == "__main__":
    export_regions(
        regions,
        NAME,
        parent=parent,
        intent_path=INTENT,
        scene_path=SCENE,
        source_path=__file__,
    )
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
          '.amagine3d-internal/color_bar/color_bar-region-left.stl',
          '.amagine3d-internal/color_bar/color_bar-region-right.stl',
          '.amagine3d-internal/color_bar/semantic/color_bar-region-left.stl',
          '.amagine3d-internal/color_bar/semantic/color_bar-region-right.stl',
          'color_bar.3mf',
          'color_bar.stl',
          'color_bar.step',
          'color_bar-display.glb',
          'color_bar_export-audit.json',
          'color_bar_material-plan.json',
        ].sort(),
      );
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'split-offset': 2 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'color_bar_report.json'), 'utf8'),
      ) as {
        features: Record<string, { bbox_mm: { size: number[] } }>;
      };
      assert.deepEqual(
        report.features['complete-parent']?.bbox_mm.size,
        [20, 10, 5],
      );
      assert.deepEqual(report.features['left-region']?.bbox_mm.size, [12, 10, 5]);
      assert.deepEqual(report.features['right-region']?.bbox_mm.size, [8, 10, 5]);
      assert.ok((await readFile(join(root, 'color_bar.3mf'))).byteLength > 0);
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'treats the proposed-color 3MF as the adjustable print root',
  { skip: !existsSync(VENV_PYTHON) },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-assembly-parameter-'));
    try {
      const skillRoot = join(PROJECT_ROOT, 'skills', 'text-a3d');
      const manufacturing = {
        interfaces: [
          {
            acceptance: 'named parts form one case',
            assembly_axis: '+Z',
            between: ['lower-shell', 'top-lid'],
            connection: 'glue-face',
            engagement_mm: 2,
            features: ['lower-shell', 'top-lid'],
            id: 'case-seam',
          },
        ],
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
      };
      const inputs = await writeEvidenceInputs({
        features: [
          { id: 'lower-shell', kind: 'interface', part: 'lower-shell' },
          { id: 'top-lid', kind: 'interface', part: 'top-lid' },
        ],
        manufacturing,
        name: 'shell_case',
        parts: ['lower-shell', 'top-lid'],
        root,
      });
      const source = `import sys
sys.path.insert(0, ${JSON.stringify(skillRoot)})
from build123d import Align, Box, Pos
from cad_helpers import export_assembly, observe, parameter

NAME = "shell_case"
INTENT = ${JSON.stringify(inputs.intent)}
SCENE = ${JSON.stringify(inputs.scene)}
LID_THICKNESS = parameter(
    "lid-thickness", 2.0,
    min_value=1.0, max_value=3.0, step=0.5,
    unit="mm", label="Lid thickness", group="Feature",
    affects=("top-lid",),
)
lower_shell = Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
top_lid = Pos(0, 0, 8 - LID_THICKNESS) * Box(
    20, 10, LID_THICKNESS,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
observe(lower_shell, "lower-shell", "part", part_name="lower-shell")
observe(top_lid, "top-lid", "part", part_name="top-lid")

if __name__ == "__main__":
    export_assembly(
        {"lower-shell": lower_shell, "top-lid": top_lid},
        NAME,
        intent_path=INTENT,
        scene_path=SCENE,
        source_path=__file__,
    )
`;
      await writeFile(join(root, 'shell_case.py'), source);
      await execFileAsync(VENV_PYTHON, ['shell_case.py'], { cwd: root });
      const [model] = await parameterModelsForWorkspace(root, VENV_PYTHON);
      assert.ok(model);
      assert.equal(model.primaryPreviewPath, 'shell_case.3mf');
      assert.equal(model.displayPreviewPath, 'shell_case-display.glb');
      assert.deepEqual(
        model.artifactPaths.slice().sort(),
        [
          '.amagine3d-internal/shell_case/plate/shell_case-lower-shell.stl',
          '.amagine3d-internal/shell_case/plate/shell_case-top-lid.stl',
          'shell_case-lower-shell.stl',
          'shell_case-lower-shell.step',
          'shell_case-top-lid.stl',
          'shell_case-top-lid.step',
          'shell_case.3mf',
          'shell_case.stl',
          'shell_case-assemble.step',
          'shell_case-display.glb',
          'shell_case_export-audit.json',
          'shell_case_material-plan.json',
        ].sort(),
      );
      await rebuildModelWithParameters({
        pythonExecutable: VENV_PYTHON,
        request: {
          primaryPreviewPath: model.primaryPreviewPath,
          sourceHash: model.sourceHash,
          sourcePath: model.sourcePath,
          values: { 'lid-thickness': 2.5 },
        },
        workspaceRoot: root,
      });
      const report = JSON.parse(
        await readFile(join(root, 'shell_case_report.json'), 'utf8'),
      ) as {
        backendData: {
          assembly: {
            shape: { bbox_mm: { size: number[] }; solid_count: number };
          };
          printPlate: { bodyCount: number; boundsMm: { size: number[] } };
        };
        parts: Record<string, { semantic: { boundsMm: { size: number[] } } }>;
      };
      assert.equal(report.backendData.assembly.shape.solid_count, 2);
      assert.deepEqual(
        report.backendData.assembly.shape.bbox_mm.size,
        [20, 10, 8],
      );
      assert.equal(report.backendData.printPlate.bodyCount, 2);
      assert.deepEqual(report.backendData.printPlate.boundsMm.size, [45, 10, 4]);
      assert.deepEqual(
        report.parts['top-lid']?.semantic.boundsMm.size,
        [20, 10, 2.5],
      );
      assert.match(
        await readFile(join(root, 'shell_case.py'), 'utf8'),
        /"lid-thickness", 2\.5/u,
      );
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  },
);
