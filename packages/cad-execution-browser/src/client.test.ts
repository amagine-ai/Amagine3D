import {
  SCHEMA_VERSION,
  type CadWorkerRequest,
  type CadWorkerResponse,
} from '@amagine3d/cad-protocol';
import { describe, expect, it, vi } from 'vitest';

import { BrowserCadExecutor, createBrowserCadExecutor } from './client';

type WorkerBehavior = (worker: FakeWorker, request: CadWorkerRequest) => void;

class FakeWorker extends EventTarget {
  terminated = false;
  readonly behavior: WorkerBehavior;

  constructor(behavior: WorkerBehavior) {
    super();
    this.behavior = behavior;
  }

  postMessage(message: unknown): void {
    this.behavior(this, message as CadWorkerRequest);
  }

  respond(response: CadWorkerResponse): void {
    queueMicrotask(() =>
      this.dispatchEvent(new MessageEvent('message', { data: response })),
    );
  }

  terminate(): void {
    this.terminated = true;
  }
}

function ready(requestId: string): CadWorkerResponse {
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId,
    type: 'ready',
    runtimeVersions: { pyodide: '314.0.3' },
  };
}

function result(
  requestId: string,
  runId: string,
): Extract<CadWorkerResponse, { type: 'result' }> {
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId,
    type: 'result',
    runId,
    qaReport: {
      schemaVersion: SCHEMA_VERSION,
      runId,
      workflowKind: 'single-color',
      status: 'passed',
      checks: [],
    },
    artifacts: [],
    artifactPayloads: [],
    buildReport: { schemaVersion: SCHEMA_VERSION, data: { valid: true } },
  };
}

function buildRequest(requestId = 'build-1') {
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId,
    type: 'build' as const,
    runId: 'run-1',
    projectId: 'project-1',
    workflowKind: 'single-color' as const,
    source:
      'from build123d import Box\nfrom amagine_cad import publish_model\npublish_model(Box(1, 1, 1), "box")',
    sourceHash: 'a'.repeat(64),
    parameterOverrides: {},
  };
}

describe('BrowserCadExecutor lifecycle', () => {
  it('creates a static module Worker outside the application bundler', async () => {
    let workerUrl: string | URL | undefined;
    let workerOptions: WorkerOptions | undefined;
    class StaticModuleWorker extends FakeWorker {
      constructor(url: string | URL, options?: WorkerOptions) {
        super((target, request) => {
          target.respond(
            request.type === 'bootstrap'
              ? ready(request.requestId)
              : result(
                  request.requestId,
                  request.type === 'build' ? request.runId : '',
                ),
          );
        });
        workerUrl = url;
        workerOptions = options;
      }
    }
    vi.stubGlobal('Worker', StaticModuleWorker);
    try {
      const executor = createBrowserCadExecutor({
        workerUrl: '/test-cad-worker.mjs',
        createRequestId: () => 'static-worker-bootstrap',
      });
      await executor.execute(buildRequest('static-worker-build'));
      expect(workerUrl).toBe('/test-cad-worker.mjs');
      expect(workerOptions).toMatchObject({
        type: 'module',
        name: 'amagine3d-cad-worker',
      });
      executor.dispose();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('bootstraps once and resolves a versioned build result', async () => {
    const worker = new FakeWorker((target, request) => {
      target.respond(
        request.type === 'bootstrap'
          ? ready(request.requestId)
          : result(
              request.requestId,
              request.type === 'build' ? request.runId : '',
            ),
      );
    });
    const executor = new BrowserCadExecutor({
      workerFactory: () => worker as unknown as Worker,
      createRequestId: () => 'bootstrap-1',
    });
    const execution = await executor.execute(buildRequest());
    expect(execution.runId).toBe('run-1');
    expect(execution.runtimeVersions).toEqual({ pyodide: '314.0.3' });
    executor.dispose();
  });

  it('terminates a timed-out Worker and recreates it for the next build', async () => {
    const workers: FakeWorker[] = [];
    const executor = new BrowserCadExecutor({
      timeoutMs: 5,
      workerFactory: () => {
        const generation = workers.length;
        const worker = new FakeWorker((target, request) => {
          if (request.type === 'bootstrap')
            target.respond(ready(request.requestId));
          else if (generation > 0 && request.type === 'build') {
            target.respond(result(request.requestId, request.runId));
          }
        });
        workers.push(worker);
        return worker as unknown as Worker;
      },
      createRequestId: () => `bootstrap-${String(workers.length + 1)}`,
    });

    await expect(
      executor.execute(buildRequest('build-timeout')),
    ).rejects.toMatchObject({
      code: 'WorkerTimeout',
      message: 'CAD geometry build and QA exceeded its 5 ms deadline.',
      operation: 'build',
      details: { timeoutMs: 5 },
    });
    expect(workers[0]?.terminated).toBe(true);
    await expect(
      executor.execute(buildRequest('build-retry')),
    ).resolves.toMatchObject({ runId: 'run-1' });
    expect(workers).toHaveLength(2);
    executor.dispose();
  });

  it('identifies a cold runtime bootstrap timeout separately from geometry', async () => {
    const worker = new FakeWorker(() => undefined);
    const executor = new BrowserCadExecutor({
      bootstrapTimeoutMs: 5,
      workerFactory: () => worker as unknown as Worker,
      createRequestId: () => 'bootstrap-timeout',
    });

    await expect(executor.execute(buildRequest())).rejects.toMatchObject({
      code: 'WorkerTimeout',
      message:
        'CAD runtime bootstrap exceeded its 5 ms deadline. Last activity: bootstrap: Worker request started.',
      operation: 'bootstrap',
      details: { timeoutMs: 5 },
    });
    expect(worker.terminated).toBe(true);
    executor.dispose();
  });

  it('rebuilds the Worker after an explicit crash event', async () => {
    const workers: FakeWorker[] = [];
    const executor = new BrowserCadExecutor({
      workerFactory: () => {
        const generation = workers.length;
        const worker = new FakeWorker((target, request) => {
          if (request.type === 'bootstrap') {
            target.respond(ready(request.requestId));
          } else if (generation === 0) {
            queueMicrotask(() => target.dispatchEvent(new Event('error')));
          } else if (request.type === 'build') {
            target.respond(result(request.requestId, request.runId));
          }
        });
        workers.push(worker);
        return worker as unknown as Worker;
      },
      createRequestId: () => `bootstrap-crash-${String(workers.length)}`,
    });
    await expect(
      executor.execute(buildRequest('build-crash')),
    ).rejects.toMatchObject({ code: 'WorkerCrashed' });
    await expect(
      executor.execute(buildRequest('build-after-crash')),
    ).resolves.toMatchObject({ runId: 'run-1' });
    expect(workers).toHaveLength(2);
    executor.dispose();
  });

  it('cancels by terminating the running Worker', async () => {
    const workers: FakeWorker[] = [];
    let notifyBuildStarted: (() => void) | undefined;
    const buildStarted = new Promise<void>((resolve) => {
      notifyBuildStarted = resolve;
    });
    const executor = new BrowserCadExecutor({
      workerFactory: () => {
        const worker = new FakeWorker((target, request) => {
          if (request.type === 'bootstrap')
            target.respond(ready(request.requestId));
          else notifyBuildStarted?.();
        });
        workers.push(worker);
        return worker as unknown as Worker;
      },
      createRequestId: () => 'bootstrap-cancel',
    });
    const controller = new AbortController();
    const promise = executor.execute(buildRequest('build-cancel'), {
      signal: controller.signal,
    });
    await buildStarted;
    controller.abort();
    await expect(promise).rejects.toMatchObject({ code: 'Cancelled' });
    expect(workers[0]?.terminated).toBe(true);
    executor.dispose();
  });

  it('rejects a transferred artifact whose bytes do not match metadata', async () => {
    const worker = new FakeWorker((target, request) => {
      if (request.type === 'bootstrap') {
        target.respond(ready(request.requestId));
        return;
      }
      if (request.type !== 'build') return;
      const bytes = new TextEncoder().encode('tampered').buffer;
      target.respond({
        ...result(request.requestId, request.runId),
        type: 'result',
        artifacts: [
          {
            schemaVersion: 1,
            id: 'artifact-1',
            runId: request.runId,
            kind: 'stl',
            fileName: 'model.stl',
            mediaType: 'model/stl',
            byteLength: bytes.byteLength,
            sha256: 'a'.repeat(64),
            createdAt: '2026-08-13T08:00:00.000Z',
          },
        ],
        artifactPayloads: [
          {
            artifact: {
              schemaVersion: 1,
              id: 'artifact-1',
              runId: request.runId,
              kind: 'stl',
              fileName: 'model.stl',
              mediaType: 'model/stl',
              byteLength: bytes.byteLength,
              sha256: 'a'.repeat(64),
              createdAt: '2026-08-13T08:00:00.000Z',
            },
            bytes,
          },
        ],
      });
    });
    const executor = new BrowserCadExecutor({
      workerFactory: () => worker as unknown as Worker,
      createRequestId: () => 'bootstrap-integrity',
    });
    await expect(
      executor.execute(buildRequest('build-integrity')),
    ).rejects.toMatchObject({ code: 'IntegrityMismatch' });
    expect(worker.terminated).toBe(true);
    executor.dispose();
  });
});
