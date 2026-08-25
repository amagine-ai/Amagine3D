import { strict as assert } from 'node:assert';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';

import type {
  AgentSession,
  AgentSessionEvent,
  PiRuntime,
} from '@amagine3d/a3d-runtime';
import express from 'express';

import { assistantMessageOutcome } from '../server/agent-events.ts';
import { registerChatRoute } from '../server/routes/chat.ts';
import type { AgentEvent } from '../src/types.ts';

function assistantEnd(
  stopReason: 'aborted' | 'error' | 'stop' | 'toolUse',
  errorMessage?: string,
): AgentSessionEvent {
  return {
    message: {
      api: 'openai-responses',
      content: [],
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

test('chat reports a failed message_end instead of a false done event', async () => {
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
      appendCustomEntry: (_type: string, data: { stages: { status: string }[] }) => {
        persistedStatuses.push(...data.stages.map(({ status }) => status));
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
        sessionId: '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    const events = (await response.text())
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line) as AgentEvent);

    assert.equal(response.status, 200);
    assert.equal(events.some(({ type }) => type === 'done'), false);
    assert.deepEqual(events.at(-1), {
      code: 'provider_error',
      message: 'provider request failed',
      type: 'error',
    });
    assert.ok(persistedStatuses.includes('failed'));
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if (previousApiKey === undefined) delete process.env.LLM_API_KEY;
    else process.env.LLM_API_KEY = previousApiKey;
  }
});
