import { strict as assert } from 'node:assert';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { scanArtifacts } from '../server/artifacts.ts';
import { discoverModelBuilds } from '../server/model-builds.ts';

async function writeAssemblyBuild(
  root: string,
  name: string,
  colored: boolean,
): Promise<void> {
  const source = join(root, `${name}.py`);
  const stl = join(root, `${name}.stl`);
  const display = join(root, `${name}-display.glb`);
  const threeMf = join(root, `${name}.3mf`);
  await writeFile(source, '# assembly source\n');
  await writeFile(stl, `solid ${name}\nendsolid ${name}\n`);
  await writeFile(display, 'glTF');
  if (colored) await writeFile(threeMf, '3MF');
  await writeFile(
    join(root, `${name}_report.json`),
    JSON.stringify({
      artifacts: {
        stl: { path: stl },
        'glb:display': { path: display },
        ...(colored ? { '3mf': { path: threeMf } } : {}),
      },
      part: name,
      schema: 'evidence-cad-assembly-build/v3',
      source: { path: source },
    }),
  );
}

test('multipart assembly uses its optional colored 3MF as the print root', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-assembly-builds-'));
  try {
    await writeAssemblyBuild(root, 'plain-case', false);
    await writeAssemblyBuild(root, 'color-case', true);
    const builds = await discoverModelBuilds(root, await scanArtifacts(root));
    const byId = new Map(builds.map((build) => [build.modelId, build]));
    assert.equal(byId.get('plain-case')?.primaryPreviewPath, 'plain-case.stl');
    assert.equal(byId.get('color-case')?.primaryPreviewPath, 'color-case.3mf');
    assert.equal(
      byId.get('color-case')?.displayPreviewPath,
      'color-case-display.glb',
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
