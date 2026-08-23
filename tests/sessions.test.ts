import { strict as assert } from 'node:assert';
import { appendFile, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { CURRENT_SESSION_VERSION } from '@amagine3d/a3d-runtime';

import {
  listSessionCatalog,
  readSessionMessages,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../server/sessions.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';

async function writeSession(sessionRoot: string, workspace: string): Promise<string> {
  const timestamp = '2026-08-23T08:00:00.000Z';
  const path = join(sessionRoot, `2026-08-23T08-00-00-000Z_${SESSION_ID}.jsonl`);
  const entries = [
    {
      type: 'session',
      version: CURRENT_SESSION_VERSION,
      id: SESSION_ID,
      timestamp,
      cwd: workspace,
    },
    {
      type: 'message',
      id: 'user-message',
      parentId: null,
      timestamp,
      message: {
        role: 'user',
        content: [{ type: 'text', text: '生成一个桌面支架' }],
        timestamp: Date.parse(timestamp),
      },
    },
    {
      type: 'message',
      id: 'assistant-message',
      parentId: 'user-message',
      timestamp,
      message: {
        role: 'assistant',
        content: [{ type: 'text', text: '模型已经生成。' }],
        api: 'openai-responses',
        provider: 'openai',
        model: 'test',
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: 'stop',
        timestamp: Date.parse(timestamp),
      },
    },
    {
      type: 'custom',
      customType: 'amagine3d.run-stages.v1',
      data: {
        stages: [
          {
            id: 'stage-1',
            label: '模型已经生成',
            occurredAt: Date.parse(timestamp),
            stage: 'files',
            status: 'completed',
          },
        ],
      },
      id: 'run-stages',
      parentId: 'assistant-message',
      timestamp,
    },
  ];
  await writeFile(path, `${entries.map((entry) => JSON.stringify(entry)).join('\n')}\n`);
  return path;
}

test('uses the virtual built-in session only when no user session exists', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-sessions-'));
  try {
    const sessionRoot = join(root, 'sessions');
    const workspace = join(root, 'workspace');
    await mkdir(sessionRoot);
    await mkdir(workspace);

    const emptyCatalog = await listSessionCatalog(sessionRoot);
    assert.equal(emptyCatalog.initialSessionId, 'builtin:amagine3d-pomodoro');
    assert.deepEqual(
      emptyCatalog.sessions.map(({ kind }) => kind),
      ['builtin'],
    );

    await writeSession(sessionRoot, sessionWorkspaceRoot(workspace, SESSION_ID)!);
    const catalog = await listSessionCatalog(sessionRoot);
    assert.equal(catalog.initialSessionId, SESSION_ID);
    assert.deepEqual(
      catalog.sessions.map(({ kind }) => kind),
      ['user', 'builtin'],
    );
    assert.equal(catalog.sessions[0]?.title, '生成一个桌面支架');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('restores display messages from a persisted PI session', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-session-messages-'));
  try {
    const sessionRoot = join(root, 'sessions');
    await mkdir(sessionRoot);
    const path = await writeSession(sessionRoot, join(root, 'workspace'));
    const messages = await readSessionMessages(path);
    assert.deepEqual(
      messages.map(({ role, text }) => ({ role, text })),
      [
        { role: 'user', text: '生成一个桌面支架' },
        { role: 'assistant', text: '模型已经生成。' },
      ],
    );
    assert.deepEqual(messages[1]?.stages?.map(({ label }) => label), [
      '模型已经生成',
    ]);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('restores a failed run trace even when PI produced no assistant text', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-failed-run-'));
  try {
    const sessionRoot = join(root, 'sessions');
    await mkdir(sessionRoot);
    const path = await writeSession(sessionRoot, join(root, 'workspace'));
    const timestamp = '2026-08-23T08:01:00.000Z';
    const entries = [
      {
        type: 'message',
        id: 'failed-user-message',
        parentId: 'run-stages',
        timestamp,
        message: {
          role: 'user',
          content: [{ type: 'text', text: '再试一次' }],
          timestamp: Date.parse(timestamp),
        },
      },
      {
        type: 'custom',
        customType: 'amagine3d.run-stages.v1',
        data: {
          stages: [
            {
              id: 'failed-stage',
              label: '执行失败',
              occurredAt: Date.parse(timestamp),
              stage: 'error',
              status: 'failed',
            },
          ],
        },
        id: 'failed-run-stages',
        parentId: 'failed-user-message',
        timestamp,
      },
    ];
    await appendFile(
      path,
      `${entries.map((entry) => JSON.stringify(entry)).join('\n')}\n`,
    );

    const messages = await readSessionMessages(path);
    assert.equal(messages.at(-1)?.role, 'assistant');
    assert.equal(messages.at(-1)?.text, '');
    assert.equal(messages.at(-1)?.stages?.[0]?.status, 'failed');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('discovers artifacts only inside the selected session workspace', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-session-artifacts-'));
  try {
    const selectedRoot = sessionWorkspaceRoot(root, SESSION_ID)!;
    const otherRoot = sessionWorkspaceRoot(
      root,
      '78a8b125-4c0f-49ac-a246-06bff8a4cc7e',
    )!;
    await mkdir(selectedRoot, { recursive: true });
    await mkdir(otherRoot, { recursive: true });
    await writeFile(join(selectedRoot, 'selected.stl'), 'solid selected');
    await writeFile(join(otherRoot, 'other.stl'), 'solid other');

    const collection = await userSessionArtifacts(root, SESSION_ID);
    assert.deepEqual(
      collection?.artifacts.map(({ name }) => name),
      ['selected.stl'],
    );
    assert.equal(collection?.artifactWorkspace.sessionId, SESSION_ID);
    assert.match(
      collection?.artifacts[0]?.url ?? '',
      new RegExp(`/api/sessions/${SESSION_ID}/artifacts/file\\?`, 'u'),
    );
    assert.equal(sessionWorkspaceRoot(root, '../escape'), undefined);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
