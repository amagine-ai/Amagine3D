import { describe, expect, it } from 'vitest';

import { CadDomainError, type ResearchPacket } from '@amagine3d/cad-protocol';

import { FakeWebResearchService } from './fake-service';
import { runResearchStage } from './orchestrator';

const PACKET: ResearchPacket = {
  schemaVersion: 1,
  status: 'complete',
  advisoryOnly: true,
  queries: ['sensor dimensions'],
  findings: [
    {
      topic: 'Sensor width',
      summary: 'The official drawing reports 18 mm.',
      value: 18,
      unit: 'mm',
      confidence: 'high',
      sourceIds: ['source-1'],
    },
  ],
  sources: [
    {
      id: 'source-1',
      title: 'Sensor mechanical drawing',
      url: 'https://manufacturer.example/mechanical.pdf',
      accessedAt: '2026-08-14T08:00:00.000Z',
      sourceType: 'manufacturer',
      summary: 'Official dimensions and mounting details.',
    },
  ],
  warnings: [],
};

const request = (enabled: boolean) => ({
  schemaVersion: 1 as const,
  runId: 'run-research',
  query: 'Design an enclosure for the sensor.',
  enabled,
});

describe('runResearchStage', () => {
  it('makes zero service calls when the request snapshot is off', async () => {
    const service = new FakeWebResearchService({ packet: PACKET });
    const result = await runResearchStage(service, request(false));

    expect(service.calls).toHaveLength(0);
    expect(result.packet).toBeUndefined();
    expect(result.streamEvents.map((event) => event.type)).toEqual([
      'research-status',
      'research-result',
      'workflow-ready',
    ]);
  });

  it('emits completion strictly before briefing readiness', async () => {
    const service = new FakeWebResearchService({ packet: PACKET });
    const result = await runResearchStage(service, request(true));
    const types = result.streamEvents.map((event) => event.type);
    const statusIndex = result.streamEvents.findIndex(
      (event) =>
        event.type === 'research-status' && event.status === 'complete',
    );

    expect(service.calls).toHaveLength(1);
    expect(types).toContain('research-reference');
    expect(statusIndex).toBeLessThan(types.indexOf('workflow-ready'));
    expect(result.workflowEvents.at(-1)?.payload).toMatchObject({
      to: 'selecting_workflow',
    });
  });

  it.each(['timeout', 'rate limit', 'no results'])(
    '%s fails soft',
    async (reason) => {
      const service = new FakeWebResearchService({
        error: new CadDomainError('ResearchUnavailable', reason, {
          category: 'research',
          retryable: true,
        }),
      });
      const result = await runResearchStage(service, request(true));

      expect(result.packet?.status).toBe('failed');
      expect(
        result.workflowEvents.some((event) => event.type === 'warning'),
      ).toBe(true);
      expect(result.streamEvents.at(-1)).toMatchObject({
        type: 'workflow-ready',
        next: 'briefing',
      });
    },
  );
});
