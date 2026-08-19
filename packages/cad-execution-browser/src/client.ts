import {
  CadDomainError,
  cadExecutionResultSchema,
  cadWorkerRequestSchema,
  cadWorkerResponseSchema,
  deserializeCadError,
  SCHEMA_VERSION,
  type CadExecutionResult,
  type CadWorkerRequest,
  type CadWorkerResponse,
} from '@amagine3d/cad-protocol';

import { RUNTIME_LIMITS } from './runtime-manifest';
import { sha256Hex } from './hash';
import type { CadExecutorOptions, RuntimeVersions } from './types';

type TerminalResponse = Extract<
  CadWorkerResponse,
  { type: 'cancelled' | 'error' | 'ready' | 'result' }
>;

type Pending = {
  resolve: (response: TerminalResponse) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  lastActivity?: string;
  removeAbortListener: () => void;
};

function defaultRequestId(): string {
  return crypto.randomUUID();
}

export class BrowserCadExecutor {
  readonly #options: Required<
    Pick<CadExecutorOptions, 'bootstrapTimeoutMs' | 'timeoutMs'>
  > &
    Omit<CadExecutorOptions, 'bootstrapTimeoutMs' | 'timeoutMs'>;
  readonly #pending = new Map<string, Pending>();
  #worker: Worker | undefined;
  #runtimeVersions: RuntimeVersions | undefined;
  #disposed = false;

  constructor(options: CadExecutorOptions) {
    this.#options = {
      ...options,
      timeoutMs: options.timeoutMs ?? RUNTIME_LIMITS.defaultTimeoutMs,
      bootstrapTimeoutMs:
        options.bootstrapTimeoutMs ?? RUNTIME_LIMITS.bootstrapTimeoutMs,
    };
  }

  async bootstrap(signal?: AbortSignal): Promise<RuntimeVersions> {
    if (this.#runtimeVersions) return this.#runtimeVersions;
    const requestId = (this.#options.createRequestId ?? defaultRequestId)();
    const response = await this.request(
      { schemaVersion: SCHEMA_VERSION, requestId, type: 'bootstrap' },
      this.#options.bootstrapTimeoutMs,
      signal,
    );
    if (response.type !== 'ready') {
      throw new CadDomainError(
        'InvalidWorkerMessage',
        `Expected ready response, received ${response.type}.`,
        { category: 'protocol', retryable: false },
      );
    }
    this.#runtimeVersions = response.runtimeVersions;
    return response.runtimeVersions;
  }

  async execute(
    input: Extract<CadWorkerRequest, { type: 'build' }>,
    options: { signal?: AbortSignal } = {},
  ): Promise<CadExecutionResult> {
    const request = cadWorkerRequestSchema.parse(input);
    if (request.type !== 'build') {
      throw new CadDomainError(
        'InvalidWorkerMessage',
        'BrowserCadExecutor.execute only accepts build requests.',
        { category: 'protocol', retryable: false },
      );
    }
    const runtimeVersions = await this.bootstrap(options.signal);
    const response = await this.request(
      request,
      this.#options.timeoutMs,
      options.signal,
    );
    if (response.type !== 'result') {
      throw new CadDomainError(
        'InvalidWorkerMessage',
        `Expected result response, received ${response.type}.`,
        { category: 'protocol', retryable: false },
      );
    }
    for (const payload of response.artifactPayloads ?? []) {
      if (
        payload.bytes.byteLength !== payload.artifact.byteLength ||
        (await sha256Hex(payload.bytes)) !== payload.artifact.sha256
      ) {
        this.resetWorker();
        throw new CadDomainError(
          'IntegrityMismatch',
          `Worker artifact ${payload.artifact.id} failed transfer verification.`,
          { category: 'integrity', retryable: true, operation: 'transfer' },
        );
      }
    }
    if (
      response.artifactPayloads === undefined ||
      response.artifactPayloads.length !== response.artifacts.length ||
      response.buildReport === undefined
    ) {
      this.resetWorker();
      throw new CadDomainError(
        'InvalidWorkerMessage',
        'Worker result omitted its build report or transferable artifact payloads.',
        { category: 'protocol', retryable: true, operation: 'transfer' },
      );
    }
    return cadExecutionResultSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      runId: response.runId,
      qaReport: response.qaReport,
      buildReport: response.buildReport.data,
      artifacts: response.artifactPayloads,
      runtimeVersions: response.runtimeVersions ?? runtimeVersions,
      ...(response.metrics === undefined
        ? {}
        : { durationMs: response.metrics.durationMs }),
      ...(response.metrics?.wasmHeapBytes === undefined
        ? {}
        : { wasmHeapBytes: response.metrics.wasmHeapBytes }),
    });
  }

  private worker(): Worker {
    if (this.#disposed) {
      throw new CadDomainError('WorkerCrashed', 'CAD executor is disposed.', {
        category: 'execution',
        retryable: false,
      });
    }
    if (this.#worker) return this.#worker;

    const worker = this.#options.workerFactory();
    worker.addEventListener('message', this.onMessage);
    worker.addEventListener('error', this.onCrash);
    worker.addEventListener('messageerror', this.onCrash);
    this.#worker = worker;
    return worker;
  }

  private readonly onMessage = (event: MessageEvent<unknown>): void => {
    const parsed = cadWorkerResponseSchema.safeParse(event.data);
    if (!parsed.success) {
      this.crash('CAD Worker returned an invalid protocol message.');
      return;
    }
    const response = parsed.data;
    if (response.type === 'progress' || response.type === 'log') {
      const pending = this.#pending.get(response.requestId);
      if (pending !== undefined) {
        pending.lastActivity =
          response.type === 'progress'
            ? `${response.stage}: ${response.message}`
            : `${response.level}: ${response.line}`;
      }
      this.#options.onEvent?.(response);
      return;
    }
    const pending = this.#pending.get(response.requestId);
    if (!pending) return;

    this.#pending.delete(response.requestId);
    clearTimeout(pending.timer);
    pending.removeAbortListener();
    if (response.type === 'error') {
      pending.reject(deserializeCadError(response.error));
    } else if (response.type === 'cancelled') {
      pending.reject(
        new CadDomainError('Cancelled', 'CAD operation was cancelled.', {
          category: 'cancelled',
          retryable: true,
        }),
      );
    } else {
      pending.resolve(response);
    }
  };

  private readonly onCrash = (): void => {
    this.crash('CAD Worker crashed and will be rebuilt on the next request.');
  };

  private request(
    request: CadWorkerRequest,
    timeoutMs: number,
    signal?: AbortSignal,
  ): Promise<TerminalResponse> {
    if (this.#pending.size > 0) {
      return Promise.reject(
        new CadDomainError(
          'ExecutionRejected',
          'Only one CAD Worker operation may run at a time.',
          { category: 'execution', retryable: true },
        ),
      );
    }
    if (signal?.aborted) {
      return Promise.reject(
        new CadDomainError('Cancelled', 'CAD operation was cancelled.', {
          category: 'cancelled',
          retryable: true,
        }),
      );
    }

    return new Promise((resolve, reject) => {
      const abort = (): void => {
        this.#pending.delete(request.requestId);
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        this.resetWorker();
        reject(
          new CadDomainError('Cancelled', 'CAD operation was cancelled.', {
            category: 'cancelled',
            retryable: true,
          }),
        );
      };
      signal?.addEventListener('abort', abort, { once: true });
      const timer = setTimeout(() => {
        const lastActivity = this.#pending.get(request.requestId)?.lastActivity;
        this.#pending.delete(request.requestId);
        signal?.removeEventListener('abort', abort);
        this.resetWorker();
        const operationLabel =
          request.type === 'bootstrap'
            ? 'CAD runtime bootstrap'
            : 'CAD geometry build and QA';
        reject(
          new CadDomainError(
            'WorkerTimeout',
            `${operationLabel} exceeded its ${timeoutMs} ms deadline.${
              lastActivity === undefined
                ? ''
                : ` Last activity: ${lastActivity}.`
            }`,
            {
              category: 'execution',
              retryable: true,
              operation: request.type,
              details: { timeoutMs },
            },
          ),
        );
      }, timeoutMs);
      this.#pending.set(request.requestId, {
        resolve,
        reject,
        timer,
        ...(request.type === 'bootstrap'
          ? { lastActivity: 'bootstrap: Worker request started' }
          : {}),
        removeAbortListener: () => signal?.removeEventListener('abort', abort),
      });
      this.worker().postMessage(request);
    });
  }

  private crash(message: string): void {
    const error = new CadDomainError('WorkerCrashed', message, {
      category: 'execution',
      retryable: true,
    });
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.removeAbortListener();
      pending.reject(error);
    }
    this.#pending.clear();
    this.resetWorker();
  }

  private resetWorker(): void {
    this.#worker?.terminate();
    this.#worker = undefined;
    this.#runtimeVersions = undefined;
  }

  dispose(): void {
    this.#disposed = true;
    this.crash('CAD executor was disposed.');
  }
}

export function createBrowserCadExecutor(
  options: Omit<CadExecutorOptions, 'workerFactory'> & {
    workerUrl?: string | URL;
  } = {},
): BrowserCadExecutor {
  const { workerUrl = '/cad-worker.mjs', ...executorOptions } = options;
  return new BrowserCadExecutor({
    ...executorOptions,
    workerFactory: () =>
      new Worker(workerUrl, {
        type: 'module',
        name: 'amagine3d-cad-worker',
      }),
  });
}
