import { fileURLToPath } from 'node:url';

import {
  type CadExecutionResult,
  type DesignBrief,
} from '@amagine3d/cad-protocol';
import { MockLanguageModelV3 } from 'ai/test';
import { describe, expect, it } from 'vitest';

import { CadRunCoordinator } from '../run-coordinator';
import { coordinatorPhase, createCadAgent } from './cad-agent';
import { loadVerifiedWorkflowInstructions } from './workflow-instructions';

const promptRoot = fileURLToPath(new URL('../../prompt/', import.meta.url));

const usage = {
  inputTokens: {
    total: 10,
    noCache: 10,
    cacheRead: 0,
    cacheWrite: 0,
  },
  outputTokens: { total: 10, text: 10, reasoning: 0 },
};

function toolResponse(toolName: string, input: unknown, index: number) {
  return {
    content: [
      {
        type: 'tool-call' as const,
        toolCallId: `call-${String(index)}`,
        toolName,
        input: JSON.stringify(input),
      },
    ],
    finishReason: { unified: 'tool-calls' as const, raw: undefined },
    usage,
    warnings: [],
  };
}

const finalResponse = {
  content: [{ type: 'text' as const, text: 'The verified run is complete.' }],
  finishReason: { unified: 'stop' as const, raw: undefined },
  usage,
  warnings: [],
};

function passedSingleResult(runId: string): CadExecutionResult {
  const artifactKinds = [
    'model-source',
    'build-report',
    'qa-report',
    'step',
    'stl',
  ] as const;
  return {
    schemaVersion: 1,
    runId,
    qaReport: {
      schemaVersion: 1,
      runId,
      workflowKind: 'single-color',
      status: 'passed',
      checks: [{ id: 'overall', status: 'passed', message: 'Passed.' }],
    },
    buildReport: { valid: true },
    artifacts: artifactKinds.map((kind, index) => {
      const bytes = new TextEncoder().encode(kind).buffer;
      return {
        artifact: {
          schemaVersion: 1,
          id: `${runId}-${kind}`,
          runId,
          kind,
          fileName: `${kind}.${String(index)}`,
          mediaType: 'application/octet-stream',
          byteLength: bytes.byteLength,
          sha256: 'a'.repeat(64),
          createdAt: '2026-08-14T08:00:00.000Z',
        },
        bytes,
      };
    }),
    runtimeVersions: { build123d: '0.11.1' },
  };
}

function passedMultiResult(runId: string): CadExecutionResult {
  const artifactKinds = [
    'model-source',
    'color-plan',
    'build-report',
    'qa-report',
    'model-3mf',
    'region-stl',
    'region-stl',
  ] as const;
  return {
    schemaVersion: 1,
    runId,
    qaReport: {
      schemaVersion: 1,
      runId,
      workflowKind: 'multi-color',
      status: 'passed',
      checks: [{ id: 'overall', status: 'passed', message: 'Passed.' }],
      regionReports: [
        {
          regionId: 'body',
          componentCount: 1,
          watertight: true,
          checks: [{ id: 'body-mesh', status: 'passed', message: 'Passed.' }],
        },
        {
          regionId: 'logo',
          componentCount: 1,
          watertight: true,
          checks: [{ id: 'logo-mesh', status: 'passed', message: 'Passed.' }],
        },
      ],
      overlapCheck: {
        id: 'overlap',
        status: 'passed',
        message: 'No overlap.',
      },
      threeMfReadbackCheck: {
        id: '3mf-readback',
        status: 'passed',
        message: 'Readback passed.',
      },
    },
    buildReport: { valid: true },
    artifacts: artifactKinds.map((kind, index) => {
      const bytes = new TextEncoder().encode(`${kind}-${String(index)}`).buffer;
      return {
        artifact: {
          schemaVersion: 1,
          id: `${runId}-${kind}-${String(index)}`,
          runId,
          kind,
          fileName: `${kind}-${String(index)}`,
          mediaType: 'application/octet-stream',
          byteLength: bytes.byteLength,
          sha256: 'a'.repeat(64),
          createdAt: '2026-08-14T08:00:00.000Z',
          ...(kind === 'region-stl'
            ? { regionName: index === 5 ? 'body' : 'logo' }
            : {}),
        },
        bytes,
      };
    }),
    runtimeVersions: { build123d: '0.11.1', lib3mf: '2.5.0' },
  };
}

describe('CadAgent ToolLoopAgent integration', () => {
  it('runs a fixed fake-model script through the browser-tool contract', async () => {
    const runId = 'fake-model-run';
    const sourceHash = 'b'.repeat(64);
    const source = `from build123d import *
from amagine_cad import publish_model
body = Box(10, 10, 10)
publish_model(body, "box", out_dir="cad_out")
`;
    const designBrief: DesignBrief = {
      schemaVersion: 1,
      runId,
      workflowKind: 'single-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['enclosure'],
      verificationTargets: [],
      derivationNotes: [],
    };
    const executionResult = passedSingleResult(runId);
    const artifactIds = executionResult.artifacts.map(
      ({ artifact }) => artifact.id,
    );
    const coordinator = new CadRunCoordinator({
      runId,
      userRequest: 'Design a printable hardware enclosure.',
      preference: 'auto',
      researchEnabled: false,
      visualReviewConsent: 'declined',
    });
    const model = new MockLanguageModelV3({
      doGenerate: [
        toolResponse('saveDesignBrief', designBrief, 1),
        toolResponse(
          'writeCadSource',
          {
            schemaVersion: 1,
            runId,
            workflowKind: 'single-color',
            source,
          },
          2,
        ),
        toolResponse(
          'buildAndCheck',
          { schemaVersion: 1, runId, sourceHash },
          3,
        ),
        toolResponse(
          'finishCadRun',
          { schemaVersion: 1, runId, artifactIds },
          4,
        ),
        finalResponse,
      ],
    });
    const instructions = await loadVerifiedWorkflowInstructions(
      promptRoot,
      'single-color',
    );
    const agent = createCadAgent({
      model,
      instructions,
      workflowKind: 'single-color',
      implementations: {
        saveDesignBrief: async (input) => coordinator.saveDesignBrief(input),
        writeCadSource: async (input) =>
          coordinator.writeCadSource(input, sourceHash),
        buildAndCheck: async (input) => {
          coordinator.validateBuildInput(input);
          return coordinator.recordBuildResult(executionResult);
        },
        finishCadRun: async (input) => coordinator.finish(input),
      },
      resolvePhase: () => coordinatorPhase(coordinator.state),
    });

    const generated = await agent.generate({
      prompt: 'Start the run.',
      options: {
        runId,
        workflowKind: 'single-color',
        phase: 'briefing',
        userRequest: 'Design a printable hardware enclosure.',
        visualReviewConsent: 'declined',
      },
    });

    expect(
      generated.steps
        .flatMap((step) => step.toolCalls)
        .map((call) => call.toolName),
    ).toEqual([
      'saveDesignBrief',
      'writeCadSource',
      'buildAndCheck',
      'finishCadRun',
    ]);
    expect(coordinator.state.status).toBe('completed');
    expect(model.doGenerateCalls).toHaveLength(5);
  });

  it('runs the fixed multi-color fake-model path with isolated tools', async () => {
    const runId = 'fake-model-multi';
    const sourceHash = 'c'.repeat(64);
    const source = `from build123d import *
from amagine_cad import publish_color_model
body = Box(10, 10, 8)
logo = Box(4, 4, 2).translate((0, 0, 8))
publish_color_model({"body": (body, "#111111"), "logo": (logo, "#ffffff")}, "box", out_dir="cad_out")
`;
    const colorRegionPlan = {
      schemaVersion: 1 as const,
      regions: [
        {
          schemaVersion: 1 as const,
          id: 'body',
          name: 'body',
          colorName: 'black',
          hex: '#111111',
          expectedComponentCount: 1,
          features: ['shell'],
        },
        {
          schemaVersion: 1 as const,
          id: 'logo',
          name: 'logo',
          colorName: 'white',
          hex: '#ffffff',
          expectedComponentCount: 1,
          features: ['logo'],
        },
      ],
    };
    const designBrief: DesignBrief = {
      schemaVersion: 1,
      runId,
      workflowKind: 'multi-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['two-color enclosure'],
      verificationTargets: [],
      derivationNotes: [],
      colorRegionPlan,
    };
    const executionResult = passedMultiResult(runId);
    const artifactIds = executionResult.artifacts.map(
      ({ artifact }) => artifact.id,
    );
    const coordinator = new CadRunCoordinator({
      runId,
      userRequest: 'Design a two-tone AMS enclosure with a white logo.',
      preference: 'auto',
      researchEnabled: false,
      visualReviewConsent: 'declined',
    });
    const model = new MockLanguageModelV3({
      doGenerate: [
        toolResponse('saveDesignBrief', designBrief, 1),
        toolResponse(
          'writeCadSource',
          {
            schemaVersion: 1,
            runId,
            workflowKind: 'multi-color',
            source,
          },
          2,
        ),
        toolResponse(
          'buildAndCheck',
          { schemaVersion: 1, runId, sourceHash },
          3,
        ),
        toolResponse(
          'finishCadRun',
          { schemaVersion: 1, runId, artifactIds },
          4,
        ),
        finalResponse,
      ],
    });
    const instructions = await loadVerifiedWorkflowInstructions(
      promptRoot,
      'multi-color',
    );
    const agent = createCadAgent({
      model,
      instructions,
      workflowKind: 'multi-color',
      implementations: {
        saveDesignBrief: async (input) => coordinator.saveDesignBrief(input),
        writeCadSource: async (input) =>
          coordinator.writeCadSource(input, sourceHash),
        buildAndCheck: async (input) => {
          coordinator.validateBuildInput(input);
          return coordinator.recordBuildResult(executionResult);
        },
        finishCadRun: async (input) => coordinator.finish(input),
      },
      resolvePhase: () => coordinatorPhase(coordinator.state),
    });

    const generated = await agent.generate({
      prompt: 'Start the multi-color run.',
      options: {
        runId,
        workflowKind: 'multi-color',
        phase: 'briefing',
        userRequest: 'Design a two-tone AMS enclosure with a white logo.',
        visualReviewConsent: 'declined',
      },
    });

    expect(
      generated.steps
        .flatMap((step) => step.toolCalls)
        .map((call) => call.toolName),
    ).toEqual([
      'saveDesignBrief',
      'writeCadSource',
      'buildAndCheck',
      'finishCadRun',
    ]);
    expect(coordinator.workflowKind).toBe('multi-color');
    expect(coordinator.state.status).toBe('completed');
  });
});
