import {
  SCHEMA_VERSION,
  artifactSchema,
  colorRegionPlanSchema,
  type CadWorkerArtifactPayload,
} from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import { createViewerModelFromArtifacts } from './viewer-model';

function payload(
  id: string,
  kind: 'model-3mf' | 'preview-glb' | 'region-stl' | 'stl',
  regionName?: string,
): CadWorkerArtifactPayload {
  const bytes = new Uint8Array([1, 2, 3]).buffer;
  return {
    artifact: artifactSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      id,
      runId: 'run-1',
      kind,
      fileName: `${id}.${kind === 'preview-glb' ? 'glb' : kind === 'model-3mf' ? '3mf' : 'stl'}`,
      mediaType:
        kind === 'preview-glb'
          ? 'model/gltf-binary'
          : kind === 'model-3mf'
            ? 'model/3mf'
            : 'model/stl',
      byteLength: bytes.byteLength,
      sha256: 'a'.repeat(64),
      createdAt: '2026-08-13T00:00:00.000Z',
      regionName,
    }),
    bytes,
  };
}

describe('createViewerModelFromArtifacts', () => {
  it('prefers region STLs and binds frozen color metadata', () => {
    const colorRegionPlan = colorRegionPlanSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      regions: [
        {
          schemaVersion: SCHEMA_VERSION,
          id: 'body',
          name: 'Shell + lid',
          colorName: 'Graphite',
          hex: '#313633',
          expectedComponentCount: 1,
          features: ['shell'],
        },
        {
          schemaVersion: SCHEMA_VERSION,
          id: 'badge',
          name: 'Raised badge',
          colorName: 'Signal green',
          hex: '#15A173',
          expectedComponentCount: 1,
          features: ['badge'],
        },
      ],
    });

    const result = createViewerModelFromArtifacts({
      id: 'run-1',
      name: 'Controller enclosure',
      artifacts: [
        payload('preview', 'preview-glb'),
        payload('body-stl', 'region-stl', 'body'),
        payload('badge-stl', 'region-stl', 'badge'),
      ],
      colorRegionPlan,
    });

    expect(result.parts.map((part) => part.format)).toEqual(['stl', 'stl']);
    expect(result.parts.map((part) => part.region?.hex)).toEqual([
      '#313633',
      '#15A173',
    ]);
    expect(result.layout).toBe('assembled');
  });

  it('keeps multiple non-region bodies in assembly coordinates by default', () => {
    const result = createViewerModelFromArtifacts({
      id: 'assembly-run',
      name: 'Base and lid',
      artifacts: [payload('base', 'stl'), payload('lid', 'stl')],
    });

    expect(result.layout).toBe('assembled');
  });

  it('falls back to GLB, then 3MF, then overall STL', () => {
    expect(
      createViewerModelFromArtifacts({
        id: 'glb-run',
        name: 'GLB run',
        artifacts: [payload('stl', 'stl'), payload('glb', 'preview-glb')],
      }).parts.map((part) => part.format),
    ).toEqual(['glb']);
    expect(
      createViewerModelFromArtifacts({
        id: '3mf-run',
        name: '3MF run',
        artifacts: [payload('stl', 'stl'), payload('assembly', 'model-3mf')],
      }).parts.map((part) => part.format),
    ).toEqual(['3mf']);
    expect(
      createViewerModelFromArtifacts({
        id: 'stl-run',
        name: 'STL run',
        artifacts: [payload('stl', 'stl')],
      }).parts.map((part) => part.format),
    ).toEqual(['stl']);
  });

  it('rejects missing model data and mismatched byte lengths', () => {
    expect(() =>
      createViewerModelFromArtifacts({
        id: 'empty',
        name: 'Empty',
        artifacts: [],
      }),
    ).toThrow(/no 3MF, STL, or GLB/u);

    const mismatch = payload('bad', 'stl');
    mismatch.artifact = { ...mismatch.artifact, byteLength: 4 };
    expect(() =>
      createViewerModelFromArtifacts({
        id: 'bad',
        name: 'Bad',
        artifacts: [mismatch],
      }),
    ).toThrow(/metadata/u);
  });
});
