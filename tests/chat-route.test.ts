import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import type {
  CodexRuntimeLike,
  RuntimeEvent,
} from '@amagine3d/a3d-runtime';
import express from 'express';

import { registerChatRoute } from '../server/routes/chat.ts';
import {
  readSessionMessages,
  readSessionThreadId,
} from '../server/sessions.ts';
import type { AgentEvent } from '../src/types.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';

test('streams one native Codex turn without server-side repair prompts', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-route-'));
  const stateRoot = join(root, 'state');
  const workspaceRoot = join(root, 'workspace');
  await Promise.all([mkdir(stateRoot), mkdir(workspaceRoot)]);
  await mkdir(join(workspaceRoot, 'sessions', SESSION_ID), { recursive: true });
  let runCalls = 0;
  const runtime: CodexRuntimeLike = {
    configured: true,
    modelName: 'openai/test-model',
    runtimeReady: true,
    skillDiagnostics: [],
    skills: [],
    stateRoot,
    workspaceRoot,
    runTurn: async (request) => {
      runCalls += 1;
      await request.onThreadStarted?.('thread-1');
      const events: RuntimeEvent[] = [
        { threadId: 'thread-1', type: 'thread.started' },
        { type: 'turn.started' },
        {
          item: {
            command: 'a3d compile part_scene.json --marker .part.start --intent part_intent.json --source part_build.py',
            id: 'command-1',
            status: 'in_progress',
            type: 'command_execution',
          },
          type: 'item.started',
        },
        {
          item: {
            command: 'a3d compile part_scene.json --marker .part.start --intent part_intent.json --source part_build.py',
            id: 'command-1',
            status: 'in_progress',
            type: 'command_execution',
          },
          type: 'item.updated',
        },
        {
          item: {
            command: 'a3d compile part_scene.json --marker .part.start --intent part_intent.json --source part_build.py',
            exitCode: 0,
            id: 'command-1',
            status: 'completed',
            type: 'command_execution',
          },
          type: 'item.completed',
        },
        {
          item: { id: 'answer-1', text: '模', type: 'agent_message' },
          type: 'item.updated',
        },
        {
          item: { id: 'answer-1', text: '模型完成', type: 'agent_message' },
          type: 'item.completed',
        },
      ];
      for (const event of events) await request.onEvent?.(event);
      return { finalResponse: '模型完成', threadId: 'thread-1' };
    },
  };
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    python: { executable: 'python', ready: true, version: '3.13' },
    runtime,
    runtimeError: undefined,
  });
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));

  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: '创建一个支架',
        sessionId: SESSION_ID,
        taskType: 'cad',
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    const events = (await response.text())
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line) as AgentEvent);

    assert.equal(response.status, 200);
    assert.equal(runCalls, 1);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'complete');
    if (terminal?.type !== 'complete') throw new Error('Expected completion.');
    assert.equal(terminal.content, '模型完成');
    const streamedBody = JSON.stringify(events);
    assert.doesNotMatch(streamedBody, /a3d compile/u);
    assert.doesNotMatch(streamedBody, /internal compiler output/u);
    assert.deepEqual(
      events
        .filter((event): event is Extract<AgentEvent, { type: 'step' }> =>
          event.type === 'step',
        )
        .map(({ step }) => step.label),
      [
        '正在启动 A3D · openai/test-model',
        'A3D 已启动',
        'A3D 正在分析请求',
        '正在编译并检查 CAD',
        'A3D 正在分析执行结果',
        '正在组织回复',
        '正在整理生成文件',
        '已发现 0 个工作区文件',
      ],
    );
    assert.deepEqual(
      events
        .filter((event): event is Extract<AgentEvent, { type: 'step' }> =>
          event.type === 'step',
        )
        .map(({ step }) => step.localizedLabel?.en),
      [
        'Starting A3D · openai/test-model',
        'A3D started',
        'A3D is analyzing the request',
        'Compiling and validating CAD',
        'A3D is analyzing the tool result',
        'Organizing the response',
        'Collecting generated files',
        '0 workspace files discovered',
      ],
    );
    assert.equal(
      events
        .filter((event) => event.type === 'step_delta')
        .map((event) => event.content)
        .join(''),
      '模型完成',
    );
    const messages = await readSessionMessages(
      join(stateRoot, 'sessions', `${SESSION_ID}.json`),
    );
    assert.equal(
      await readSessionThreadId(join(stateRoot, 'sessions'), SESSION_ID),
      'thread-1',
    );
    assert.deepEqual(messages.map(({ role }) => role), ['user', 'assistant']);
    const assistant = messages.at(-1);
    assert.equal(assistant?.role, 'assistant');
    if (assistant?.role !== 'assistant') throw new Error('Expected assistant turn.');
    assert.equal(typeof assistant.startedAt, 'number');
    if (assistant.startedAt === undefined) throw new Error('Expected start time.');
    assert.equal(assistant.startedAt <= assistant.steps[0]!.occurredAt, true);
    assert.equal(assistant.finishedAt! >= assistant.startedAt, true);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    await rm(root, { force: true, recursive: true });
  }
});

test('does not require Python for a plain Codex chat turn', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-chat-'));
  await mkdir(join(root, 'workspace', 'sessions', SESSION_ID), {
    recursive: true,
  });
  const runtime: CodexRuntimeLike = {
    configured: true,
    modelName: 'openai/test-model',
    runtimeReady: true,
    skillDiagnostics: [],
    skills: [],
    stateRoot: join(root, 'state'),
    workspaceRoot: join(root, 'workspace'),
    runTurn: async () => ({ finalResponse: 'ok', threadId: 'thread-1' }),
  };
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    python: { executable: null, ready: false, version: null },
    runtime,
    runtimeError: undefined,
  });
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: '解释 BRep',
        sessionId: SESSION_ID,
        taskType: 'chat',
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    assert.equal(response.status, 200);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    await rm(root, { force: true, recursive: true });
  }
});
