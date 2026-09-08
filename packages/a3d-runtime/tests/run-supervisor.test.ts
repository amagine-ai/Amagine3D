import { strict as assert } from 'node:assert';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';

import {
  agentRunTimeoutsFromEnv,
  DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS,
  DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS,
  isRuntimeProgressEvent,
  MAX_TIMER_DELAY_MS,
  type RuntimeEvent,
  RunStopped,
  RunSupervisor,
} from '../src/index.ts';

test('run timeout configuration keeps the existing safety limits', () => {
  assert.deepEqual(agentRunTimeoutsFromEnv({}), {
    hardTimeoutMs: DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS,
    idleTimeoutMs: DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS,
  });
  assert.equal(DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS, 1_800_000);
  assert.equal(DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS, 7_200_000);
});

test('run timeout configuration rejects malformed values', () => {
  for (const value of [
    '',
    '-1',
    '1.5',
    '1e3',
    'Infinity',
    String(MAX_TIMER_DELAY_MS + 1),
  ]) {
    assert.throws(
      () => agentRunTimeoutsFromEnv({ AGENT_RUN_IDLE_TIMEOUT_MS: value }),
      /AGENT_RUN_IDLE_TIMEOUT_MS/u,
    );
  }
});

test('runtime lifecycle and item events count as progress', () => {
  const event = (value: RuntimeEvent) => value;
  assert.equal(
    isRuntimeProgressEvent(event({ threadId: 'x', type: 'thread.started' })),
    true,
  );
  assert.equal(
    isRuntimeProgressEvent(
      event({
        item: { id: 'a', text: 'hello', type: 'agent_message' },
        type: 'item.updated',
      }),
    ),
    true,
  );
  assert.equal(
    isRuntimeProgressEvent(event({ message: 'failed', type: 'error' })),
    false,
  );
});

test('reported runtime failures do not abort the settled Codex signal', async () => {
  const supervisor = new RunSupervisor<string>({
    hardTimeoutMs: 0,
    idleTimeoutMs: 0,
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  let runSignal: AbortSignal | undefined;

  await assert.rejects(
    supervisor.run((signal) => {
      runSignal = signal;
      return Promise.reject(new Error('provider failed'));
    }),
    /provider failed/u,
  );

  assert.equal(supervisor.fail('codex_error', 'provider failed'), true);
  assert.equal(runSignal?.aborted, false);
  await supervisor.finalize({ status: 'cancelled' }, ({ outcome }) => {
    assert.deepEqual(outcome, {
      code: 'codex_error',
      message: 'provider failed',
      status: 'failed',
    });
  });
});

test('idle timeout aborts the Codex signal and finalizes once', async () => {
  const supervisor = new RunSupervisor<string>({
    hardTimeoutMs: 500,
    idleTimeoutMs: 30,
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await assert.rejects(
    supervisor.run(
      (signal) =>
        new Promise<never>((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          });
        }),
    ),
    RunStopped,
  );
  let commits = 0;
  const commit = ({ outcome }: { outcome: { status: string; kind?: string } }) => {
    commits += 1;
    assert.equal(outcome.status, 'timed_out');
    assert.equal(outcome.kind, 'idle');
  };
  await Promise.all([
    supervisor.finalize({ status: 'cancelled' }, commit),
    supervisor.finalize({ status: 'cancelled' }, commit),
  ]);
  assert.equal(commits, 1);
});

test('progress refreshes idle timeout while hard timeout remains absolute', async () => {
  const supervisor = new RunSupervisor<string>({
    hardTimeoutMs: 75,
    idleTimeoutMs: 35,
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  const progress = setInterval(() => supervisor.observeProgress(), 15);
  try {
    await assert.rejects(
      supervisor.run(
        (signal) =>
          new Promise<never>((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(signal.reason), {
              once: true,
            });
          }),
      ),
      RunStopped,
    );
  } finally {
    clearInterval(progress);
  }
  await supervisor.finalize({ status: 'cancelled' }, ({ outcome }) => {
    assert.equal(outcome.status, 'timed_out');
    if (outcome.status === 'timed_out') assert.equal(outcome.kind, 'hard');
  });
  await delay(5);
});
