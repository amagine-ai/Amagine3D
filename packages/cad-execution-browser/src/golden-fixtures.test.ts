import { readFile } from 'node:fs/promises';

import {
  cadWorkerRequestSchema,
  SCHEMA_VERSION,
} from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import { sha256Hex } from './hash';
import { validateCadSource } from './source-policy';

const fixtureRoot = new URL('../fixtures/', import.meta.url);

describe('P3 golden fixtures', () => {
  it('keeps isolated, protocol-valid single and multi-color sources', async () => {
    const golden = JSON.parse(
      await readFile(new URL('golden.json', fixtureRoot), 'utf8'),
    ) as {
      'single-color': {
        source: string;
        qaTargets: Record<string, number>;
      };
      'multi-color': {
        source: string;
        qaTargets: Record<string, number>;
        colorRegionPlan: unknown;
      };
    };
    for (const workflowKind of ['single-color', 'multi-color'] as const) {
      const fixture = golden[workflowKind];
      const source = await readFile(
        new URL(fixture.source, fixtureRoot),
        'utf8',
      );
      validateCadSource(source, workflowKind);
      const sourceHash = await sha256Hex(new TextEncoder().encode(source));
      const request = cadWorkerRequestSchema.parse({
        schemaVersion: SCHEMA_VERSION,
        requestId: `${workflowKind}-request`,
        type: 'build',
        projectId: 'golden-enclosure',
        runId: `${workflowKind}-run`,
        workflowKind,
        source,
        sourceHash,
        parameterOverrides: {},
        qaTargets: fixture.qaTargets,
        ...(workflowKind === 'multi-color'
          ? { colorRegionPlan: golden['multi-color'].colorRegionPlan }
          : {}),
      });
      expect(request.type).toBe('build');
    }
  });
});
