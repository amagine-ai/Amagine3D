import type {
  AgentSession,
  AgentSessionEvent,
} from '@amagine3d/a3d-runtime';

import { errorMessage } from './http-utils.ts';
import { quarantineSessionActivity } from './session-activity.ts';

export type RunTimeoutKind = 'hard' | 'idle';

export type RunOutcome<T> =
  | { status: 'cancelled' }
  | { code: string; message: string; status: 'failed' }
  | { status: 'completed'; value: T }
  | {
      code: 'run_timeout';
      kind: RunTimeoutKind;
      message: string;
      status: 'timed_out';
    };

export interface RunFinalization<T> {
  deliver: boolean;
  outcome: RunOutcome<T>;
}

interface RunSupervisorOptions {
  abortGraceMs: number;
  hardTimeoutMs: number;
  idleTimeoutMs: number;
  sessionId: string;
  timeoutMessages: Record<RunTimeoutKind, string>;
}

type ManagedSession = Pick<
  AgentSession,
  'abort' | 'abortBash' | 'abortCompaction' | 'dispose'
>;

type AbortSettlement = 'fulfilled' | 'rejected' | 'timed_out';

export class RunStopped extends Error {
  constructor() {
    super('The agent run is stopping.');
    this.name = 'RunStopped';
  }
}

async function settleAbort(
  operation: Promise<void>,
  timeoutMs: number,
): Promise<AbortSettlement> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<AbortSettlement>((resolve) => {
    timer = setTimeout(() => resolve('timed_out'), timeoutMs);
  });
  try {
    return await Promise.race([
      operation.then<AbortSettlement, AbortSettlement>(
        () => 'fulfilled',
        () => 'rejected',
      ),
      timeout,
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function releaseAfterSettlementOrDeadline(
  operation: Promise<unknown>,
  release: () => void,
  timeoutMs: number,
): void {
  let timer: NodeJS.Timeout | undefined;
  const releaseOnce = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    release();
  };
  if (timeoutMs <= 0) {
    releaseOnce();
    return;
  }
  timer = setTimeout(releaseOnce, timeoutMs);
  timer.unref?.();
  void operation.then(releaseOnce, releaseOnce);
}

/**
 * Supervises one request. Idle time is refreshed only by explicit progress;
 * every active tool keeps its own last-progress deadline, so output from one
 * parallel tool cannot hide another silent child process.
 */
export class RunSupervisor<T, S extends ManagedSession = AgentSession> {
  private readonly activeToolProgress = new Map<string, number>();
  private abortPromise: Promise<void> | undefined;
  private readonly controller = new AbortController();
  private deliver = true;
  private disposed = false;
  private finalizePromise: Promise<void> | undefined;
  private hardTimer: NodeJS.Timeout | undefined;
  private idleTimer: NodeJS.Timeout | undefined;
  private lastActivityAt = Date.now();
  private outcomeValue: RunOutcome<T> | undefined;
  private phase: 'finalized' | 'finishing' | 'running' = 'running';
  private releaseCreationQuarantine: (() => void) | undefined;
  private session: S | undefined;
  private sessionCreationPromise: Promise<S> | undefined;
  private sessionCreationStarted = false;

  constructor(private readonly options: RunSupervisorOptions) {
    this.touch();
    if (options.hardTimeoutMs > 0) {
      this.hardTimer = setTimeout(
        () => this.timeout('hard'),
        options.hardTimeoutMs,
      );
    }
  }

  get running(): boolean {
    return this.phase === 'running';
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  complete(value: T): boolean {
    return this.request({ status: 'completed', value });
  }

  disconnect(): void {
    this.deliver = false;
    if (this.running) this.request({ status: 'cancelled' });
  }

  fail(code: string, message: string): boolean {
    return this.request({ code, message, status: 'failed' });
  }

  touch(): void {
    if (!this.running || this.options.idleTimeoutMs === 0) return;
    this.lastActivityAt = Date.now();
    this.scheduleIdleTimer();
  }

  observe(event: AgentSessionEvent): boolean {
    if (!this.running || !isRunProgressEvent(event)) return false;
    const observedAt = Date.now();
    if (event.type === 'tool_execution_start') {
      this.activeToolProgress.set(event.toolCallId, observedAt);
    } else if (event.type === 'tool_execution_update') {
      if (this.activeToolProgress.has(event.toolCallId)) {
        this.activeToolProgress.set(event.toolCallId, observedAt);
      }
    } else if (event.type === 'tool_execution_end') {
      this.activeToolProgress.delete(event.toolCallId);
    }
    this.lastActivityAt = observedAt;
    this.scheduleIdleTimer();
    return true;
  }

  private scheduleIdleTimer(): void {
    if (!this.running || this.options.idleTimeoutMs === 0) return;
    if (this.idleTimer !== undefined) clearTimeout(this.idleTimer);
    let deadline = this.lastActivityAt + this.options.idleTimeoutMs;
    for (const lastProgressAt of this.activeToolProgress.values()) {
      deadline = Math.min(
        deadline,
        lastProgressAt + this.options.idleTimeoutMs,
      );
    }
    this.idleTimer = setTimeout(
      () => {
        if (!this.running) return;
        const currentTime = Date.now();
        let currentDeadline =
          this.lastActivityAt + this.options.idleTimeoutMs;
        for (const lastProgressAt of this.activeToolProgress.values()) {
          currentDeadline = Math.min(
            currentDeadline,
            lastProgressAt + this.options.idleTimeoutMs,
          );
        }
        if (currentTime < currentDeadline) {
          this.scheduleIdleTimer();
          return;
        }
        this.timeout('idle');
      },
      Math.max(0, deadline - Date.now()),
    );
  }

  async run<V>(start: (signal: AbortSignal) => Promise<V>): Promise<V> {
    this.assertRunning();
    const operation = start(this.signal);
    let onStop: (() => void) | undefined;
    const stopped = new Promise<never>((_resolve, reject) => {
      onStop = () => reject(new RunStopped());
      this.signal.addEventListener('abort', onStop, { once: true });
      if (this.signal.aborted) onStop();
    });
    try {
      const value = await Promise.race([operation, stopped]);
      this.assertRunning();
      return value;
    } finally {
      if (onStop) this.signal.removeEventListener('abort', onStop);
    }
  }

  async createSession(start: () => Promise<S>): Promise<S> {
    this.assertRunning();
    if (this.sessionCreationStarted) {
      throw new Error('RunSupervisor can create only one agent session.');
    }
    this.sessionCreationStarted = true;
    this.releaseCreationQuarantine = quarantineSessionActivity(
      this.options.sessionId,
    );
    let creation: Promise<S>;
    try {
      creation = start();
    } catch (error) {
      this.takeCreationQuarantine()?.();
      throw error;
    }
    this.sessionCreationPromise = creation;

    const trackedCreation = creation.then(
      (session) => {
        this.session = session;
        if (this.running) {
          this.takeCreationQuarantine()?.();
          return session;
        }

        const abort = this.abortSession();
        this.disposeSession();
        const release = this.takeCreationQuarantine();
        if (release) {
          releaseAfterSettlementOrDeadline(
            abort,
            release,
            this.options.abortGraceMs,
          );
        }
        throw new RunStopped();
      },
      (error: unknown) => {
        this.takeCreationQuarantine()?.();
        throw error;
      },
    );
    return this.run(() => trackedCreation);
  }

  finalize(
    fallback: RunOutcome<T>,
    commit: (finalization: RunFinalization<T>) => Promise<void> | void,
  ): Promise<void> {
    this.finalizePromise ??= this.finalizeOnce(fallback, commit);
    return this.finalizePromise;
  }

  private abortSession(): Promise<void> {
    if (!this.session) return Promise.resolve();
    if (this.abortPromise) return this.abortPromise;
    const session = this.session;
    this.abortPromise = Promise.resolve().then(async () => {
      try {
        session.abortCompaction();
      } catch {
        // Agent abort remains authoritative.
      }
      try {
        session.abortBash();
      } catch {
        // Agent abort remains authoritative.
      }
      await session.abort();
    });
    return this.abortPromise;
  }

  private assertRunning(): void {
    if (!this.running) throw new RunStopped();
  }

  private clearTimers(): void {
    if (this.idleTimer !== undefined) clearTimeout(this.idleTimer);
    if (this.hardTimer !== undefined) clearTimeout(this.hardTimer);
    this.idleTimer = undefined;
    this.hardTimer = undefined;
  }

  private disposeSession(): void {
    if (this.disposed || !this.session) return;
    this.disposed = true;
    try {
      this.session.dispose();
    } catch (error) {
      console.error(`Could not dispose agent session: ${errorMessage(error)}`);
    }
  }

  private takeCreationQuarantine(): (() => void) | undefined {
    const release = this.releaseCreationQuarantine;
    this.releaseCreationQuarantine = undefined;
    return release;
  }

  private async finalizeOnce(
    fallback: RunOutcome<T>,
    commit: (finalization: RunFinalization<T>) => Promise<void> | void,
  ): Promise<void> {
    this.request(fallback);
    const outcome = this.outcomeValue!;
    try {
      if (
        (outcome.status === 'cancelled' || outcome.status === 'timed_out') &&
        this.session
      ) {
        const abort = this.abortSession();
        const settlement = await settleAbort(abort, this.options.abortGraceMs);
        if (settlement !== 'fulfilled') {
          this.disposeSession();
          if (settlement === 'timed_out') {
            const releaseShutdownQuarantine = quarantineSessionActivity(
              this.options.sessionId,
            );
            releaseAfterSettlementOrDeadline(
              abort,
              releaseShutdownQuarantine,
              this.options.abortGraceMs,
            );
          }
        }
      }
      await commit({ deliver: this.deliver, outcome });
    } finally {
      const releaseCreationQuarantine = this.takeCreationQuarantine();
      if (releaseCreationQuarantine) {
        releaseAfterSettlementOrDeadline(
          this.sessionCreationPromise ?? Promise.resolve(),
          releaseCreationQuarantine,
          this.options.abortGraceMs,
        );
      }
      this.disposeSession();
      this.phase = 'finalized';
    }
  }

  private request(outcome: RunOutcome<T>): boolean {
    if (!this.running) return false;
    this.phase = 'finishing';
    this.outcomeValue = outcome;
    this.clearTimers();
    this.controller.abort(outcome);
    if (outcome.status === 'cancelled' || outcome.status === 'timed_out') {
      void this.abortSession().catch(() => undefined);
    }
    return true;
  }

  private timeout(kind: RunTimeoutKind): void {
    this.request({
      code: 'run_timeout',
      kind,
      message: this.options.timeoutMessages[kind],
      status: 'timed_out',
    });
  }
}

export function isRunProgressEvent(event: AgentSessionEvent): boolean {
  if (
    event.type === 'tool_execution_start' ||
    event.type === 'tool_execution_end'
  ) {
    return true;
  }
  if (event.type === 'tool_execution_update') {
    return toolUpdateHasOutput(event.partialResult);
  }
  if (event.type === 'bash_execution_update') {
    return event.delta.length > 0;
  }
  if (event.type !== 'message_update') return false;
  const update = event.assistantMessageEvent;
  return (
    (update.type === 'text_delta' ||
      update.type === 'thinking_delta' ||
      update.type === 'toolcall_delta') &&
    update.delta.length > 0
  );
}

function toolUpdateHasOutput(value: unknown): boolean {
  if (typeof value === 'string') return value.length > 0;
  if (Array.isArray(value)) return value.some(toolUpdateHasOutput);
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  if ('content' in record) return toolUpdateHasOutput(record.content);
  if ('delta' in record) return toolUpdateHasOutput(record.delta);
  return Object.keys(record).length > 0;
}
