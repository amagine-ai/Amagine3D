import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { unzipSync } from 'fflate';

import { createArtifactArchive } from '../server/artifact-archive.ts';

test('creates a ZIP containing each selected artifact at its workspace path', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifact-archive-'));
  try {
    await mkdir(join(root, 'models'));
    await writeFile(join(root, 'part.py'), 'print("part")');
    await writeFile(join(root, 'models', 'part.stl'), 'solid part');

    const archive = await createArtifactArchive(root, [
      'part.py',
      'models/part.stl',
    ]);
    assert.ok(archive);
    const files = unzipSync(archive);
    assert.equal(new TextDecoder().decode(files['part.py']), 'print("part")');
    assert.equal(
      new TextDecoder().decode(files['models/part.stl']),
      'solid part',
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('refuses to archive a path outside the workspace', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifact-archive-safe-'));
  try {
    await writeFile(join(root, 'part.stl'), 'solid part');
    assert.equal(
      await createArtifactArchive(root, ['part.stl', '../secret.txt']),
      undefined,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
