import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { unzipSync } from 'fflate';

import { moveSessionsToTrash } from '../server/session-trash.ts';
import {
  appendSessionAssistantTurn,
  appendSessionUserMessage,
  listSessionCatalog,
  readSessionMessages,
  readSessionThreadId,
  setSessionThreadId,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../server/sessions.ts';
import { writeUnifiedBuildFixture } from './unified-build-fixture.ts';
import { fileSectionArtifacts } from '../src/lib/artifact-selection.ts';
import { createArtifactArchive } from '../server/artifact-archive.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';

test('shows only display and plate files from the current validated report', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-plate-files-'));
  try {
    const workspace = sessionWorkspaceRoot(root, SESSION_ID)!;
    await mkdir(workspace, { recursive: true });
    await writeUnifiedBuildFixture({ backend: 'brep-assembly', name: 'pair', root: workspace,
      parts: ['left', 'right'], plateParts: [['left'], ['right']] });
    const collection = await userSessionArtifacts(root, SESSION_ID);
    const visible = fileSectionArtifacts(collection!.artifacts);
    assert.deepEqual(visible.map(({ path }) => path), [
      'pair-display.glb', 'pair-plate-01.3mf', 'pair-plate-01.stl', 'pair-plate-02.3mf', 'pair-plate-02.stl',
    ]);
    assert.deepEqual(visible.map(({ plateId }) => plateId), [undefined, '01', '01', '02', '02']);
    assert.ok(visible.every(({ buildId, modelId }) => buildId && modelId === 'pair'));
    assert.ok(collection!.artifacts.some(({ path, primary }) => path === 'pair-left.stl' && !primary));
    const download = await createArtifactArchive(workspace, visible.map(({ path }) => path));
    assert.ok(download);
    assert.deepEqual(Object.keys(unzipSync(download)).sort(), visible.map(({ path }) => path).sort());
    await writeUnifiedBuildFixture({ backend: 'brep-assembly', colored: true, name: 'pair', root: workspace, parts: ['left', 'right'] });
    const rebuilt = await userSessionArtifacts(root, SESSION_ID);
    assert.deepEqual(fileSectionArtifacts(rebuilt!.artifacts).map(({ path }) => path), ['pair-display.glb', 'pair.3mf', 'pair.stl']);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('persists product messages and the Codex thread id without legacy agent history', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-session-'));
  const sessionRoot = join(root, 'sessions');
  try {
    await appendSessionUserMessage(sessionRoot, SESSION_ID, '生成一个桌面支架');
    await setSessionThreadId(sessionRoot, SESSION_ID, 'codex-thread-1');
    await appendSessionAssistantTurn(sessionRoot, SESSION_ID, {
      finishedAt: Date.now(),
      replyText: '模型已经生成。',
      startedAt: Date.now() - 2,
      steps: [
        {
          id: 'step-1',
          label: '正在执行工作区命令',
          occurredAt: Date.now() - 1,
          stage: 'command',
          status: 'completed',
        },
      ],
    });

    const catalog = await listSessionCatalog(sessionRoot);
    assert.equal(catalog.initialSessionId, SESSION_ID);
    assert.equal(catalog.sessions[0]?.title, '生成一个桌面支架');
    assert.equal(await readSessionThreadId(sessionRoot, SESSION_ID), 'codex-thread-1');
    const messages = await readSessionMessages(join(sessionRoot, `${SESSION_ID}.json`));
    assert.deepEqual(messages.map(({ role }) => role), ['user', 'assistant']);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('trashes session metadata, workspace, and isolated Codex state together', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-trash-'));
  const sessionRoot = join(root, 'state', 'sessions');
  const workspaceRoot = join(root, 'workspace');
  const workspace = sessionWorkspaceRoot(workspaceRoot, SESSION_ID)!;
  const codexState = join(dirname(sessionRoot), 'codex', SESSION_ID);
  try {
    await appendSessionUserMessage(sessionRoot, SESSION_ID, 'test');
    await mkdir(workspace, { recursive: true });
    await mkdir(codexState, { recursive: true });
    let moved: string[] = [];
    const count = await moveSessionsToTrash(
      sessionRoot,
      workspaceRoot,
      [SESSION_ID],
      async (paths) => {
        moved = paths;
      },
    );
    assert.equal(count, 1);
    assert.deepEqual(
      new Set(moved),
      new Set([join(sessionRoot, `${SESSION_ID}.json`), workspace, codexState]),
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('discovers only artifacts from the selected session and features its display GLB', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-artifacts-'));
  try {
    const selected = sessionWorkspaceRoot(root, SESSION_ID)!;
    const other = sessionWorkspaceRoot(
      root,
      '78a8b125-4c0f-49ac-a246-06bff8a4cc7e',
    )!;
    await mkdir(selected, { recursive: true });
    await mkdir(other, { recursive: true });
    await writeFile(join(other, 'other.stl'), 'solid other');
    await writeUnifiedBuildFixture({ backend: 'brep-part', name: 'part', root: selected });

    const collection = await userSessionArtifacts(root, SESSION_ID);
    assert.equal(collection?.artifacts.some(({ name }) => name === 'other.stl'), false);
    assert.equal(
      collection?.artifacts.find(({ featured }) => featured)?.path,
      'part-display.glb',
    );
    assert.deepEqual(
      collection?.artifacts
        .filter(({ primary }) => primary)
        .map(({ path }) => path)
        .sort(),
      ['part-display.glb', 'part.stl'],
    );
    assert.equal(sessionWorkspaceRoot(root, '../escape'), undefined);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
