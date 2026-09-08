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
  hardTimeoutMs: number;
  idleTimeoutMs: number;
  timeoutMessages: Record<RunTimeoutKind, string>;
}

export class RunStopped extends Error {
  constructor() {
    super('The A3D run is stopping.');
    this.name = 'RunStopped';
  }
}

export class RunSupervisor<T> {
  private readonly controller = new AbortController();
  private deliver = true;
  private finalizePromise: Promise<void> | undefined;
  private hardTimer: NodeJS.Timeout | undefined;
  private idleTimer: NodeJS.Timeout | undefined;
  private outcomeValue: RunOutcome<T> | undefined;
  private phase: 'finalized' | 'finishing' | 'running' = 'running';

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
    if (this.idleTimer !== undefined) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(
      () => this.timeout('idle'),
      this.options.idleTimeoutMs,
    );
  }

  observeProgress(): boolean {
    if (!this.running) return false;
    this.touch();
    return true;
  }

  async run<V>(start: (signal: AbortSignal) => Promise<V>): Promise<V> {
    this.assertRunning();
    try {
      const value = await start(this.signal);
      this.assertRunning();
      return value;
    } catch (error) {
      if (!this.running) throw new RunStopped();
      throw error;
    }
  }

  finalize(
    fallback: RunOutcome<T>,
    commit: (finalization: RunFinalization<T>) => Promise<void> | void,
  ): Promise<void> {
    this.finalizePromise ??= this.finalizeOnce(fallback, commit);
    return this.finalizePromise;
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

  private async finalizeOnce(
    fallback: RunOutcome<T>,
    commit: (finalization: RunFinalization<T>) => Promise<void> | void,
  ): Promise<void> {
    this.request(fallback);
    try {
      await commit({ deliver: this.deliver, outcome: this.outcomeValue! });
    } finally {
      this.phase = 'finalized';
    }
  }

  private request(outcome: RunOutcome<T>): boolean {
    if (!this.running) return false;
    this.phase = 'finishing';
    this.outcomeValue = outcome;
    this.clearTimers();
    // A reported failure has already settled the runtime. Aborting afterward can
    // race with Codex SDK child-process cleanup and emit an unhandled ABORT_ERR.
    if (
      outcome.status === 'cancelled' ||
      outcome.status === 'timed_out'
    ) {
      this.controller.abort(outcome);
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
