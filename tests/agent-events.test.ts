import { strict as assert } from 'node:assert';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import type {
  AgentSession,
  AgentSessionEvent,
  PiRuntime,
} from '@amagine3d/a3d-runtime';
import express from 'express';

import { assistantMessageOutcome } from '../server/agent-events.ts';
import { registerChatRoute } from '../server/routes/chat.ts';
import { CHAT_TURN_CUSTOM_TYPE } from '../src/lib/chat-turn.ts';
import type { AgentEvent, ChatTurn } from '../src/types.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';

function assistantEnd(
  stopReason: 'aborted' | 'error' | 'stop' | 'toolUse',
  errorMessage?: string,
  text = '',
): AgentSessionEvent {
  return {
    message: {
      api: 'openai-responses',
      content: text ? [{ type: 'text', text }] : [],
      errorMessage,
      model: 'test-model',
      provider: 'openai',
      role: 'assistant',
      stopReason,
      timestamp: Date.now(),
      usage: {
        cacheRead: 0,
        cacheWrite: 0,
        cost: {
          cacheRead: 0,
          cacheWrite: 0,
          input: 0,
          output: 0,
          total: 0,
        },
        input: 0,
        output: 0,
        totalTokens: 0,
      },
    },
    type: 'message_end',
  } as AgentSessionEvent;
}

function emitEvent(
  listeners: Set<(event: AgentSessionEvent) => void>,
  event: AgentSessionEvent,
) {
  for (const listener of listeners) listener(event);
}

function assistantStart(): AgentSessionEvent {
  return {
    message: { role: 'assistant' },
    type: 'message_start',
  } as AgentSessionEvent;
}

function assistantDelta(content: string): AgentSessionEvent {
  return {
    assistantMessageEvent: { delta: content, type: 'text_delta' },
    message: { role: 'assistant' },
    type: 'message_update',
  } as AgentSessionEvent;
}

test('captures provider failures from authoritative assistant message_end events', () => {
  assert.deepEqual(
    assistantMessageOutcome(
      assistantEnd('error', 'invalid_encrypted_content'),
    ),
    { status: 'error', message: 'invalid_encrypted_content' },
  );
});

test('uses a readable fallback for aborted assistant messages', () => {
  assert.deepEqual(assistantMessageOutcome(assistantEnd('aborted')), {
    status: 'error',
    message: 'Model request was aborted.',
  });
});

test('clears stale provider errors after a successful assistant message', () => {
  assert.deepEqual(assistantMessageOutcome(assistantEnd('toolUse')), {
    status: 'success',
  });
  assert.deepEqual(assistantMessageOutcome(assistantEnd('stop')), {
    status: 'success',
  });
});

test('chat reports a failed message_end instead of a false complete event', async () => {
  const listeners = new Set<(event: AgentSessionEvent) => void>();
  const messages: unknown[] = [];
  const persistedStatuses: string[] = [];
  const session = {
    abort: async () => undefined,
    dispose: () => undefined,
    messages,
    prompt: async () => {
      const event = assistantEnd('error', 'provider request failed');
      if (event.type !== 'message_end') throw new Error('Invalid test event.');
      messages.push(event.message);
      for (const listener of listeners) listener(event);
    },
    sessionManager: {
      appendCustomEntry: (_type: string, data: { steps: { status: string }[] }) => {
        persistedStatuses.push(...data.steps.map(({ status }) => status));
      },
    },
    subscribe: (listener: (event: AgentSessionEvent) => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  } as unknown as AgentSession;
  const runtime = {
    createSession: async () => session,
    modelName: 'openai/test-model',
    skills: [],
    stateRoot: '/tmp/amagine3d-test-state',
    workspaceRoot: '/tmp/amagine3d-test-workspace',
  } as unknown as PiRuntime;
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    python: { executable: 'python', ready: true, version: '3.13' },
    runtime,
    runtimeError: undefined,
  });

  const previousApiKey = process.env.LLM_API_KEY;
  process.env.LLM_API_KEY = 'test-key';
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));

  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: 'Create a test part.',
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
    assert.equal(events.some(({ type }) => type === 'complete'), false);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'error');
    if (terminal?.type !== 'error') throw new Error('Expected an error event.');
    assert.equal(terminal.code, 'provider_error');
    assert.equal(terminal.message, 'provider request failed');
    assert.equal(typeof terminal.finishedAt, 'number');
    assert.ok(persistedStatuses.includes('failed'));
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if (previousApiKey === undefined) delete process.env.LLM_API_KEY;
    else process.env.LLM_API_KEY = previousApiKey;
  }
});

test('idle timeout aborts once and cannot enter web or visual repair prompts', async () => {
  const prompts: string[] = [];
  let abortCalls = 0;
  let releasePrompt!: () => void;
  const blockedPrompt = new Promise<void>((resolve) => {
    releasePrompt = resolve;
  });
  const session = {
    abort: async () => {
      abortCalls += 1;
      releasePrompt();
    },
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => undefined,
    messages: [],
    prompt: async (prompt: string) => {
      prompts.push(prompt);
      await blockedPrompt;
    },
    sessionManager: {
      appendCustomEntry: () => undefined,
    },
    subscribe: () => () => undefined,
  } as unknown as AgentSession;
  const runtime = {
    createSession: async () => session,
    modelName: 'openai/test-model',
    skills: [],
    stateRoot: '/tmp/amagine3d-test-state',
    workspaceRoot: '/tmp/amagine3d-test-workspace',
  } as unknown as PiRuntime;
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    abortGraceMs: 100,
    firstBuildReminderMs: 0,
    python: { executable: 'python', ready: true, version: '3.13' },
    runtime,
    runtimeError: undefined,
    timeouts: { hardTimeoutMs: 500, idleTimeoutMs: 40 },
  });

  const previousApiKey = process.env.LLM_API_KEY;
  const previousTavilyKey = process.env.TAVILY_API_KEY;
  process.env.LLM_API_KEY = 'test-key';
  process.env.TAVILY_API_KEY = 'test-key';
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));

  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: 'Create a CAD model.',
        sessionId: SESSION_ID,
        taskType: 'cad',
        webSearchEnabled: true,
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    const events = (await response.text())
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line) as AgentEvent);

    assert.equal(prompts.length, 1);
    assert.equal(abortCalls, 1);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'error');
    if (terminal?.type !== 'error') throw new Error('Expected an error event.');
    assert.equal(terminal.code, 'run_timeout');
    assert.match(terminal.message, /没有模型或工具进展/u);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if (previousApiKey === undefined) delete process.env.LLM_API_KEY;
    else process.env.LLM_API_KEY = previousApiKey;
    if (previousTavilyKey === undefined) delete process.env.TAVILY_API_KEY;
    else process.env.TAVILY_API_KEY = previousTavilyKey;
  }
});

test('cad task type enforces visual validation without prompt keyword inference', async () => {
  const messages: unknown[] = [];
  const prompts: string[] = [];
  const session = {
    abort: async () => undefined,
    dispose: () => undefined,
    messages,
    prompt: async (prompt: string) => {
      prompts.push(prompt);
      const event = assistantEnd(
        'stop',
        undefined,
        'No visual tool evidence was produced.',
      );
      if (event.type !== 'message_end') throw new Error('Invalid test event.');
      messages.push(event.message);
    },
    sessionManager: {
      appendCustomEntry: () => undefined,
    },
    subscribe: () => () => undefined,
  } as unknown as AgentSession;
  const runtime = {
    createSession: async () => session,
    modelName: 'openai/test-model',
    skills: [],
    stateRoot: '/tmp/amagine3d-test-state',
    workspaceRoot: '/tmp/amagine3d-test-workspace',
  } as unknown as PiRuntime;
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    python: { executable: 'python', ready: true, version: '3.13' },
    runtime,
    runtimeError: undefined,
  });

  const previousApiKey = process.env.LLM_API_KEY;
  process.env.LLM_API_KEY = 'test-key';
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));

  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: 'hello',
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
    assert.equal(prompts.length, 4);
    assert.match(prompts[0] ?? '', /<visual_validation_required>/u);
    assert.equal(events.some(({ type }) => type === 'complete'), false);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'error');
    if (terminal?.type !== 'error') throw new Error('Expected an error event.');
    assert.equal(terminal.code, 'visual_validation_required');
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if (previousApiKey === undefined) delete process.env.LLM_API_KEY;
    else process.env.LLM_API_KEY = previousApiKey;
  }
});

test('chat persists the same interleaved turn that it streams before completing', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-chat-turn-'));
  const workspaceRoot = join(root, 'workspace');
  await mkdir(join(workspaceRoot, 'sessions', SESSION_ID), { recursive: true });

  const listeners = new Set<(event: AgentSessionEvent) => void>();
  const messages: unknown[] = [];
  let persistedCustomType: string | undefined;
  let persistedTurn: ChatTurn | undefined;
  const session = {
    abort: async () => undefined,
    dispose: () => undefined,
    messages,
    prompt: async () => {
      const interim = assistantEnd(
        'toolUse',
        undefined,
        '我会先检查模型尺寸。',
      );
      emitEvent(listeners, assistantStart());
      emitEvent(listeners, assistantDelta('我会先检查模型尺寸。'));
      if (interim.type !== 'message_end') throw new Error('Invalid event.');
      messages.push(interim.message);
      emitEvent(listeners, interim);
      emitEvent(
        listeners,
        {
          args: {},
          toolCallId: 'tool-call-1',
          toolName: 'bash',
          type: 'tool_execution_start',
        } as AgentSessionEvent,
      );

      const final = assistantEnd('stop', undefined, '模型已经生成。');
      emitEvent(listeners, assistantStart());
      emitEvent(listeners, assistantDelta('模型已经生成。'));
      if (final.type !== 'message_end') throw new Error('Invalid event.');
      messages.push(final.message);
      emitEvent(listeners, final);
    },
    sessionManager: {
      appendCustomEntry: (customType: string, data: ChatTurn) => {
        persistedCustomType = customType;
        persistedTurn = data;
      },
    },
    subscribe: (listener: (event: AgentSessionEvent) => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  } as unknown as AgentSession;
  const runtime = {
    createSession: async () => session,
    modelName: 'openai/test-model',
    skills: [],
    stateRoot: join(root, 'state'),
    workspaceRoot,
  } as unknown as PiRuntime;
  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    python: { executable: 'python', ready: true, version: '3.13' },
    runtime,
    runtimeError: undefined,
  });

  const previousApiKey = process.env.LLM_API_KEY;
  process.env.LLM_API_KEY = 'test-key';
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));

  try {
    const { port } = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${String(port)}/api/chat`, {
      body: JSON.stringify({
        message: 'Create a test part.',
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
    assert.equal(persistedCustomType, CHAT_TURN_CUSTOM_TYPE);
    assert.ok(persistedTurn);
    const terminal = events.at(-1);
    assert.equal(terminal?.type, 'complete');
    if (terminal?.type !== 'complete') {
      throw new Error('Expected a complete event.');
    }
    assert.equal(terminal.content, '模型已经生成。');
    assert.equal(persistedTurn.replyText, terminal.content);
    assert.equal(persistedTurn.finishedAt, terminal.finishedAt);

    const streamedSteps = events.flatMap((event) =>
      event.type === 'step' ? [event.step] : [],
    );
    assert.deepEqual(
      persistedTurn.steps.map(({ id }) => id),
      streamedSteps.map(({ id }) => id),
    );
    assert.equal(
      persistedTurn.steps.some(
        ({ progressText }) => progressText === '我会先检查模型尺寸。',
      ),
      true,
    );
    assert.equal(
      persistedTurn.steps.find(({ id }) => id === terminal.sourceStepId)
        ?.progressText,
      undefined,
    );
    assert.ok(
      events.findIndex(({ type }) => type === 'artifacts') <
        events.findIndex(({ type }) => type === 'complete'),
    );
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if (previousApiKey === undefined) delete process.env.LLM_API_KEY;
    else process.env.LLM_API_KEY = previousApiKey;
    await rm(root, { force: true, recursive: true });
  }
});
