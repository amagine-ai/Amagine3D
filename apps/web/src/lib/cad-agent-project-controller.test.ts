import { createHash } from 'node:crypto';

import type { BrowserCadExecutor } from '@amagine3d/cad-execution-browser';
import {
  SCHEMA_VERSION,
  type Artifact,
  type CadExecutionResult,
  type CadWorkerRequest,
} from '@amagine3d/cad-protocol';
import { InMemoryProjectRepository } from '@amagine3d/cad-storage-opfs';
import { describe, expect, it } from 'vitest';

import { CadAgentProjectController } from './cad-agent-project-controller';

const SOURCE_A = `from build123d import Box
from amagine_cad import publish_model
BODY_WIDTH = 40
body = Box(BODY_WIDTH, 30, 20)
publish_model(body, "body", out_dir="cad_out")
`;
const SOURCE_B = SOURCE_A.replace('BODY_WIDTH = 40', 'BODY_WIDTH = 42');

function payload(
  runId: string,
  kind: Artifact['kind'],
  fileName: string,
  content: string,
) {
  const bytes = new TextEncoder().encode(content);
  return {
    artifact: {
      schemaVersion: SCHEMA_VERSION,
      id: `${kind}-${fileName.replaceAll('.', '-')}`,
      runId,
      kind,
      fileName,
      mediaType:
        kind === 'step'
          ? 'model/step'
          : kind === 'model-3mf'
            ? 'model/3mf'
            : kind === 'stl'
              ? 'model/stl'
              : kind === 'model-source'
                ? 'text/x-python'
                : 'application/json',
      byteLength: bytes.byteLength,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      createdAt: '2026-08-19T00:00:00.000Z',
    },
    bytes: bytes.buffer,
  };
}

function failedResult(
  runId: string,
  source: string,
  failedCheckId: string,
): CadExecutionResult {
  const qaReport = {
    schemaVersion: SCHEMA_VERSION,
    runId,
    workflowKind: 'single-color' as const,
    status: 'failed' as const,
    checks: [
      {
        id: failedCheckId,
        status: 'failed' as const,
        message: 'Deterministic fixture failure.',
      },
    ],
  };
  return {
    schemaVersion: SCHEMA_VERSION,
    runId,
    qaReport,
    buildReport: { valid: true },
    runtimeVersions: { pyodide: '314.0.3', build123d: '0.11.1' },
    artifacts: [
      payload(runId, 'model-source', 'model.py', source),
      payload(runId, 'step', 'model.step', 'STEP'),
      payload(runId, 'model-3mf', 'model.3mf', '3MF'),
      payload(runId, 'stl', 'model.stl', 'solid body\nendsolid body\n'),
      payload(
        runId,
        'build-report',
        'build-report.json',
        JSON.stringify({ valid: true }),
      ),
      payload(runId, 'qa-report', 'qa-report.json', JSON.stringify(qaReport)),
    ],
  };
}

describe('CadAgentProjectController repair state', () => {
  it('persists and restores the best source revision when QA regresses', async () => {
    const repository = new InMemoryProjectRepository();
    const results = ['watertight', 'assembly-body-overlap'];
    const executor = {
      execute: async (request: Extract<CadWorkerRequest, { type: 'build' }>) =>
        failedResult(
          request.runId,
          request.source,
          results.shift() ?? 'unexpected-failure',
        ),
      dispose: () => undefined,
    } as unknown as BrowserCadExecutor;
    const controller = await CadAgentProjectController.open({
      projectId: 'project-rollback',
      projectName: 'Rollback fixture',
      runId: 'run-rollback',
      userRequest: 'Create a plain enclosure.',
      preference: 'single-color',
      researchEnabled: false,
      visualReviewConsent: 'declined',
      requestVisualReview: async () => ({
        passed: true,
        summary: 'Not used.',
      }),
      repository,
      executor,
    });
    await controller.handleToolCall('saveDesignBrief', {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-rollback',
      workflowKind: 'single-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['plain enclosure'],
      verificationTargets: [],
      derivationNotes: [],
    });
    const firstWrite = await controller.handleToolCall('writeCadSource', {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-rollback',
      workflowKind: 'single-color',
      source: SOURCE_A,
    });
    if (firstWrite.tool !== 'writeCadSource') throw new Error('write failed');
    await controller.handleToolCall('buildAndCheck', {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-rollback',
      sourceHash: firstWrite.sourceHash,
    });

    const secondWrite = await controller.handleToolCall('writeCadSource', {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-rollback',
      workflowKind: 'single-color',
      source: SOURCE_B,
    });
    if (secondWrite.tool !== 'writeCadSource') throw new Error('write failed');
    const output = await controller.handleToolCall('buildAndCheck', {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-rollback',
      sourceHash: secondWrite.sourceHash,
    });

    expect(output).toMatchObject({
      tool: 'buildAndCheck',
      status: 'failed',
      repairContext: {
        baselineSourceHash: firstWrite.sourceHash,
        regression: true,
        rollbackApplied: true,
        affectedConstraintIds: ['assembly-body-overlap'],
        sourceDelta: { addedLineCount: 1, removedLineCount: 1 },
      },
    });
    const workspace = await controller.workspaceSnapshot();
    expect(workspace.source).toBe(SOURCE_A);
    expect(workspace.sourceHash).toBe(firstWrite.sourceHash);
    expect(workspace.revisions.at(-1)).toMatchObject({
      sourceHash: firstWrite.sourceHash,
      reason: 'automatic-rollback',
      repairContext: { rollbackApplied: true },
    });
    expect(
      (await repository.getRun('project-rollback', 'run-rollback'))?.run
        .sourceHash,
    ).toBe(firstWrite.sourceHash);
    expect(controller.viewerModel()?.parts).toMatchObject([
      { format: '3mf', name: 'model.3mf' },
    ]);
  });
});
