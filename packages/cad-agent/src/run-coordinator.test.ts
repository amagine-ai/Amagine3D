import type {
  Artifact,
  CadExecutionResult,
  CadWorkflowKind,
  ColorRegionPlan,
  DesignBrief,
  QaReport,
} from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import { CadRunCoordinator } from './run-coordinator';

const textEncoder = new TextEncoder();

const lidHingeMechanism = {
  schemaVersion: 1 as const,
  id: 'lid-hinge',
  kind: 'revolute' as const,
  movingBodyIds: ['lid'],
  stationaryBodyIds: ['base', 'hinge-pin'],
  motions: [
    {
      type: 'rotation' as const,
      axisOrigin: [0, 30, 12] as [number, number, number],
      axisDirection: [1, 0, 0] as [number, number, number],
      angleDegrees: 110,
    },
  ],
  clearanceChecks: [
    {
      id: 'base-lid-gap',
      leftBodyId: 'base',
      rightBodyId: 'lid',
      minimumMm: 0.3,
      maximumMm: 0.8,
      poseScope: 'intermediate' as const,
    },
  ],
};

function brief(runId: string, workflowKind: CadWorkflowKind): DesignBrief {
  const colorRegionPlan: ColorRegionPlan | undefined =
    workflowKind === 'multi-color'
      ? {
          schemaVersion: 1,
          regions: [
            {
              schemaVersion: 1,
              id: 'body',
              name: 'body',
              colorName: 'black',
              hex: '#111111',
              expectedComponentCount: 1,
              features: ['shell'],
            },
            {
              schemaVersion: 1,
              id: 'logo',
              name: 'logo',
              colorName: 'white',
              hex: '#ffffff',
              expectedComponentCount: 1,
              features: ['logo'],
            },
          ],
        }
      : undefined;
  return {
    schemaVersion: 1,
    runId,
    workflowKind,
    userConstraints: [],
    agentAssumptions: [],
    researchHints: [],
    features: ['hardware enclosure'],
    verificationTargets: [],
    derivationNotes: [],
    ...(colorRegionPlan === undefined ? {} : { colorRegionPlan }),
  };
}

function artifact(
  runId: string,
  kind: Artifact['kind'],
  index: number,
  regionName?: string,
) {
  const bytes = textEncoder.encode(`${kind}-${String(index)}`).buffer;
  return {
    artifact: {
      schemaVersion: 1 as const,
      id: `${runId}-${kind}-${String(index)}`,
      runId,
      kind,
      fileName: `${kind}-${String(index)}`,
      mediaType: 'application/octet-stream',
      byteLength: bytes.byteLength,
      sha256: 'a'.repeat(64),
      createdAt: '2026-08-14T08:00:00.000Z',
      ...(regionName === undefined ? {} : { regionName }),
    },
    bytes,
  };
}

function result(
  runId: string,
  workflowKind: CadWorkflowKind,
  passed: boolean,
): CadExecutionResult {
  const failedCheck = {
    id: 'watertight',
    status: 'failed' as const,
    message: 'Mesh is not watertight.',
  };
  const qaReport: QaReport =
    workflowKind === 'single-color'
      ? {
          schemaVersion: 1,
          runId,
          workflowKind,
          status: passed ? 'passed' : 'failed',
          checks: passed
            ? [{ id: 'watertight', status: 'passed', message: 'Passed.' }]
            : [failedCheck],
        }
      : {
          schemaVersion: 1,
          runId,
          workflowKind,
          status: passed ? 'passed' : 'failed',
          checks: [],
          regionReports: [
            {
              regionId: 'body',
              componentCount: 1,
              watertight: passed,
              checks: passed
                ? [{ id: 'watertight', status: 'passed', message: 'Passed.' }]
                : [failedCheck],
            },
          ],
          overlapCheck: {
            id: 'overlap',
            status: 'passed',
            message: 'Passed.',
          },
          threeMfReadbackCheck: {
            id: '3mf-readback',
            status: 'passed',
            message: 'Passed.',
          },
        };
  const kinds: Artifact['kind'][] =
    workflowKind === 'single-color'
      ? ['model-source', 'build-report', 'qa-report', 'step', 'stl']
      : [
          'model-source',
          'color-plan',
          'build-report',
          'qa-report',
          'model-3mf',
          'region-stl',
          'region-stl',
        ];
  return {
    schemaVersion: 1,
    runId,
    qaReport,
    buildReport: { ok: passed },
    artifacts: kinds.map((kind, index) =>
      artifact(
        runId,
        kind,
        index,
        kind === 'region-stl' ? (index === 5 ? 'body' : 'logo') : undefined,
      ),
    ),
    runtimeVersions: { build123d: '0.11.1' },
  };
}

async function writtenCoordinator(
  runId: string,
  workflowKind: CadWorkflowKind,
  visualReviewConsent: 'approved' | 'declined' = 'declined',
  limits: { maxFailureOccurrences?: number; maxSteps?: number } = {},
) {
  const coordinator = new CadRunCoordinator({
    runId,
    userRequest:
      workflowKind === 'multi-color'
        ? 'Make a two-tone AMS enclosure.'
        : 'Make a printable enclosure.',
    preference: 'auto',
    researchEnabled: false,
    visualReviewConsent,
    ...limits,
    createId: (() => {
      let value = 0;
      return () => `event-${String(value++)}`;
    })(),
    now: () => new Date('2026-08-14T08:00:00.000Z'),
  });
  coordinator.saveDesignBrief(brief(runId, workflowKind));
  coordinator.writeCadSource(
    {
      schemaVersion: 1,
      runId,
      workflowKind,
      source: 'from build123d import Box\n',
    },
    'b'.repeat(64),
  );
  return coordinator;
}

describe('CadRunCoordinator', () => {
  it('rejects a closure brief without a frozen mechanism definition', () => {
    const coordinator = new CadRunCoordinator({
      runId: 'run-unchecked-hinge',
      userRequest: 'Design a box with an opening hinge lid.',
      preference: 'single-color',
      researchEnabled: false,
      visualReviewConsent: 'declined',
    });
    expect(() =>
      coordinator.saveDesignBrief(brief('run-unchecked-hinge', 'single-color')),
    ).toThrow(/mechanism definition/u);
  });

  it('does not accept passed QA when a frozen mechanism report is missing', () => {
    const runId = 'run-missing-mechanism-report';
    const coordinator = new CadRunCoordinator({
      runId,
      userRequest: 'Design a box with a removable hinge lid.',
      preference: 'single-color',
      researchEnabled: false,
      visualReviewConsent: 'declined',
    });
    coordinator.saveDesignBrief({
      ...brief(runId, 'single-color'),
      features: ['removable hinge lid'],
      mechanisms: [lidHingeMechanism],
    });
    coordinator.writeCadSource(
      {
        schemaVersion: 1,
        runId,
        workflowKind: 'single-color',
        source: 'from build123d import Box\n',
      },
      'b'.repeat(64),
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId,
      sourceHash: 'b'.repeat(64),
    });
    const output = coordinator.recordBuildResult(
      result(runId, 'single-color', true),
    );
    expect(output).toMatchObject({
      tool: 'buildAndCheck',
      status: 'failed',
      failedCheckIds: ['qa:mechanism:lid-hinge'],
    });
  });

  it.each([
    ['disabled', false, undefined, 'research_skipped'],
    [
      'available',
      true,
      {
        schemaVersion: 1 as const,
        status: 'partial' as const,
        advisoryOnly: true as const,
        queries: ['board dimensions'],
        findings: [],
        sources: [],
        warnings: ['Only partial data was available.'],
      },
      'research_ready',
    ],
    [
      'failed softly',
      true,
      {
        schemaVersion: 1 as const,
        status: 'failed' as const,
        advisoryOnly: true as const,
        queries: [],
        findings: [],
        sources: [],
        warnings: ['Provider timed out.'],
      },
      'research_failed',
    ],
  ])(
    'records the %s research path before briefing',
    (_label, enabled, packet, researchState) => {
      const coordinator = new CadRunCoordinator({
        runId: `run-research-${String(enabled)}`,
        userRequest: 'Design an enclosure.',
        preference: 'auto',
        researchEnabled: enabled,
        visualReviewConsent: 'declined',
        ...(packet === undefined ? {} : { research: packet }),
      });
      expect(
        coordinator.events.some(
          (event) =>
            event.type === 'workflow-transition' &&
            event.payload.to === researchState,
        ),
      ).toBe(true);
      expect(coordinator.state.status).toBe('briefing');
    },
  );

  it.each(['single-color', 'multi-color'] as const)(
    'enforces the complete %s success path',
    async (workflowKind) => {
      const coordinator = await writtenCoordinator('run-success', workflowKind);
      coordinator.validateBuildInput({
        schemaVersion: 1,
        runId: 'run-success',
        sourceHash: 'b'.repeat(64),
      });
      const output = coordinator.recordBuildResult(
        result('run-success', workflowKind, true),
      );
      expect(output).toMatchObject({ tool: 'buildAndCheck', status: 'passed' });
      expect(coordinator.state.status).toBe('ready_to_finish');
      coordinator.finish({
        schemaVersion: 1,
        runId: 'run-success',
        artifactIds: coordinator.artifacts.map((item) => item.id),
      });
      expect(coordinator.state.status).toBe('completed');
    },
  );

  it('allows a repair after deterministic QA failure', async () => {
    const coordinator = await writtenCoordinator('run-repair', 'single-color');
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-repair',
      sourceHash: 'b'.repeat(64),
    });
    const failure = coordinator.recordBuildResult(
      result('run-repair', 'single-color', false),
    );
    expect(failure).toMatchObject({
      tool: 'buildAndCheck',
      status: 'failed',
      failedCheckIds: ['watertight'],
      repairContext: {
        baselineSourceHash: 'b'.repeat(64),
        newlyFailedCheckIds: [],
        resolvedCheckIds: [],
        regression: false,
      },
      summary: expect.stringContaining('watertight: Mesh is not watertight.'),
    });
    expect(coordinator.state.status).toBe('coding');
    coordinator.writeCadSource(
      {
        schemaVersion: 1,
        runId: 'run-repair',
        workflowKind: 'single-color',
        source: 'from build123d import Box\n# repaired\n',
      },
      'c'.repeat(64),
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-repair',
      sourceHash: 'c'.repeat(64),
    });
    coordinator.recordBuildResult(result('run-repair', 'single-color', true));
    expect(coordinator.state.status).toBe('ready_to_finish');
  });

  it('keeps source-level build errors inside the same repair discipline', async () => {
    const coordinator = await writtenCoordinator(
      'run-build-error',
      'single-color',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-build-error',
      sourceHash: 'b'.repeat(64),
    });
    const output = coordinator.recordBuildFailure(
      "NameError: name 'Scale' is not defined",
    );
    expect(output).toMatchObject({
      tool: 'buildAndCheck',
      status: 'failed',
      failedCheckIds: ["build:NameError: name 'Scale' is not defined"],
      repairContext: {
        baselineSourceHash: 'b'.repeat(64),
        regression: false,
      },
    });
    expect(coordinator.state.status).toBe('coding');
  });

  it('stops after the same check recurs five times', async () => {
    const coordinator = await writtenCoordinator('run-five', 'single-color');
    for (let attempt = 0; attempt < 5; attempt += 1) {
      coordinator.validateBuildInput({
        schemaVersion: 1,
        runId: 'run-five',
        sourceHash: (attempt === 0 ? 'b' : String(attempt)).repeat(64),
      });
      coordinator.recordBuildResult(result('run-five', 'single-color', false));
      if (attempt < 4) {
        coordinator.writeCadSource(
          {
            schemaVersion: 1,
            runId: 'run-five',
            workflowKind: 'single-color',
            source: `from build123d import Box\n# repair ${String(attempt)}\n`,
          },
          String(attempt + 1).repeat(64),
        );
      }
    }
    expect(coordinator.state).toMatchObject({ status: 'failed' });
  });

  it('detects regressions and stops an alternating failure loop', async () => {
    const coordinator = await writtenCoordinator(
      'run-cycle',
      'single-color',
      'declined',
      { maxFailureOccurrences: 3 },
    );
    const ids = ['watertight', 'assembly-body-overlap'] as const;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const sourceHash =
        attempt === 0 ? 'b'.repeat(64) : String(attempt).repeat(64);
      coordinator.validateBuildInput({
        schemaVersion: 1,
        runId: 'run-cycle',
        sourceHash,
      });
      const failedResult = result('run-cycle', 'single-color', false);
      failedResult.qaReport.checks = [
        {
          id: ids[attempt % ids.length] ?? 'watertight',
          status: 'failed',
          message: 'Failed.',
        },
      ];
      const output = coordinator.recordBuildResult(failedResult);
      if (output.tool !== 'buildAndCheck')
        throw new Error('expected buildAndCheck');
      if (attempt === 1) {
        expect(output.repairContext).toMatchObject({
          baselineSourceHash: 'b'.repeat(64),
          newlyFailedCheckIds: ['assembly-body-overlap'],
          resolvedCheckIds: ['watertight'],
          regression: true,
        });
        coordinator.restoreRepairBaseline('b'.repeat(64));
      }
      if (attempt < 4) {
        coordinator.writeCadSource(
          {
            schemaVersion: 1,
            runId: 'run-cycle',
            workflowKind: 'single-color',
            source: `from build123d import Box\n# alternating repair ${String(attempt)}\n`,
          },
          String(attempt + 1).repeat(64),
        );
      }
    }
    expect(coordinator.state).toMatchObject({ status: 'failed' });
  });

  it('restores the accepted source hash and failure set after a regression', async () => {
    const coordinator = await writtenCoordinator(
      'run-rollback',
      'single-color',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-rollback',
      sourceHash: 'b'.repeat(64),
    });
    coordinator.recordBuildResult(
      result('run-rollback', 'single-color', false),
    );
    coordinator.writeCadSource(
      {
        schemaVersion: 1,
        runId: 'run-rollback',
        workflowKind: 'single-color',
        source: 'from build123d import Box\n# regression\n',
      },
      'c'.repeat(64),
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-rollback',
      sourceHash: 'c'.repeat(64),
    });
    const regressed = result('run-rollback', 'single-color', false);
    regressed.qaReport.checks = [
      {
        id: 'assembly-body-overlap',
        status: 'failed',
        message: 'New collision.',
      },
    ];
    const output = coordinator.recordBuildResult(regressed);
    expect(output).toMatchObject({
      repairContext: { regression: true, baselineSourceHash: 'b'.repeat(64) },
    });

    coordinator.restoreRepairBaseline('b'.repeat(64));
    expect(() =>
      coordinator.validateBuildInput({
        schemaVersion: 1,
        runId: 'run-rollback',
        sourceHash: 'b'.repeat(64),
      }),
    ).not.toThrow();
  });

  it('orders structural and assembly diagnostics before finishing', async () => {
    const coordinator = await writtenCoordinator(
      'run-priority',
      'single-color',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-priority',
      sourceHash: 'b'.repeat(64),
    });
    const failedResult = result('run-priority', 'single-color', false);
    failedResult.qaReport.checks = [
      { id: 'bevel-partial', status: 'failed', message: 'Finish failed.' },
      {
        id: 'assembly-body-overlap',
        status: 'failed',
        message: 'Bodies collide.',
      },
      { id: 'missed-cut', status: 'failed', message: 'Cut removed nothing.' },
    ];
    const output = coordinator.recordBuildResult(failedResult);
    if (output.tool !== 'buildAndCheck')
      throw new Error('expected buildAndCheck');
    expect(output.failedCheckIds).toEqual([
      'missed-cut',
      'assembly-body-overlap',
      'bevel-partial',
    ]);
    expect(output.summary.indexOf('missed-cut:')).toBeLessThan(
      output.summary.indexOf('assembly-body-overlap:'),
    );
    expect(output.summary.indexOf('assembly-body-overlap:')).toBeLessThan(
      output.summary.indexOf('bevel-partial:'),
    );
  });

  it('surfaces build-report issues and measurements in a failed QA summary', async () => {
    const coordinator = await writtenCoordinator(
      'run-diagnostics',
      'single-color',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-diagnostics',
      sourceHash: 'b'.repeat(64),
    });
    const failedResult = result('run-diagnostics', 'single-color', false);
    failedResult.buildReport = {
      ok: false,
      issues: ['FILLET_FAILED: perimeter — all sizes failed'],
      measurements: {
        base: {
          size_x: 120,
          size_y: 60,
          size_z: 10,
          min: [0, 0, 0],
          max: [120, 60, 10],
        },
      },
    };
    const output = coordinator.recordBuildResult(failedResult);
    if (output.tool !== 'buildAndCheck')
      throw new Error('expected buildAndCheck');
    expect(output).toMatchObject({
      status: 'failed',
      summary: expect.stringContaining(
        'Build issues: FILLET_FAILED: perimeter — all sizes failed',
      ),
    });
    expect(output.summary).toContain('Measurements: base=120x60x10mm');
    expect(coordinator.state.status).toBe('coding');
  });

  it('gates approved visual review and returns rejected review to coding', async () => {
    const coordinator = await writtenCoordinator(
      'run-visual',
      'multi-color',
      'approved',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-visual',
      sourceHash: 'b'.repeat(64),
    });
    coordinator.recordBuildResult(result('run-visual', 'multi-color', true));
    const primary = coordinator.state;
    expect(primary.status).toBe('visual_review_waiting');
    coordinator.requestVisualReview({
      schemaVersion: 1,
      runId: 'run-visual',
      artifactId: 'run-visual-model-3mf-4',
      reviewFocus: 'Check region colors and silhouette.',
    });
    coordinator.recordVisualReview(false, 'The logo is too small.');
    expect(coordinator.state.status).toBe('coding');
  });

  it('continues to finish after an approved visual review passes', async () => {
    const coordinator = await writtenCoordinator(
      'run-visual-pass',
      'single-color',
      'approved',
    );
    coordinator.validateBuildInput({
      schemaVersion: 1,
      runId: 'run-visual-pass',
      sourceHash: 'b'.repeat(64),
    });
    coordinator.recordBuildResult(
      result('run-visual-pass', 'single-color', true),
    );
    const artifactId =
      coordinator.state.status === 'visual_review_waiting'
        ? coordinator.state.artifactId
        : 'missing';
    coordinator.requestVisualReview({
      schemaVersion: 1,
      runId: 'run-visual-pass',
      artifactId,
      reviewFocus: 'Check the silhouette.',
    });
    coordinator.recordVisualReview(true, 'The preview matches the brief.');
    expect(coordinator.state.status).toBe('ready_to_finish');
  });

  it('rejects visual review without consent and supports cancellation', async () => {
    const coordinator = await writtenCoordinator('run-cancel', 'single-color');
    expect(() =>
      coordinator.requestVisualReview({
        schemaVersion: 1,
        runId: 'run-cancel',
        artifactId: 'preview',
        reviewFocus: 'Inspect it.',
      }),
    ).toThrow(/did not approve/u);
    coordinator.cancel();
    expect(coordinator.state.status).toBe('cancelled');
  });

  it('supports an explicit terminal failure for host-side tool errors', () => {
    const coordinator = new CadRunCoordinator({
      runId: 'run-host-failure',
      userRequest: 'Design an enclosure.',
      preference: 'auto',
      researchEnabled: false,
      visualReviewConsent: 'declined',
    });

    coordinator.fail('saveDesignBrief failed repeatedly.');
    coordinator.fail('A later duplicate failure must be ignored.');

    expect(coordinator.state).toMatchObject({
      status: 'failed',
      reason: 'saveDesignBrief failed repeatedly.',
    });
  });
});
