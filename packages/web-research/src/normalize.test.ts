import { describe, expect, it } from 'vitest';

import type { ResearchPacketDraft } from '@amagine3d/cad-protocol';

import { normalizeResearchPacket } from './normalize';

const NOW = '2026-08-14T08:00:00.000Z';

function draft(
  overrides: Partial<ResearchPacketDraft> = {},
): ResearchPacketDraft {
  return {
    schemaVersion: 1,
    status: 'complete',
    advisoryOnly: true,
    queries: ['board mechanical drawing'],
    findings: [
      {
        topic: 'Board width',
        summary: 'One drawing reports the board width.',
        value: 2,
        unit: 'in',
        confidence: 'high',
        sourceIds: ['community', 'official-copy'],
      },
    ],
    sources: [
      {
        id: 'community',
        title: 'Forum copy',
        url: 'https://example.com/drawing?utm_source=forum',
        accessedAt: NOW,
        sourceType: 'community',
      },
      {
        id: 'official-copy',
        title: 'Official drawing duplicate',
        url: 'https://example.com/drawing#dimensions',
        accessedAt: NOW,
        sourceType: 'manufacturer',
      },
    ],
    warnings: [],
    ...overrides,
  };
}

describe('normalizeResearchPacket', () => {
  it('ranks and deduplicates URLs while preserving original units', () => {
    const packet = normalizeResearchPacket(draft());

    expect(packet.sources).toHaveLength(1);
    expect(packet.sources[0]).toMatchObject({
      id: 'official-copy',
      sourceType: 'manufacturer',
      url: 'https://example.com/drawing',
    });
    expect(packet.findings[0]).toMatchObject({
      value: 50.8,
      unit: 'mm',
      originalExpression: '2 in',
      sourceIds: ['official-copy'],
    });
    expect(packet.status).toBe('partial');
  });

  it('keeps conflicting dimensions as separate advisory findings', () => {
    const packet = normalizeResearchPacket(
      draft({
        sources: [
          {
            id: 'a',
            title: 'Drawing A',
            url: 'https://vendor.example/a.pdf',
            accessedAt: NOW,
            sourceType: 'manufacturer',
          },
          {
            id: 'b',
            title: 'Drawing B',
            url: 'https://vendor.example/b.pdf',
            accessedAt: NOW,
            sourceType: 'datasheet',
          },
        ],
        findings: [
          {
            topic: 'Board width',
            summary: 'Revision A width.',
            value: 40,
            unit: 'mm',
            confidence: 'high',
            sourceIds: ['a'],
          },
          {
            topic: 'Board width',
            summary: 'Revision B width.',
            value: 42,
            unit: 'mm',
            confidence: 'high',
            sourceIds: ['b'],
          },
        ],
      }),
    );

    expect(packet.findings.map((finding) => finding.value)).toEqual([40, 42]);
    expect(packet.findings.every((finding) => finding.caveat)).toBe(true);
    expect(packet.warnings[0]).toContain('board width');
  });

  it('rejects packets beyond the configured response limit', () => {
    expect(() =>
      normalizeResearchPacket(draft(), { maxResponseBytes: 100 }),
    ).toThrow(RangeError);
  });
});
