import { strict as assert } from 'node:assert';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  BUNDLED_POMODORO_PREVIEW,
  bundledPomodoroArtifacts,
} from '../server/bundled-workspace.ts';

test('exposes the Pomodoro models as an immutable artifact collection', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-bundled-'));
  try {
    await writeFile(join(root, BUNDLED_POMODORO_PREVIEW), '3mf');
    await writeFile(join(root, 'focus-bar-logical-assembly.step'), 'step');
    await writeFile(join(root, 'housing-shell.stl'), 'stl');
    await writeFile(join(root, 'manifest.json'), '{}');

    const collection = await bundledPomodoroArtifacts(root);

    assert.equal(collection.artifactWorkspace.sessionId, 'builtin:amagine3d-pomodoro');
    assert.equal(collection.artifactWorkspace.readOnly, true);
    assert.equal(collection.artifacts.length, 3);
    assert.equal(
      collection.artifacts.find(({ featured }) => featured)?.name,
      BUNDLED_POMODORO_PREVIEW,
    );
    assert.ok(
      collection.artifacts.every(
        ({ readOnly, url }) =>
          readOnly === true && url.startsWith('/api/bundled-artifacts/file?'),
      ),
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
