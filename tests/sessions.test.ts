import { strict as assert } from 'node:assert';
import { appendFile, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { CURRENT_SESSION_VERSION } from '@amagine3d/a3d-runtime';

import { CHAT_TURN_CUSTOM_TYPE } from '../src/lib/chat-turn.ts';
import {
  listWorkspaceStorage,
  listSessionCatalog,
  readSessionMessages,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../server/sessions.ts';
import { moveSessionsToTrash } from '../server/session-trash.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';
const OTHER_SESSION_ID = '78a8b125-4c0f-49ac-a246-06bff8a4cc7e';

async function writeSession(
  sessionRoot: string,
  workspace: string,
  firstUserText = '生成一个桌面支架',
  sessionId = SESSION_ID,
): Promise<string> {
  const timestamp = '2026-08-23T08:00:00.000Z';
  const occurredAt = Date.parse(timestamp);
  const path = join(sessionRoot, `2026-08-23T08-00-00-000Z_${sessionId}.jsonl`);
  const entries = [
    {
      type: 'session',
      version: CURRENT_SESSION_VERSION,
      id: sessionId,
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
        content: [{ type: 'text', text: firstUserText }],
        timestamp: occurredAt,
      },
    },
    {
      type: 'message',
      id: 'raw-assistant-analysis',
      parentId: 'user-message',
      timestamp,
      message: {
        role: 'assistant',
        content: [{ type: 'text', text: '我会先分析尺寸。' }],
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
        stopReason: 'toolUse',
        timestamp: occurredAt + 500,
      },
    },
    {
      type: 'message',
      id: 'raw-tool-result',
      parentId: 'raw-assistant-analysis',
      timestamp,
      message: {
        role: 'toolResult',
        toolCallId: 'tool-call-1',
        toolName: 'read',
        content: [{ type: 'text', text: '读取完成' }],
        isError: false,
        timestamp: occurredAt + 1_000,
      },
    },
    {
      type: 'message',
      id: 'internal-visual-repair',
      parentId: 'raw-tool-result',
      timestamp,
      message: {
        role: 'user',
        content: [
          {
            type: 'text',
            text: '<visual_validation_repair>内部补救提示</visual_validation_repair>',
          },
        ],
        timestamp: occurredAt + 1_500,
      },
    },
    {
      type: 'message',
      id: 'raw-assistant-final',
      parentId: 'internal-visual-repair',
      timestamp,
      message: {
        role: 'assistant',
        content: [{ type: 'text', text: '这段原始最终回复不应直接显示。' }],
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
        timestamp: occurredAt + 2_000,
      },
    },
    {
      type: 'custom',
      customType: CHAT_TURN_CUSTOM_TYPE,
      data: {
        finishedAt: occurredAt + 4_000,
        replyText: '模型已经生成。',
        steps: [
          {
            id: 'step-1',
            label: '正在分析需求',
            occurredAt,
            progressText: '已识别桌面支架的主要尺寸。',
            stage: 'analysis',
            status: 'completed',
          },
          {
            id: 'step-2',
            label: '正在生成模型',
            occurredAt: occurredAt + 2_000,
            progressText: '主体已完成，正在补充连接结构。',
            stage: 'build',
            status: 'completed',
          },
        ],
      },
      id: 'chat-turn',
      parentId: 'raw-assistant-final',
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

test('lists workspace storage grouped by every session', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-workspace-storage-'));
  try {
    const sessionRoot = join(root, 'sessions');
    const workspace = join(root, 'workspace');
    const bundled = join(root, 'bundled');
    await mkdir(sessionRoot);
    await mkdir(bundled);

    const selectedRoot = sessionWorkspaceRoot(workspace, SESSION_ID)!;
    const otherRoot = sessionWorkspaceRoot(workspace, OTHER_SESSION_ID)!;
    await mkdir(selectedRoot, { recursive: true });
    await mkdir(otherRoot, { recursive: true });
    await writeFile(join(selectedRoot, 'selected.stl'), 'solid selected');
    await writeFile(join(otherRoot, 'other.py'), 'print("other")');
    await writeSession(sessionRoot, selectedRoot, '第一个模型', SESSION_ID);
    await writeSession(sessionRoot, otherRoot, '第二个模型', OTHER_SESSION_ID);

    const storage = await listWorkspaceStorage(sessionRoot, workspace, bundled);
    const userGroups = storage.groups.filter(
      ({ session }) => session.kind === 'user',
    );
    assert.deepEqual(
      new Set(userGroups.map(({ session }) => session.id)),
      new Set([SESSION_ID, OTHER_SESSION_ID]),
    );
    assert.deepEqual(
      new Map(
        userGroups.map(({ artifacts, session }) => [
          session.id,
          artifacts.map(({ name }) => name),
        ]),
      ),
      new Map([
        [SESSION_ID, ['selected.stl']],
        [OTHER_SESSION_ID, ['other.py']],
      ]),
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('moves session metadata and workspace folders to trash together', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-session-trash-'));
  try {
    const sessionRoot = join(root, 'sessions');
    const workspace = join(root, 'workspace');
    await mkdir(sessionRoot);
    const selectedRoot = sessionWorkspaceRoot(workspace, SESSION_ID)!;
    await mkdir(selectedRoot, { recursive: true });
    await writeFile(join(selectedRoot, 'selected.stl'), 'solid selected');
    const sessionPath = await writeSession(sessionRoot, selectedRoot);
    let movedPaths: string[] = [];

    const trashed = await moveSessionsToTrash(
      sessionRoot,
      workspace,
      [SESSION_ID],
      (paths) => {
        movedPaths = paths;
        return Promise.resolve();
      },
    );

    assert.equal(trashed, 1);
    assert.deepEqual(new Set(movedPaths), new Set([sessionPath, selectedRoot]));
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('restores one v2 assistant turn instead of raw assistant and tool messages', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-session-messages-'));
  try {
    const sessionRoot = join(root, 'sessions');
    await mkdir(sessionRoot);
    const path = await writeSession(
      sessionRoot,
      join(root, 'workspace'),
      `生成一个桌面支架

<web_reference_mode required="true">
Search before building.
</web_reference_mode>`,
    );
    const messages = await readSessionMessages(path);
    assert.deepEqual(messages, [
      {
        id: 'user-message',
        role: 'user',
        text: '生成一个桌面支架',
      },
      {
        finishedAt: Date.parse('2026-08-23T08:00:04.000Z'),
        id: 'chat-turn',
        replyText: '模型已经生成。',
        role: 'assistant',
        steps: [
          {
            id: 'step-1',
            label: '正在分析需求',
            occurredAt: Date.parse('2026-08-23T08:00:00.000Z'),
            progressText: '已识别桌面支架的主要尺寸。',
            stage: 'analysis',
            status: 'completed',
          },
          {
            id: 'step-2',
            label: '正在生成模型',
            occurredAt: Date.parse('2026-08-23T08:00:02.000Z'),
            progressText: '主体已完成，正在补充连接结构。',
            stage: 'build',
            status: 'completed',
          },
        ],
      },
    ]);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('removes every internal prompt suffix from visible user history', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-internal-prompts-'));
  try {
    const sessionRoot = join(root, 'sessions');
    await mkdir(sessionRoot);
    const suffixes = [
      '<uploaded_image_files>\n- /tmp/reference.png\n</uploaded_image_files>',
      '<web_reference_mode required="true">内部联网提示</web_reference_mode>',
      '<web_reference_repair>内部联网补救提示</web_reference_repair>',
      '<visual_validation_required>内部视觉验证提示</visual_validation_required>',
      '<visual_validation_repair>内部视觉补救提示</visual_validation_repair>',
    ];

    for (const suffix of suffixes) {
      const path = await writeSession(
        sessionRoot,
        join(root, 'workspace'),
        `保留这段用户输入\n\n${suffix}`,
      );
      const messages = await readSessionMessages(path);
      assert.deepEqual(messages[0], {
        id: 'user-message',
        role: 'user',
        text: '保留这段用户输入',
      });
    }

    const catalog = await listSessionCatalog(sessionRoot);
    assert.equal(catalog.sessions[0]?.title, '保留这段用户输入');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('restores a failed v2 turn and suppresses internal repair prompts', async () => {
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
        parentId: 'chat-turn',
        timestamp,
        message: {
          role: 'user',
          content: [
            {
              type: 'text',
              text: `再试一次

<visual_validation_required>
内部视觉验证提示
</visual_validation_required>`,
            },
          ],
          timestamp: Date.parse(timestamp),
        },
      },
      {
        type: 'message',
        id: 'internal-web-repair',
        parentId: 'failed-user-message',
        timestamp,
        message: {
          role: 'user',
          content: [
            {
              type: 'text',
              text: '<web_reference_repair>内部联网补救提示</web_reference_repair>',
            },
          ],
          timestamp: Date.parse(timestamp) + 500,
        },
      },
      {
        type: 'message',
        id: 'failed-assistant-message',
        parentId: 'internal-web-repair',
        timestamp,
        message: {
          role: 'assistant',
          content: [],
          errorMessage: 'provider request failed',
          stopReason: 'error',
          timestamp: Date.parse(timestamp),
        },
      },
      {
        type: 'custom',
        customType: CHAT_TURN_CUSTOM_TYPE,
        data: {
          finishedAt: Date.parse(timestamp) + 2_000,
          replyText: 'provider request failed',
          steps: [
            {
              id: 'failed-step',
              label: '执行失败',
              occurredAt: Date.parse(timestamp),
              progressText: '模型供应商请求失败。',
              stage: 'error',
              status: 'failed',
            },
          ],
        },
        id: 'failed-chat-turn',
        parentId: 'failed-assistant-message',
        timestamp,
      },
    ];
    await appendFile(
      path,
      `${entries.map((entry) => JSON.stringify(entry)).join('\n')}\n`,
    );

    const messages = await readSessionMessages(path);
    assert.deepEqual(messages.slice(-2), [
      {
        id: 'failed-user-message',
        role: 'user',
        text: '再试一次',
      },
      {
        finishedAt: Date.parse(timestamp) + 2_000,
        id: 'failed-chat-turn',
        replyText: 'provider request failed',
        role: 'assistant',
        steps: [
          {
            id: 'failed-step',
            label: '执行失败',
            occurredAt: Date.parse(timestamp),
            progressText: '模型供应商请求失败。',
            stage: 'error',
            status: 'failed',
          },
        ],
      },
    ]);
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

test('marks the build report display GLB as featured', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-featured-model-'));
  try {
    const selectedRoot = sessionWorkspaceRoot(root, SESSION_ID)!;
    await mkdir(selectedRoot, { recursive: true });
    const sourcePath = join(selectedRoot, 'part.py');
    const stlPath = join(selectedRoot, 'part.stl');
    const assembleStepPath = join(selectedRoot, 'part-assemble.step');
    const displayGlbPath = join(selectedRoot, 'part-display.glb');
    await writeFile(sourcePath, 'print("part")\n');
    await writeFile(stlPath, 'solid part\nendsolid part\n');
    await writeFile(assembleStepPath, 'assemble step');
    await writeFile(displayGlbPath, 'display glb');
    await writeFile(
      join(selectedRoot, 'part_report.json'),
      JSON.stringify({
        artifacts: {
          stl: { path: stlPath },
          'step:assemble': { path: assembleStepPath },
          'glb:display': { path: displayGlbPath },
        },
        part: 'part',
        schema: 'evidence-cad-build/v4',
        source: { path: sourcePath },
      }),
    );

    const collection = await userSessionArtifacts(root, SESSION_ID);
    assert.equal(
      collection?.artifacts.find(({ featured }) => featured)?.path,
      'part-display.glb',
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
