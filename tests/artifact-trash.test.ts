import { strict as assert } from 'node:assert';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { moveArtifactsToTrash } from '../server/artifact-trash.ts';

test('resolves every selected artifact before moving files to trash', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifact-trash-'));
  try {
    await writeFile(join(root, 'part.py'), 'print("part")');
    await writeFile(join(root, 'part.stl'), 'solid part');
    let movedPaths: string[] = [];
    const moved = await moveArtifactsToTrash(
      root,
      ['part.py', 'part.stl'],
      (paths) => {
        movedPaths = paths;
        return Promise.resolve();
      },
    );
    assert.equal(moved, 2);
    assert.deepEqual(
      movedPaths.map((path) => path.split('/').at(-1)),
      ['part.py', 'part.stl'],
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('does not move any files when one selected path is invalid', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-artifact-trash-safe-'));
  try {
    await writeFile(join(root, 'part.stl'), 'solid part');
    let called = false;
    const moved = await moveArtifactsToTrash(
      root,
      ['part.stl', '../secret.txt'],
      () => {
        called = true;
        return Promise.resolve();
      },
    );
    assert.equal(moved, undefined);
    assert.equal(called, false);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
