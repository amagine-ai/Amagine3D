import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { preferredPreviewArtifact } from '../src/lib/artifact-selection.ts';
import type { ArtifactSummary, PreviewFormat } from '../src/types.ts';

function model(
  path: string,
  format: PreviewFormat | undefined,
  modifiedAt: string,
): ArtifactSummary {
  return {
    ...(format ? { format } : {}),
    kind: 'model',
    modifiedAt,
    name: path.split('/').at(-1) ?? path,
    path,
    size: 1,
    url: `/api/artifacts/file?path=${encodeURIComponent(path)}`,
  };
}

test('selects the combined STL for the latest single-color build', () => {
  const artifacts = [
    model('bracket.stl', 'stl', '2026-08-23T08:00:02.000Z'),
    model('bracket.step', undefined, '2026-08-23T08:00:01.000Z'),
  ];
  assert.equal(preferredPreviewArtifact(artifacts)?.path, 'bracket.stl');
});

test('selects the assembled 3MF instead of region STLs for multi-color builds', () => {
  const artifacts = [
    model('timer.step', undefined, '2026-08-23T08:00:05.000Z'),
    model('timer.3mf', '3mf', '2026-08-23T08:00:04.000Z'),
    model('timer-screen.stl', 'stl', '2026-08-23T08:00:03.000Z'),
    model('timer-housing.stl', 'stl', '2026-08-23T08:00:02.000Z'),
  ];
  assert.equal(preferredPreviewArtifact(artifacts)?.path, 'timer.3mf');
});

test('does not let an older multi-color build override a newer STL build', () => {
  const artifacts = [
    model('new-part.stl', 'stl', '2026-08-23T09:00:00.000Z'),
    model('old-part.3mf', '3mf', '2026-08-23T08:00:00.000Z'),
  ];
  assert.equal(preferredPreviewArtifact(artifacts)?.path, 'new-part.stl');
});

test('prefers 3MF when combined outputs share the same timestamp', () => {
  const artifacts = [
    model('timer.stl', 'stl', '2026-08-23T08:00:00.000Z'),
    model('timer.3mf', '3mf', '2026-08-23T08:00:00.000Z'),
  ];
  assert.equal(preferredPreviewArtifact(artifacts)?.path, 'timer.3mf');
});

test('honors the explicit preview of a bundled project', () => {
  const featured = {
    ...model('focus-bar-logical-assembly.3mf', '3mf', '2026-01-01T00:00:00.000Z'),
    featured: true,
  };
  const artifacts = [
    model('timer-knob-orange.stl', 'stl', '2026-08-23T08:00:00.000Z'),
    featured,
  ];
  assert.equal(
    preferredPreviewArtifact(artifacts)?.path,
    'focus-bar-logical-assembly.3mf',
  );
});
