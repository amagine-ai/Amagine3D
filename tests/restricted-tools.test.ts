import { strict as assert } from 'node:assert';
import {
  link,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  assertWritablePath,
  createRestrictedToolDefinitions,
} from '@amagine3d/a3d-runtime';

test('PI mutation tools only target the current session directory', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-tool-boundary-'));
  try {
    const sessionRoot = join(root, 'workspace', 'sessions', 'session-id');
    const outsideRoot = join(root, 'skills');
    await mkdir(sessionRoot, { recursive: true });
    await mkdir(outsideRoot, { recursive: true });

    assert.equal(
      await assertWritablePath(sessionRoot, join(sessionRoot, 'part.py')),
      join(sessionRoot, 'part.py'),
    );
    await assert.rejects(
      assertWritablePath(sessionRoot, join(outsideRoot, 'SKILL.md')),
      /current session artifact directory/u,
    );
    const definitions = createRestrictedToolDefinitions(sessionRoot);
    assert.deepEqual(
      definitions.map(({ name }) => name),
      ['edit', 'write'],
    );
    const writeTool = definitions.find(({ name }) => name === 'write') as unknown as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };
    await writeTool.execute(
      'inside-write',
      { content: 'inside', path: join(sessionRoot, 'part.py') },
      undefined,
      undefined,
      {},
    );
    assert.equal(await readFile(join(sessionRoot, 'part.py'), 'utf8'), 'inside');
    await assert.rejects(
      writeTool.execute(
        'outside-write',
        { content: 'changed', path: join(outsideRoot, 'SKILL.md') },
        undefined,
        undefined,
        {},
      ),
      /current session artifact directory/u,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('PI mutation tools reject symbolic-link and hard-link escapes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-tool-links-'));
  try {
    const sessionRoot = join(root, 'session');
    const protectedFile = join(root, 'application.ts');
    await mkdir(sessionRoot);
    await writeFile(protectedFile, 'protected');
    await symlink(protectedFile, join(sessionRoot, 'symbolic.ts'));
    await link(protectedFile, join(sessionRoot, 'hard.ts'));

    await assert.rejects(
      assertWritablePath(sessionRoot, join(sessionRoot, 'symbolic.ts')),
      /symbolic links/u,
    );
    await assert.rejects(
      assertWritablePath(sessionRoot, join(sessionRoot, 'hard.ts')),
      /hard links/u,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
