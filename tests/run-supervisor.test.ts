import { strict as assert } from 'node:assert';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';

import type { AgentSessionEvent } from '@amagine3d/a3d-runtime';

import {
  FirstBuildReminder,
  isBuildExecutionStart,
} from '../server/first-build-reminder.ts';
import {
  agentRunTimeoutsFromEnv,
  DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS,
  DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS,
  MAX_TIMER_DELAY_MS,
} from '../server/run-config.ts';
import {
  isRunProgressEvent,
  RunStopped,
  RunSupervisor,
} from '../server/run-supervisor.ts';
import { acquireSessionActivity } from '../server/session-activity.ts';

test('run timeout configuration uses idle 30 minutes and hard 2 hours', () => {
  assert.deepEqual(agentRunTimeoutsFromEnv({}), {
    hardTimeoutMs: DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS,
    idleTimeoutMs: DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS,
  });
  assert.equal(DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS, 1_800_000);
  assert.equal(DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS, 7_200_000);
});

test('run timeout configuration rejects malformed or overflowing values', () => {
  for (const value of [
    '',
    '-1',
    '1.5',
    '1e3',
    'Infinity',
    String(MAX_TIMER_DELAY_MS + 1),
  ]) {
    assert.throws(
      () =>
        agentRunTimeoutsFromEnv({
          AGENT_RUN_IDLE_TIMEOUT_MS: value,
        }),
      /AGENT_RUN_IDLE_TIMEOUT_MS/u,
    );
  }
});

test('only substantive model and tool events refresh idle activity', () => {
  const event = (value: unknown) => value as AgentSessionEvent;
  assert.equal(
    isRunProgressEvent(
      event({
        assistantMessageEvent: { delta: 'reasoning', type: 'thinking_delta' },
        message: { role: 'assistant' },
        type: 'message_update',
      }),
    ),
    true,
  );
  assert.equal(
    isRunProgressEvent(
      event({
        assistantMessageEvent: { delta: '', type: 'text_delta' },
        message: { role: 'assistant' },
        type: 'message_update',
      }),
    ),
    false,
  );
  assert.equal(
    isRunProgressEvent(
      event({
        args: {},
        toolCallId: 'tool-1',
        toolName: 'bash',
        type: 'tool_execution_start',
      }),
    ),
    true,
  );
  assert.equal(
    isRunProgressEvent(
      event({
        args: {},
        partialResult: { content: [{ text: 'progress', type: 'text' }] },
        toolCallId: 'tool-1',
        toolName: 'bash',
        type: 'tool_execution_update',
      }),
    ),
    true,
  );
  assert.equal(
    isRunProgressEvent(event({ message: {}, type: 'message_start' })),
    false,
  );
});

test('a started tool that stays silent hits idle timeout and finalizes once', async () => {
  let abortCalls = 0;
  let disposeCalls = 0;
  let commits = 0;
  const session = {
    abort: async () => {
      abortCalls += 1;
    },
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => {
      disposeCalls += 1;
    },
  };
  const supervisor = new RunSupervisor<string, typeof session>({
    abortGraceMs: 100,
    hardTimeoutMs: 500,
    idleTimeoutMs: 50,
    sessionId: 'idle-timeout-test',
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await supervisor.createSession(async () => session);
  const toolStart = {
    args: { command: 'python slow_build.py' },
    toolCallId: 'silent-tool',
    toolName: 'bash',
    type: 'tool_execution_start',
  } as AgentSessionEvent;
  assert.equal(isRunProgressEvent(toolStart), true);
  supervisor.observe(toolStart);

  await assert.rejects(
    supervisor.run(() => new Promise<never>(() => undefined)),
    RunStopped,
  );
  const commit = async (finalization: {
    outcome: { status: string; kind?: string };
  }) => {
    commits += 1;
    assert.equal(finalization.outcome.status, 'timed_out');
    assert.equal(finalization.outcome.kind, 'idle');
  };
  await Promise.all([
    supervisor.finalize({ status: 'cancelled' }, commit),
    supervisor.finalize({ status: 'cancelled' }, commit),
  ]);

  assert.equal(abortCalls, 1);
  assert.equal(disposeCalls, 1);
  assert.equal(commits, 1);
});

test('a real tool output update refreshes the idle deadline', async () => {
  const session = {
    abort: async () => undefined,
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => undefined,
  };
  const supervisor = new RunSupervisor<string, typeof session>({
    abortGraceMs: 100,
    hardTimeoutMs: 500,
    idleTimeoutMs: 60,
    sessionId: 'tool-update-test',
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await supervisor.createSession(async () => session);
  supervisor.observe({
    args: {},
    toolCallId: 'tool-update',
    toolName: 'bash',
    type: 'tool_execution_start',
  } as AgentSessionEvent);
  const operation = supervisor.run(() => new Promise<never>(() => undefined));
  await delay(35);
  const update = {
    args: {},
    partialResult: { content: [{ text: 'still building', type: 'text' }] },
    toolCallId: 'tool-update',
    toolName: 'bash',
    type: 'tool_execution_update',
  } as AgentSessionEvent;
  assert.equal(isRunProgressEvent(update), true);
  supervisor.observe(update);
  await delay(35);
  assert.equal(supervisor.running, true);
  await assert.rejects(operation, RunStopped);
  await supervisor.finalize({ status: 'cancelled' }, ({ outcome }) => {
    assert.equal(outcome.status, 'timed_out');
    if (outcome.status === 'timed_out') assert.equal(outcome.kind, 'idle');
  });
});

test('one noisy parallel tool cannot mask another silent tool', async () => {
  const session = {
    abort: async () => undefined,
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => undefined,
  };
  const supervisor = new RunSupervisor<string, typeof session>({
    abortGraceMs: 100,
    hardTimeoutMs: 500,
    idleTimeoutMs: 55,
    sessionId: 'parallel-tool-idle-test',
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await supervisor.createSession(async () => session);
  for (const toolCallId of ['noisy-tool', 'silent-tool']) {
    supervisor.observe({
      args: {},
      toolCallId,
      toolName: 'read',
      type: 'tool_execution_start',
    } as AgentSessionEvent);
  }
  const operation = supervisor.run(() => new Promise<never>(() => undefined));
  const noise = setInterval(() => {
    supervisor.observe({
      args: {},
      partialResult: {
        content: [{ text: 'still active', type: 'text' }],
      },
      toolCallId: 'noisy-tool',
      toolName: 'read',
      type: 'tool_execution_update',
    } as AgentSessionEvent);
  }, 15);
  try {
    await assert.rejects(operation, RunStopped);
  } finally {
    clearInterval(noise);
  }
  await supervisor.finalize({ status: 'cancelled' }, ({ outcome }) => {
    assert.equal(outcome.status, 'timed_out');
    if (outcome.status === 'timed_out') assert.equal(outcome.kind, 'idle');
  });
});

test('hard timeout wins even while progress keeps refreshing idle time', async () => {
  const session = {
    abort: async () => undefined,
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => undefined,
  };
  const supervisor = new RunSupervisor<string, typeof session>({
    abortGraceMs: 100,
    hardTimeoutMs: 80,
    idleTimeoutMs: 50,
    sessionId: 'hard-timeout-test',
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await supervisor.createSession(async () => session);
  const progress = setInterval(() => supervisor.touch(), 15);
  try {
    await assert.rejects(
      supervisor.run(() => new Promise<never>(() => undefined)),
      RunStopped,
    );
  } finally {
    clearInterval(progress);
  }
  await supervisor.finalize({ status: 'cancelled' }, ({ outcome }) => {
    assert.equal(outcome.status, 'timed_out');
    if (outcome.status === 'timed_out') assert.equal(outcome.kind, 'hard');
  });
});

test('a rejected abort never leaves the session quarantined', async () => {
  const sessionId = 'abort-rejection-release-test';
  const session = {
    abort: async () => {
      throw new Error('abort failed');
    },
    abortBash: () => undefined,
    abortCompaction: () => undefined,
    dispose: () => undefined,
  };
  const supervisor = new RunSupervisor<string, typeof session>({
    abortGraceMs: 10,
    hardTimeoutMs: 500,
    idleTimeoutMs: 500,
    sessionId,
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await supervisor.createSession(async () => session);
  supervisor.disconnect();
  await supervisor.finalize({ status: 'cancelled' }, () => undefined);

  const release = acquireSessionActivity(sessionId);
  assert.ok(release);
  release();
});

test('a session creation that never settles has a bounded quarantine', async () => {
  const sessionId = 'creation-quarantine-deadline-test';
  const supervisor = new RunSupervisor<string>({
    abortGraceMs: 15,
    hardTimeoutMs: 500,
    idleTimeoutMs: 20,
    sessionId,
    timeoutMessages: { hard: 'hard', idle: 'idle' },
  });
  await assert.rejects(
    supervisor.createSession(() => new Promise<never>(() => undefined)),
    RunStopped,
  );
  await supervisor.finalize({ status: 'cancelled' }, () => undefined);
  assert.equal(acquireSessionActivity(sessionId), undefined);
  await delay(30);
  const release = acquireSessionActivity(sessionId);
  assert.ok(release);
  release();
});

test('first-build reminder fires once and a compiler invocation suppresses it', async () => {
  let reminders = 0;
  const reminder = new FirstBuildReminder(15, () => {
    reminders += 1;
  });
  await delay(40);
  reminder.finish();
  assert.equal(reminders, 1);

  const suppressed = new FirstBuildReminder(15, () => {
    reminders += 1;
  });
  const compileEvent = {
    args: {
      intent: 'intent.json',
      marker: '.generation-start',
      output_dir: '.',
      scene: 'scene.json',
      source: 'build.py',
    },
    toolCallId: 'compile-1',
    toolName: 'cad_compile',
    type: 'tool_execution_start',
  } as AgentSessionEvent;
  assert.equal(isBuildExecutionStart(compileEvent), true);
  suppressed.observe(compileEvent);
  suppressed.observe({
    args: {},
    isError: true,
    result: {
      details: {
        artifacts: {},
        issues: [{ code: 'QA.MESH_FAILED' }],
        pass: false,
        runId: '11111111-1111-4111-8111-111111111111',
        schema: 'evidence-cad-compile-result/v1',
        status: 'failed',
      },
    },
    toolCallId: 'compile-1',
    toolName: 'cad_compile',
    type: 'tool_execution_end',
  } as AgentSessionEvent);
  await delay(40);
  suppressed.finish();
  assert.equal(reminders, 1);
});

test('an immediate cad_compile infrastructure failure restores the remaining reminder budget', async () => {
  let reminders = 0;
  const reminder = new FirstBuildReminder(30, () => {
    reminders += 1;
  });
  const started = {
    args: {},
    toolCallId: 'invalid-compile',
    toolName: 'cad_compile',
    type: 'tool_execution_start',
  } as AgentSessionEvent;
  reminder.observe(started);
  await delay(5);
  reminder.observe({
    args: {},
    isError: true,
    result: { content: [{ text: 'invalid arguments', type: 'text' }] },
    toolCallId: 'invalid-compile',
    toolName: 'cad_compile',
    type: 'tool_execution_end',
  } as AgentSessionEvent);
  await delay(40);
  reminder.finish();
  assert.equal(reminders, 1);
});
