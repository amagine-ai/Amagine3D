import { describe, expect, it } from 'vitest';

import {
  cadWorkerRequestSchema,
  designBriefSchema,
  projectArchiveManifestSchema,
  projectRevisionSchema,
  qaReportSchema,
  researchPacketSchema,
} from './schemas';

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
      minimumSamples: 12,
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

describe('public protocol schemas', () => {
  it('rejects external messages without schemaVersion', () => {
    expect(() =>
      cadWorkerRequestSchema.parse({
        type: 'bootstrap',
        requestId: 'request-1',
      }),
    ).toThrow();
  });

  it('parses a versioned Worker build request', () => {
    expect(
      cadWorkerRequestSchema.parse({
        schemaVersion: 1,
        type: 'build',
        requestId: 'request-2',
        runId: 'run-2',
        workflowKind: 'single-color',
        source: 'from build123d import Box',
        sourceHash: 'a'.repeat(64),
        parameterOverrides: { WIDTH: 40 },
      }).type,
    ).toBe('build');
  });

  it('carries frozen mechanism definitions into Worker build requests', () => {
    const parsed = cadWorkerRequestSchema.safeParse({
      schemaVersion: 1,
      type: 'build',
      requestId: 'request-mechanism',
      runId: 'run-mechanism',
      workflowKind: 'single-color',
      source: 'from build123d import Box',
      sourceHash: 'a'.repeat(64),
      parameterOverrides: {},
      mechanisms: [lidHingeMechanism],
    });
    expect(parsed.success).toBe(true);
    if (parsed.success && parsed.data.type === 'build') {
      expect(parsed.data.mechanisms?.[0]?.id).toBe('lid-hinge');
    }
  });

  it('carries feature-level measurement targets into Worker builds', () => {
    const parsed = cadWorkerRequestSchema.parse({
      schemaVersion: 1,
      type: 'build',
      requestId: 'request-features',
      runId: 'run-features',
      workflowKind: 'single-color',
      source: 'from build123d import Box',
      sourceHash: 'a'.repeat(64),
      parameterOverrides: {},
      featureChecks: [
        {
          id: 'usb-cutout-width',
          featureId: 'usb-cutout',
          metric: 'sizeX',
          expected: 12.4,
          tolerance: 0.2,
        },
      ],
    });
    expect(parsed.type === 'build' && parsed.featureChecks?.[0]).toMatchObject({
      featureId: 'usb-cutout',
      metric: 'sizeX',
    });
  });

  it('keeps single and multi-color Worker profiles isolated', () => {
    const base = {
      schemaVersion: 1,
      type: 'build',
      requestId: 'request-color',
      runId: 'run-color',
      source: 'from build123d import Box',
      sourceHash: 'a'.repeat(64),
      parameterOverrides: {},
    };
    expect(() =>
      cadWorkerRequestSchema.parse({
        ...base,
        workflowKind: 'multi-color',
      }),
    ).toThrow(/color-region plan/u);
    expect(() =>
      cadWorkerRequestSchema.parse({
        ...base,
        workflowKind: 'single-color',
        colorRegionPlan: {
          schemaVersion: 1,
          regions: [
            {
              schemaVersion: 1,
              id: 'body',
              name: 'body',
              colorName: 'black',
              hex: '#000000',
              expectedComponentCount: 1,
              features: [],
            },
            {
              schemaVersion: 1,
              id: 'logo',
              name: 'logo',
              colorName: 'white',
              hex: '#ffffff',
              expectedComponentCount: 1,
              features: [],
            },
          ],
        },
      }),
    ).toThrow(/cannot contain/u);
  });

  it('keeps research advisory-only and rejects non-http sources', () => {
    const packet = {
      schemaVersion: 1,
      status: 'complete',
      advisoryOnly: true,
      queries: ['board dimensions'],
      findings: [
        {
          topic: 'board width',
          summary: 'The drawing lists a width.',
          value: 40,
          unit: 'mm',
          confidence: 'high',
          sourceIds: ['source-1'],
        },
      ],
      sources: [
        {
          id: 'source-1',
          title: 'Mechanical drawing',
          url: 'https://example.com/drawing.pdf',
          accessedAt: '2026-08-13T08:00:00.000Z',
          sourceType: 'manufacturer',
        },
      ],
      warnings: [],
    };

    expect(researchPacketSchema.parse(packet).advisoryOnly).toBe(true);
    expect(() =>
      researchPacketSchema.parse({
        ...packet,
        sources: [{ ...packet.sources[0], url: 'file:///private/drawing.pdf' }],
      }),
    ).toThrow();
  });

  it('rejects research findings that reference missing sources', () => {
    expect(() =>
      researchPacketSchema.parse({
        schemaVersion: 1,
        status: 'partial',
        advisoryOnly: true,
        queries: ['board dimensions'],
        findings: [
          {
            topic: 'Board width',
            summary: 'Reported width.',
            value: 40,
            unit: 'mm',
            confidence: 'medium',
            sourceIds: ['missing'],
          },
        ],
        sources: [],
        warnings: [],
      }),
    ).toThrow(/saved source/u);
  });

  it('requires a color region plan for multi-color briefs', () => {
    expect(() =>
      designBriefSchema.parse({
        schemaVersion: 1,
        runId: 'run-color',
        workflowKind: 'multi-color',
        userConstraints: [],
        agentAssumptions: [],
        researchHints: [],
        features: ['contrasting logo'],
        verificationTargets: [],
        derivationNotes: [],
      }),
    ).toThrow(/color-region plan/u);
  });

  it('requires deterministic mechanism definitions for closure features', () => {
    const withoutMechanism = designBriefSchema.safeParse({
      schemaVersion: 1,
      runId: 'run-hinge',
      workflowKind: 'single-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['removable hinge lid'],
      verificationTargets: [],
      derivationNotes: [],
    });
    expect(withoutMechanism.success).toBe(false);

    const withMechanism = designBriefSchema.safeParse({
      schemaVersion: 1,
      runId: 'run-hinge',
      workflowKind: 'single-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['removable hinge lid'],
      verificationTargets: [],
      derivationNotes: [],
      mechanisms: [lidHingeMechanism],
    });
    expect(withMechanism.success).toBe(true);
  });

  it('rejects invalid mechanism axes, partitions, and clearance references', () => {
    const baseBrief = {
      schemaVersion: 1 as const,
      runId: 'run-invalid-hinge',
      workflowKind: 'single-color' as const,
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['hinge'],
      verificationTargets: [],
      derivationNotes: [],
    };
    const zeroAxis = designBriefSchema.safeParse({
      ...baseBrief,
      mechanisms: [
        {
          ...lidHingeMechanism,
          motions: [
            {
              ...lidHingeMechanism.motions[0],
              axisDirection: [0, 0, 0],
            },
          ],
        },
      ],
    });
    expect(zeroAxis.success).toBe(false);

    const overlappingGroups = designBriefSchema.safeParse({
      ...baseBrief,
      mechanisms: [
        {
          ...lidHingeMechanism,
          stationaryBodyIds: ['base', 'lid'],
        },
      ],
    });
    expect(overlappingGroups.success).toBe(false);

    const unknownClearanceBody = designBriefSchema.safeParse({
      ...baseBrief,
      mechanisms: [
        {
          ...lidHingeMechanism,
          clearanceChecks: [
            {
              ...lidHingeMechanism.clearanceChecks[0],
              rightBodyId: 'unknown-body',
            },
          ],
        },
      ],
    });
    expect(unknownClearanceBody.success).toBe(false);

    const unanchoredRevolute = designBriefSchema.safeParse({
      ...baseBrief,
      mechanisms: [
        {
          ...lidHingeMechanism,
          clearanceChecks: lidHingeMechanism.clearanceChecks.map(
            (clearance) => ({
              id: clearance.id,
              leftBodyId: clearance.leftBodyId,
              rightBodyId: clearance.rightBodyId,
              minimumMm: clearance.minimumMm,
              poseScope: clearance.poseScope,
            }),
          ),
        },
      ],
    });
    expect(unanchoredRevolute.success).toBe(false);

    const inconsistentPartitions = designBriefSchema.safeParse({
      ...baseBrief,
      mechanisms: [
        lidHingeMechanism,
        {
          ...lidHingeMechanism,
          id: 'second-path',
          stationaryBodyIds: ['base'],
        },
      ],
    });
    expect(inconsistentPartitions.success).toBe(false);
  });

  it('requires profile-aware multi-color QA fields', () => {
    expect(() =>
      qaReportSchema.parse({
        schemaVersion: 1,
        runId: 'run-color',
        workflowKind: 'multi-color',
        status: 'passed',
        checks: [],
      }),
    ).toThrow(/Multi-color QA/u);
  });

  it('cannot mark failed deterministic checks as passed', () => {
    expect(() =>
      qaReportSchema.parse({
        schemaVersion: 1,
        runId: 'run-single',
        workflowKind: 'single-color',
        status: 'passed',
        checks: [
          {
            id: 'missed-cut',
            status: 'failed',
            message: 'Connector cut missed the enclosure wall.',
          },
        ],
      }),
    ).toThrow(/passed QA report/u);
  });

  it('cannot hide a failed mechanism check in a passed QA report', () => {
    const parsed = qaReportSchema.safeParse({
      schemaVersion: 1,
      runId: 'run-mechanism-failed',
      workflowKind: 'single-color',
      status: 'passed',
      checks: [],
      mechanismReports: [
        {
          mechanismId: 'lid-hinge',
          sampledPoseCount: 13,
          maxCollisionVolumeMm3: 4.2,
          checks: [
            {
              id: 'mechanism-lid-hinge-motion-collision',
              status: 'failed',
              message: 'The sampled lid path collides with the base.',
            },
          ],
        },
      ],
    });
    expect(parsed.success).toBe(false);
  });

  it('parses the additive P2 revision document without changing v1', () => {
    expect(
      projectRevisionSchema.parse({
        schemaVersion: 1,
        id: 'revision-1',
        projectId: 'project-1',
        revision: 1,
        createdAt: '2026-08-13T08:00:00.000Z',
        sourceHash: 'a'.repeat(64),
        modelSource: 'from build123d import Box',
        parameters: null,
        reason: 'automatic-rollback',
        restoredFromRevisionId: 'revision-0',
        repairContext: {
          baselineSourceHash: 'a'.repeat(64),
          baselineRevisionId: 'revision-0',
          newlyFailedCheckIds: ['assembly-overlap'],
          resolvedCheckIds: [],
          regression: true,
          rollbackApplied: true,
          directive: 'Repair only the affected assembly interface.',
        },
      }).schemaVersion,
    ).toBe(1);
  });

  it('requires checksums on every P2 archive entry', () => {
    expect(() =>
      projectArchiveManifestSchema.parse({
        schemaVersion: 1,
        format: 'amagine3d-project',
        projectId: 'project-1',
        projectName: 'Enclosure',
        exportedAt: '2026-08-13T08:00:00.000Z',
        entries: [{ path: 'project-1/project.json', byteLength: 10 }],
      }),
    ).toThrow();
  });
});
