import {
  SCHEMA_VERSION,
  type CadWorkerRequest,
  type CadWorkerResponse,
  type QaReport,
} from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import { sha256Hex } from './hash';
import type { CadRuntime, RuntimeBuildResult, RuntimeLogSink } from './types';
import { CadWorkerServer, type WorkerEndpoint } from './worker-server';

const SOURCE =
  'from build123d import Box\nfrom amagine_cad import publish_model\nbody = Box(1, 2, 3)\npublish_model(body, "box")\n';

function passedQa(runId: string): QaReport {
  return {
    schemaVersion: SCHEMA_VERSION,
    runId,
    workflowKind: 'single-color',
    status: 'passed',
    checks: [{ id: 'validity', status: 'passed', message: 'Valid shape.' }],
  };
}

class FakeEndpoint {
  readonly responses: CadWorkerResponse[] = [];
  listener: ((event: MessageEvent<unknown>) => void) | undefined;

  addEventListener(
    _type: string,
    listener: EventListenerOrEventListenerObject,
  ): void {
    this.listener = (event) => {
      if (typeof listener === 'function') listener(event);
      else listener.handleEvent(event);
    };
  }

  postMessage(message: unknown): void {
    this.responses.push(message as CadWorkerResponse);
  }
}

class FakeRuntime implements CadRuntime {
  bootstrapCount = 0;
  buildCount = 0;

  async bootstrap(log: RuntimeLogSink) {
    this.bootstrapCount += 1;
    log('info', 'runtime ready');
    return { pyodide: '314.0.3' };
  }

  async build(
    request: Extract<CadWorkerRequest, { type: 'build' }>,
    log: RuntimeLogSink,
  ): Promise<RuntimeBuildResult> {
    this.buildCount += 1;
    log('info', 'x'.repeat(10_000));
    return {
      buildReport: { valid: true },
      qaReport: passedQa(request.runId),
      artifacts: [
        {
          kind: 'stl',
          fileName: 'model.stl',
          mediaType: 'model/stl',
          bytes: new TextEncoder().encode('solid box\nendsolid box\n'),
        },
      ],
    };
  }

  dispose(): void {}
}

describe('CadWorkerServer contract', () => {
  it('validates requests and returns hashed transferable artifacts', async () => {
    const endpoint = new FakeEndpoint();
    const runtime = new FakeRuntime();
    const server = new CadWorkerServer(
      endpoint as unknown as WorkerEndpoint,
      runtime,
    );
    await server.handle({
      schemaVersion: SCHEMA_VERSION,
      requestId: 'bootstrap-1',
      type: 'bootstrap',
    });
    await server.handle({
      schemaVersion: SCHEMA_VERSION,
      requestId: 'build-1',
      type: 'build',
      runId: 'run-1',
      projectId: 'project-1',
      workflowKind: 'single-color',
      source: SOURCE,
      sourceHash: await sha256Hex(new TextEncoder().encode(SOURCE)),
      parameterOverrides: {},
    });

    const result = endpoint.responses.find(
      (response): response is Extract<CadWorkerResponse, { type: 'result' }> =>
        response.type === 'result',
    );
    expect(result?.artifactPayloads?.[0]?.artifact.sha256).toMatch(
      /^[a-f0-9]{64}$/u,
    );
    expect(result?.artifactPayloads?.[0]?.bytes.byteLength).toBeGreaterThan(0);
    expect(runtime.bootstrapCount).toBe(1);
    expect(runtime.buildCount).toBe(1);
    const truncatedLog = endpoint.responses.find(
      (response) => response.type === 'log' && response.truncated,
    );
    expect(truncatedLog?.type === 'log' ? truncatedLog.line.length : 0).toBe(
      4_000,
    );
  });

  it('rejects invalid and forbidden requests without invoking the runtime', async () => {
    const endpoint = new FakeEndpoint();
    const runtime = new FakeRuntime();
    const server = new CadWorkerServer(
      endpoint as unknown as WorkerEndpoint,
      runtime,
    );
    await server.handle({ type: 'bootstrap', requestId: 'missing-version' });
    expect(endpoint.responses.at(-1)?.type).toBe('error');

    await server.handle({
      schemaVersion: 1,
      requestId: 'bootstrap-2',
      type: 'bootstrap',
    });
    await server.handle({
      schemaVersion: 1,
      requestId: 'bad-build',
      type: 'build',
      runId: 'run-2',
      workflowKind: 'single-color',
      source: 'import socket',
      sourceHash: 'a'.repeat(64),
      parameterOverrides: {},
    });
    expect(endpoint.responses.at(-1)?.type).toBe('error');
    expect(runtime.buildCount).toBe(0);
  });

  it('reuses one bootstrapped runtime across ten deterministic rebuilds', async () => {
    const endpoint = new FakeEndpoint();
    const runtime = new FakeRuntime();
    const server = new CadWorkerServer(
      endpoint as unknown as WorkerEndpoint,
      runtime,
    );
    await server.handle({
      schemaVersion: 1,
      requestId: 'bootstrap-ten',
      type: 'bootstrap',
    });
    const sourceHash = await sha256Hex(new TextEncoder().encode(SOURCE));
    for (let index = 0; index < 10; index += 1) {
      await server.handle({
        schemaVersion: 1,
        requestId: `rebuild-${String(index)}`,
        type: 'build',
        runId: `run-${String(index)}`,
        workflowKind: 'single-color',
        source: SOURCE,
        sourceHash,
        parameterOverrides: { WIDTH: index + 1 },
      });
    }
    expect(runtime.bootstrapCount).toBe(1);
    expect(runtime.buildCount).toBe(10);
    expect(
      endpoint.responses.filter((response) => response.type === 'result'),
    ).toHaveLength(10);
  });
});
