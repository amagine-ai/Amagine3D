import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { resolveArtifactPath, scanArtifacts } from '../server/artifacts.ts';

test('discovers supported artifacts and keeps newest files first', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifacts-'));
  try {
    await mkdir(root, { recursive: true });
    await writeFile(join(root, 'legacy.obj'), 'unsupported model');
    await writeFile(join(root, 'part.py'), 'print("source")');
    await writeFile(join(root, 'part.stl'), 'solid part\nendsolid part\n');
    await writeFile(join(root, 'part-display.glb'), 'glb');
    await writeFile(join(root, 'part-assemble.step'), 'step');
    await writeFile(join(root, '.ignored.json'), '{}');

    const artifacts = await scanArtifacts(root);
    assert.equal(artifacts.length, 4);
    assert.equal(artifacts.find(({ name }) => name === 'part.stl')?.format, 'stl');
    assert.equal(
      artifacts.find(({ name }) => name === 'part-display.glb')?.format,
      'glb',
    );
    assert.equal(
      artifacts.find(({ name }) => name === 'part-assemble.step')?.format,
      undefined,
    );
    assert.equal(artifacts.find(({ name }) => name === 'part.py')?.kind, 'source');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('artifact resolution rejects traversal outside the workspace', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifact-path-'));
  try {
    await writeFile(join(root, 'inside.stl'), 'solid part\nendsolid part\n');
    assert.equal(
      await resolveArtifactPath(root, '../outside.stl'),
      undefined,
    );
    assert.equal(
      await resolveArtifactPath(root, 'inside.stl'),
      join(await realpath(root), 'inside.stl'),
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
