export class BuildSuperseded extends Error {
  constructor() {
    super('A newer parameter build replaced this request.');
    this.name = 'BuildSuperseded';
  }
}

export type BuildQueueOptions<Input, Result> = {
  debounceMs?: number;
  execute: (input: Input, signal: AbortSignal) => Promise<Result>;
};

type Pending<Result> = {
  id: number;
  reject: (reason: unknown) => void;
  resolve: (result: Result) => void;
};

export class LatestBuildQueue<Input, Result> {
  readonly #debounceMs: number;
  readonly #execute: BuildQueueOptions<Input, Result>['execute'];
  #active: AbortController | undefined;
  #pending: Pending<Result> | undefined;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #requestId = 0;

  constructor(options: BuildQueueOptions<Input, Result>) {
    this.#debounceMs = options.debounceMs ?? 350;
    this.#execute = options.execute;
  }

  submit(input: Input): Promise<Result> {
    this.#requestId += 1;
    const id = this.#requestId;
    this.#cancelCurrent();
    return new Promise<Result>((resolve, reject) => {
      this.#pending = { id, resolve, reject };
      this.#timer = setTimeout(() => {
        this.#timer = undefined;
        const abortController = new AbortController();
        this.#active = abortController;
        void this.#execute(input, abortController.signal).then(
          (result) => {
            if (this.#pending?.id !== id || id !== this.#requestId) return;
            this.#pending = undefined;
            this.#active = undefined;
            resolve(result);
          },
          (error: unknown) => {
            if (this.#pending?.id !== id || id !== this.#requestId) return;
            this.#pending = undefined;
            this.#active = undefined;
            reject(error);
          },
        );
      }, this.#debounceMs);
    });
  }

  dispose(): void {
    this.#requestId += 1;
    this.#cancelCurrent();
  }

  #cancelCurrent(): void {
    if (this.#timer !== undefined) {
      clearTimeout(this.#timer);
      this.#timer = undefined;
    }
    this.#active?.abort();
    this.#active = undefined;
    this.#pending?.reject(new BuildSuperseded());
    this.#pending = undefined;
  }
}
