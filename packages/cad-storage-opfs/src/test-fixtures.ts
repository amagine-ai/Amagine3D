import {
  SCHEMA_VERSION,
  type Artifact,
  type CadProject,
  type CadRun,
  type ProjectRevision,
  type ResearchPacket,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import { encodeText, sha256 } from './hash';
import type { StoredRun } from './types';

export const FIXED_NOW = '2026-08-13T08:00:00.000Z';
export const MODEL_SOURCE =
  'from build123d import Box\nresult = Box(40, 30, 20)\n';

export function makeProject(overrides: Partial<CadProject> = {}): CadProject {
  return {
    schemaVersion: SCHEMA_VERSION,
    id: 'project-1',
    name: 'Sensor enclosure',
    createdAt: FIXED_NOW,
    updatedAt: FIXED_NOW,
    revision: 0,
    currentRunId: null,
    ...overrides,
  };
}

export async function makeRevision(): Promise<ProjectRevision> {
  const sourceHash = await sha256(encodeText(MODEL_SOURCE));
  return {
    schemaVersion: SCHEMA_VERSION,
    id: 'revision-0',
    projectId: 'project-1',
    revision: 0,
    createdAt: FIXED_NOW,
    sourceHash,
    modelSource: MODEL_SOURCE,
    parameters: {
      schemaVersion: SCHEMA_VERSION,
      sourceHash,
      parameters: [
        {
          name: 'WIDTH',
          label: 'Width',
          type: 'number',
          defaultValue: 40,
          value: 40,
          unit: 'mm',
          minimum: 20,
          maximum: 80,
          step: 1,
        },
      ],
      history: [],
      historyCursor: 0,
    },
  };
}

export function makeEvent(sequence = 0): WorkflowEventRecord {
  return {
    schemaVersion: SCHEMA_VERSION,
    id: `event-${String(sequence)}`,
    runId: 'run-1',
    sequence,
    occurredAt: FIXED_NOW,
    type: 'workflow-transition',
    payload: {
      eventType: 'start',
      from: 'received',
      to: 'selecting_workflow',
    },
  };
}

export const RESEARCH_PACKET: ResearchPacket = {
  schemaVersion: SCHEMA_VERSION,
  status: 'complete',
  advisoryOnly: true,
  queries: ['sensor board dimensions'],
  findings: [
    {
      topic: 'board width',
      summary: 'The manufacturer drawing lists the board width.',
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
      url: 'https://example.com/mechanical.pdf',
      accessedAt: FIXED_NOW,
      sourceType: 'manufacturer',
    },
  ],
  warnings: [],
};

export async function makeStoredRun(
  status: CadRun['status'] = 'succeeded',
  events: WorkflowEventRecord[] = [makeEvent()],
): Promise<StoredRun> {
  const bytes = encodeText('solid enclosure\nendsolid enclosure\n');
  const artifact: Artifact = {
    schemaVersion: SCHEMA_VERSION,
    id: 'artifact-stl',
    runId: 'run-1',
    kind: 'stl',
    fileName: 'model.stl',
    mediaType: 'model/stl',
    byteLength: bytes.byteLength,
    sha256: await sha256(bytes),
    createdAt: FIXED_NOW,
  };
  const sourceHash = await sha256(encodeText(MODEL_SOURCE));
  return {
    run: {
      schemaVersion: SCHEMA_VERSION,
      id: 'run-1',
      projectId: 'project-1',
      createdAt: FIXED_NOW,
      completedAt: status === 'active' ? null : FIXED_NOW,
      status,
      workflowKind: 'single-color',
      workflowSelectionReason: 'Default single-color workflow.',
      sourceHash,
      workflowSnapshot: {
        engine: 'Amagine3D-CAD',
        revision: 'workflow-2026.08.18.2',
        profile: 'hardware-enclosure-single',
      },
      artifactIds: [artifact.id],
    },
    events,
    artifacts: [{ metadata: artifact, bytes }],
    research: RESEARCH_PACKET,
    buildReport: {
      schemaVersion: SCHEMA_VERSION,
      data: { durationMs: 1200, sourceHash },
    },
    qaReport: {
      schemaVersion: SCHEMA_VERSION,
      runId: 'run-1',
      workflowKind: 'single-color',
      status: 'passed',
      checks: [
        { id: 'validity', status: 'passed', message: 'Shape is valid.' },
      ],
    },
  };
}
