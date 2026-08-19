import {
  SCHEMA_VERSION,
  type Artifact,
  type CadExecutionResult,
  type CadRun,
  type QaReport,
} from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import { persistSuccessfulExecution } from './execution-persistence';
import { MemoryFileStore } from './file-store';
import { encodeText, sha256 } from './hash';
import { InMemoryProjectRepository } from './in-memory-repository';

const NOW = '2026-08-13T08:00:00.000Z';
const SOURCE = 'from build123d import Box\nbody = Box(40, 30, 20)\n';

function qa(status: QaReport['status'] = 'passed'): QaReport {
  return {
    schemaVersion: SCHEMA_VERSION,
    runId: 'run-1',
    workflowKind: 'single-color',
    status,
    checks: [
      {
        id: 'watertight',
        status: status === 'passed' ? 'passed' : 'failed',
        message: 'Mesh watertight check.',
      },
    ],
  };
}

function activeRun(): CadRun {
  return {
    schemaVersion: SCHEMA_VERSION,
    id: 'run-1',
    projectId: 'project-1',
    createdAt: NOW,
    completedAt: null,
    status: 'active',
    workflowKind: 'single-color',
    workflowSelectionReason: 'Default single-color workflow.',
    sourceHash: null,
    workflowSnapshot: {
      engine: 'Amagine3D-CAD',
      revision: 'workflow-2026.08.18.2',
      profile: 'hardware-enclosure-single',
    },
    artifactIds: [],
  };
}

async function payload(
  kind: Artifact['kind'],
  fileName: string,
  content: string,
) {
  const bytes = encodeText(content);
  const buffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  return {
    artifact: {
      schemaVersion: SCHEMA_VERSION,
      id: `artifact-${kind}`,
      runId: 'run-1',
      kind,
      fileName,
      mediaType: kind.endsWith('report') ? 'application/json' : 'model/stl',
      byteLength: bytes.byteLength,
      sha256: await sha256(bytes),
      createdAt: NOW,
    },
    bytes: buffer,
  };
}

async function executionResult(report: QaReport): Promise<CadExecutionResult> {
  return {
    schemaVersion: SCHEMA_VERSION,
    runId: 'run-1',
    qaReport: report,
    buildReport: { valid: report.status === 'passed' },
    runtimeVersions: { pyodide: '314.0.3', build123d: '0.11.1' },
    artifacts: [
      await payload('model-source', 'model.py', SOURCE),
      await payload(
        'build-report',
        'build-report.json',
        `${JSON.stringify({ schemaVersion: 1, data: { valid: true } })}\n`,
      ),
      await payload(
        'qa-report',
        'qa-report.json',
        `${JSON.stringify(report)}\n`,
      ),
      await payload('step', 'model.step', 'STEP'),
      await payload(
        'stl',
        'model.stl',
        'solid enclosure\nendsolid enclosure\n',
      ),
    ],
  };
}

describe('immutable execution persistence', () => {
  it('persists the verified STEP primary artifact with the run', async () => {
    const files = new MemoryFileStore();
    const repository = new InMemoryProjectRepository({}, files);
    await repository.createProject({
      project: {
        schemaVersion: SCHEMA_VERSION,
        id: 'project-1',
        name: 'Sensor enclosure',
        createdAt: NOW,
        updatedAt: NOW,
        revision: 0,
        currentRunId: null,
      },
    });
    await repository.saveRun('project-1', {
      run: activeRun(),
      events: [],
      artifacts: [],
    });
    const result = await executionResult(qa());
    const saved = await persistSuccessfulExecution(repository, {
      projectId: 'project-1',
      run: activeRun(),
      result,
      events: [
        {
          schemaVersion: SCHEMA_VERSION,
          id: 'event-transition',
          runId: 'run-1',
          sequence: 0,
          occurredAt: NOW,
          type: 'workflow-transition',
          payload: { eventType: 'build_succeeded', from: 'building', to: 'qa' },
        },
        {
          schemaVersion: SCHEMA_VERSION,
          id: 'event-step',
          runId: 'run-1',
          sequence: 1,
          occurredAt: NOW,
          type: 'artifact',
          payload: { artifactId: 'artifact-step', action: 'verified' },
        },
        {
          schemaVersion: SCHEMA_VERSION,
          id: 'event-stl',
          runId: 'run-1',
          sequence: 2,
          occurredAt: NOW,
          type: 'artifact',
          payload: { artifactId: 'artifact-stl', action: 'verified' },
        },
      ],
      now: () => new Date(NOW),
    });

    expect(saved.status).toBe('succeeded');
    expect(saved.runtimeVersions).toEqual(result.runtimeVersions);
    const restored = await repository.getRun('project-1', 'run-1');
    const expectedArtifacts = result.artifacts;
    expect(restored?.artifacts.map(({ metadata }) => metadata.sha256)).toEqual(
      expectedArtifacts.map(({ artifact }) => artifact.sha256),
    );
    expect(restored?.run.artifactIds).toEqual(
      expectedArtifacts.map(({ artifact }) => artifact.id),
    );
    expect(
      restored?.events.map(({ id, sequence }) => ({ id, sequence })),
    ).toEqual([
      { id: 'event-transition', sequence: 0 },
      { id: 'event-step', sequence: 1 },
      { id: 'event-stl', sequence: 2 },
    ]);
    expect((await files.list()).some((path) => path.endsWith('.step'))).toBe(
      true,
    );
    expect((await repository.getProject('project-1'))?.currentRunId).toBe(
      'run-1',
    );
  });

  it('rejects a successful single-color result without STEP', async () => {
    const repository = new InMemoryProjectRepository();
    const result = await executionResult(qa());
    result.artifacts = result.artifacts.filter(
      ({ artifact }) => artifact.kind !== 'step',
    );

    await expect(
      persistSuccessfulExecution(repository, {
        projectId: 'project-1',
        run: activeRun(),
        result,
      }),
    ).rejects.toMatchObject({ code: 'InvalidExternalData' });
  });

  it('never marks a failed QA result as a successful run', async () => {
    const repository = new InMemoryProjectRepository();
    const report = qa('failed');
    await expect(
      persistSuccessfulExecution(repository, {
        projectId: 'project-1',
        run: activeRun(),
        result: await executionResult(report),
      }),
    ).rejects.toMatchObject({ code: 'QaFailed' });
  });
});
