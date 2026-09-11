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
    webSearchEnabled: true,
    workspaceRoot,
    runTurn: async (request) => {
      runCalls += 1;
      assert.equal('webSearchEnabled' in request, false);
      await request.onThreadStarted?.('thread-1');
      const command =
        'a3d compile part_scene.json --marker .part.start --intent part_intent.json --source part_build.py';
      const events: RuntimeEvent[] = [
        { threadId: 'thread-1', type: 'thread.started' },
        { type: 'turn.started' },
        {
          item: {
            command,
            id: 'command-1',
            status: 'in_progress',
            type: 'command_execution',
          },
          type: 'item.started',
        },
        {
          commandId: 'command-1',
          stage: 'source',
          status: 'running',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          elapsedMs: 1_200,
          stage: 'source',
          status: 'pass',
          type: 'cad.compile.progress',
        },
        {
          item: {
            command,
            id: 'command-1',
            status: 'in_progress',
            type: 'command_execution',
          },
          type: 'item.updated',
        },
        {
          commandId: 'command-1',
          stage: 'render',
          status: 'running',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          elapsedMs: 850,
          stage: 'render',
          status: 'pass',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          stage: 'custom-check',
          status: 'running',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          elapsedMs: 900,
          stage: 'custom-check',
          status: 'pass',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          stage: 'mesh-qa:part',
          status: 'running',
          type: 'cad.compile.progress',
        },
        {
          commandId: 'command-1',
          elapsedMs: 2_500,
          stage: 'mesh-qa:part',
          status: 'fail',
          type: 'cad.compile.progress',
        },
        {
          item: {
            command,
            id: 'command-1',
            status: 'in_progress',
            type: 'command_execution',
          },
          type: 'item.updated',
        },
        {
          commandId: 'command-1',
          elapsedMs: 5_000,
          stage: 'freshness',
          status: 'timeout',
          type: 'cad.compile.progress',
        },
        {
          item: {
            command,
            exitCode: 1,
            id: 'command-1',
            status: 'failed',
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
        webSearchEnabled: false,
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
        '正在生成 CAD 几何',
        '正在渲染 CAD 预览',
        '正在执行 CAD 检查',
        '正在检查可打印网格',
        '正在检查产物新鲜度',
        '工具执行未成功，A3D 正在尝试修复',
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
        'Generating CAD geometry',
        'Rendering the CAD preview',
        'Running a CAD validation stage',
        'Checking printable meshes',
        'Checking artifact freshness',
        'The tool failed; A3D is trying to repair the issue',
        'Organizing the response',
        'Collecting generated files',
        '0 workspace files discovered',
      ],
    );
    const stepEvents = events.filter(
      (event): event is Extract<AgentEvent, { type: 'step' }> =>
        event.type === 'step',
    );
    const deltaEvents = events.filter(
      (event): event is Extract<AgentEvent, { type: 'step_delta' }> =>
        event.type === 'step_delta',
    );
    const progressFor = (label: string) => {
      const stepId = stepEvents.find(({ step }) => step.label === label)?.step.id;
      assert.ok(stepId, `Missing step: ${label}`);
      return deltaEvents
        .filter((event) => event.stepId === stepId)
        .map(({ content }) => content)
        .join('');
    };
    assert.equal(progressFor('正在生成 CAD 几何'), 'pass · 1.20 s');
    assert.equal(progressFor('正在渲染 CAD 预览'), 'pass · 850 ms');
    assert.equal(progressFor('正在执行 CAD 检查'), 'pass · 900 ms');
    assert.equal(progressFor('正在检查可打印网格'), 'fail · 2.50 s');
    assert.equal(progressFor('正在检查产物新鲜度'), 'timeout · 5.00 s');
    assert.equal(progressFor('正在组织回复'), '模型完成');
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
    webSearchEnabled: false,
    workspaceRoot: join(root, 'workspace'),
    runTurn: async (request) => {
      assert.equal('webSearchEnabled' in request, false);
      return { finalResponse: 'ok', threadId: 'thread-1' };
    },
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
        webSearchEnabled: true,
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    assert.equal(response.status, 200);
    await response.text();
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    await rm(root, { force: true, recursive: true });
  }
});

test('streams Codex failures without aborting the settled runtime', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-failure-'));
  await mkdir(join(root, 'workspace', 'sessions', SESSION_ID), {
    recursive: true,
  });
  let signalWasAborted = false;
  const runtime: CodexRuntimeLike = {
    configured: true,
    modelName: 'openai/test-model',
    runtimeReady: true,
    skillDiagnostics: [],
    skills: [],
    stateRoot: join(root, 'state'),
    webSearchEnabled: true,
    workspaceRoot: join(root, 'workspace'),
    runTurn: async (request) => {
      request.signal?.addEventListener(
        'abort',
        () => {
          signalWasAborted = true;
        },
        { once: true },
      );
      throw new Error(
        'exceeded retry limit, last status: 429 Too Many Requests',
      );
    },
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
    const events = (await response.text())
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line) as AgentEvent);

    assert.equal(response.status, 200);
    assert.equal(signalWasAborted, false);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'error');
    if (terminal?.type !== 'error') throw new Error('Expected error event.');
    assert.equal(terminal.code, 'codex_error');
    assert.equal(
      terminal.message,
      'exceeded retry limit, last status: 429 Too Many Requests',
    );
    assert.equal(typeof terminal.finishedAt, 'number');
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    await rm(root, { force: true, recursive: true });
  }
});
