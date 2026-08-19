import { describe, expect, it } from 'vitest';

import type {
  ResearchPacket,
  WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import { InMemoryProjectRepository } from './in-memory-repository';
import { persistResearchStage } from './research-persistence';

const NOW = '2026-08-14T08:00:00.000Z';
const PACKET: ResearchPacket = {
  schemaVersion: 1,
  status: 'complete',
  advisoryOnly: true,
  queries: ['board dimensions'],
  findings: [
    {
      topic: 'Board width',
      summary: 'Official drawing.',
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
      accessedAt: NOW,
      sourceType: 'manufacturer',
    },
  ],
  warnings: [],
};
const EVENTS: WorkflowEventRecord[] = [
  {
    schemaVersion: 1,
    id: 'event-0',
    runId: 'run-research',
    sequence: 0,
    occurredAt: NOW,
    type: 'workflow-transition',
    payload: {
      eventType: 'begin_research',
      from: 'received',
      to: 'researching',
    },
  },
];

describe('persistResearchStage', () => {
  it('restores sources after refresh and ZIP export/import', async () => {
    const repository = new InMemoryProjectRepository();
    await persistResearchStage(repository, {
      projectId: 'p5-research',
      projectName: 'P5 Web Research',
      runId: 'run-research',
      createdAt: NOW,
      research: PACKET,
      events: EVENTS,
    });

    expect(
      (await repository.getRun('p5-research', 'run-research'))?.research
        ?.sources,
    ).toEqual(PACKET.sources);
    const archive = await repository.exportProject('p5-research');
    const imported = new InMemoryProjectRepository();
    await imported.importProject(archive);
    expect(
      (await imported.getRun('p5-research', 'run-research'))?.research?.sources,
    ).toEqual(PACKET.sources);
  });

  it('does not create research.json for a skipped request', async () => {
    const repository = new InMemoryProjectRepository();
    await persistResearchStage(repository, {
      projectId: 'p5-research',
      projectName: 'P5 Web Research',
      runId: 'run-skipped',
      createdAt: NOW,
      events: [],
    });

    expect(
      (await repository.getRun('p5-research', 'run-skipped'))?.research,
    ).toBeUndefined();
  });
});
