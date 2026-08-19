/// <reference lib="webworker" />

import {
  CadDomainError,
  cadWorkerRequestSchema,
  cadWorkerResponseSchema,
  SCHEMA_VERSION,
  serializeCadError,
  type CadWorkerRequest,
  type CadWorkerResponse,
} from '@amagine3d/cad-protocol';

import { createArtifactPayloads } from './artifacts';
import { RUNTIME_LIMITS } from './runtime-manifest';
import { validateCadSource } from './source-policy';
import type { CadRuntime, RuntimeLogSink } from './types';

export type WorkerEndpoint = Pick<
  DedicatedWorkerGlobalScope,
  'addEventListener' | 'postMessage'
>;

function transferableBuffers(response: CadWorkerResponse): Transferable[] {
  if (response.type !== 'result') return [];
  return (response.artifactPayloads ?? []).map(({ bytes }) => bytes);
}

export class CadWorkerServer {
  readonly #cancelled = new Set<string>();
  readonly #endpoint: WorkerEndpoint;
  readonly #runtime: CadRuntime;
  #bootstrapped = false;
  #activeRequestId: string | undefined;
  #logBytes = 0;

  constructor(endpoint: WorkerEndpoint, runtime: CadRuntime) {
    this.#endpoint = endpoint;
    this.#runtime = runtime;
    endpoint.addEventListener('message', (event: MessageEvent<unknown>) => {
      void this.handle(event.data);
    });
  }

  emit(response: CadWorkerResponse): void {
    const parsed = cadWorkerResponseSchema.parse(response);
    this.#endpoint.postMessage(parsed, {
      transfer: transferableBuffers(parsed),
    });
  }

  progress(
    requestId: string,
    stage: Extract<CadWorkerResponse, { type: 'progress' }>['stage'],
    progress: number,
    message: string,
  ): void {
    this.emit({
      schemaVersion: SCHEMA_VERSION,
      requestId,
      type: 'progress',
      stage,
      progress,
      message,
    });
  }

  logger(requestId: string): RuntimeLogSink {
    return (level, line) => {
      if (this.#logBytes >= RUNTIME_LIMITS.logBytes) return;
      const encoded = new TextEncoder().encode(line);
      const remaining = Math.min(
        4_000,
        RUNTIME_LIMITS.logBytes - this.#logBytes,
      );
      const truncated = encoded.byteLength > remaining;
      const safeLine = truncated
        ? new TextDecoder().decode(encoded.slice(0, remaining))
        : line;
      this.#logBytes += Math.min(encoded.byteLength, remaining);
      this.emit({
        schemaVersion: SCHEMA_VERSION,
        requestId,
        type: 'log',
        level,
        line: safeLine,
        truncated,
      });
    };
  }

  async handle(input: unknown): Promise<void> {
    const parsed = cadWorkerRequestSchema.safeParse(input);
    if (!parsed.success) {
      const requestId =
        typeof input === 'object' &&
        input !== null &&
        'requestId' in input &&
        typeof input.requestId === 'string'
          ? input.requestId
          : 'invalid-request';
      this.emit({
        schemaVersion: SCHEMA_VERSION,
        requestId,
        type: 'error',
        error: new CadDomainError(
          'InvalidWorkerMessage',
          'Worker request failed schema validation.',
          {
            category: 'protocol',
            retryable: false,
            operation: 'parse-worker-request',
          },
        ).serialize(),
      });
      return;
    }

    const request = parsed.data;
    if (request.type === 'cancel') {
      if (this.#activeRequestId === request.targetRequestId) {
        this.#cancelled.add(request.targetRequestId);
      }
      this.emit({
        schemaVersion: SCHEMA_VERSION,
        requestId: request.targetRequestId,
        type: 'cancelled',
      });
      return;
    }

    if (this.#activeRequestId !== undefined) {
      this.emit({
        schemaVersion: SCHEMA_VERSION,
        requestId: request.requestId,
        type: 'error',
        error: new CadDomainError(
          'ExecutionRejected',
          `CAD Worker is already processing ${this.#activeRequestId}.`,
          {
            category: 'execution',
            retryable: true,
            operation: request.type,
          },
        ).serialize(),
      });
      return;
    }

    this.#activeRequestId = request.requestId;
    try {
      if (request.type === 'bootstrap') {
        await this.bootstrap(request);
      } else {
        await this.build(request);
      }
    } catch (error) {
      this.emit({
        schemaVersion: SCHEMA_VERSION,
        requestId: request.requestId,
        type: 'error',
        error: serializeCadError(error),
      });
    } finally {
      this.#activeRequestId = undefined;
    }
  }

  async bootstrap(
    request: Extract<CadWorkerRequest, { type: 'bootstrap' }>,
  ): Promise<void> {
    this.progress(request.requestId, 'bootstrap', 0, 'Loading CAD runtime');
    const versions = await this.#runtime.bootstrap(
      this.logger(request.requestId),
    );
    this.#bootstrapped = true;
    this.progress(request.requestId, 'bootstrap', 1, 'CAD runtime ready');
    this.emit({
      schemaVersion: SCHEMA_VERSION,
      requestId: request.requestId,
      type: 'ready',
      runtimeVersions: versions,
    });
  }

  async build(
    request: Extract<CadWorkerRequest, { type: 'build' }>,
  ): Promise<void> {
    if (!this.#bootstrapped) {
      throw new CadDomainError(
        'WorkerCrashed',
        'CAD runtime must be bootstrapped before a build.',
        {
          category: 'execution',
          retryable: true,
          operation: 'build',
        },
      );
    }
    if (this.#cancelled.delete(request.requestId)) return;

    validateCadSource(request.source, request.workflowKind);
    this.#logBytes = 0;
    const startedAt = performance.now();
    this.progress(request.requestId, 'build', 0.05, 'Validating CAD source');
    const result = await this.#runtime.build(
      request,
      this.logger(request.requestId),
    );
    if (this.#cancelled.delete(request.requestId)) return;

    this.progress(request.requestId, 'export', 0.75, 'Preparing artifacts');
    const artifactPayloads = await createArtifactPayloads(
      request.runId,
      result.artifacts,
      new Date().toISOString(),
    );
    this.progress(request.requestId, 'qa', 1, 'Deterministic QA complete');
    const durationMs = Math.round((performance.now() - startedAt) * 10) / 10;
    this.emit({
      schemaVersion: SCHEMA_VERSION,
      requestId: request.requestId,
      type: 'result',
      runId: request.runId,
      qaReport: result.qaReport,
      artifacts: artifactPayloads.map(({ artifact }) => artifact),
      artifactPayloads,
      buildReport: { schemaVersion: SCHEMA_VERSION, data: result.buildReport },
      metrics: {
        durationMs,
        ...(result.wasmHeapBytes === undefined
          ? {}
          : { wasmHeapBytes: result.wasmHeapBytes }),
      },
    });
  }

  dispose(): void {
    this.#runtime.dispose();
  }
}
