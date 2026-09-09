import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  defaultPreviewArtifact,
  fileSectionArtifacts,
  preferredDisplayPreviewArtifact,
  preferredPrintPreviewArtifact,
  printPreviewForSelection,
} from '../src/lib/artifact-selection.ts';
import type { ArtifactSummary, PreviewFormat } from '../src/types.ts';

function artifact(
  path: string,
  kind: ArtifactSummary['kind'],
  modifiedAt = '2026-08-23T08:00:00.000Z',
): ArtifactSummary {
  return {
    kind,
    modifiedAt,
    name: path.split('/').at(-1) ?? path,
    path,
    size: 1,
    url: `/api/artifacts/file?path=${encodeURIComponent(path)}`,
  };
}

function model(
  path: string,
  format: PreviewFormat | undefined,
  modifiedAt: string,
): ArtifactSummary {
  return {
    ...artifact(path, 'model', modifiedAt),
    ...(format ? { format } : {}),
  };
}

test('keeps plate selection and hides individual parts and obsolete plates', () => {
  const display = { ...model('pair-display.glb', 'glb', '2026-09-09'), primary: true, modelId: 'pair', buildId: 'new' };
  const plates = ['01', '02'].flatMap((plateId) => (['3mf', 'stl'] as const).map((format) => ({
    ...model(`pair-plate-${plateId}.${format}`, format, `2026-09-09T00:00:0${plateId === '02' ? 2 : 1}Z`),
    primary: true, modelId: 'pair', buildId: 'new', plateId,
  })));
  const artifacts = [
    model('pair-part-a.stl', 'stl', '2026-09-10'),
    model('pair-plate-03.stl', 'stl', '2026-09-10'),
    ...plates.slice().reverse(), display,
  ];
  assert.deepEqual(fileSectionArtifacts(artifacts).map(({ path }) => path), [display.path, ...plates.map(({ path }) => path)]);
  assert.equal(preferredPrintPreviewArtifact(artifacts)?.path, plates[0]!.path);
  for (const selected of plates) {
    assert.equal(printPreviewForSelection(artifacts, selected, { primaryPreviewPath: plates[0]!.path }), selected);
  }
  assert.equal(printPreviewForSelection(artifacts, display, { primaryPreviewPath: plates[0]!.path }), plates[0]);
});

test('selects the display GLB over the top-level STL for single-color builds', () => {
  const artifacts = [
    model('bracket.stl', 'stl', '2026-08-23T08:00:02.000Z'),
    model('bracket-display.glb', 'glb', '2026-08-23T08:00:03.000Z'),
  ];
  assert.equal(
    preferredDisplayPreviewArtifact(artifacts)?.path,
    'bracket-display.glb',
  );
  assert.equal(defaultPreviewArtifact(artifacts)?.path, 'bracket-display.glb');
});

test('selects the display GLB over the 3MF print package for color builds', () => {
  const artifacts = [
    model('timer-display.glb', 'glb', '2026-08-23T08:00:05.000Z'),
    model('timer.3mf', '3mf', '2026-08-23T08:00:04.000Z'),
    model('timer-region-screen.stl', 'stl', '2026-08-23T08:00:03.000Z'),
    model('timer-region-housing.stl', 'stl', '2026-08-23T08:00:02.000Z'),
  ];
  assert.equal(
    preferredDisplayPreviewArtifact(artifacts)?.path,
    'timer-display.glb',
  );
  assert.equal(preferredPrintPreviewArtifact(artifacts)?.path, 'timer.3mf');
});

test('honors a featured display GLB as the visible model', () => {
  const artifacts = [
    model('timer.3mf', '3mf', '2026-08-23T08:00:04.000Z'),
    {
      ...model('timer-display.glb', 'glb', '2026-08-23T08:00:03.000Z'),
      featured: true,
    },
  ];
  assert.equal(
    preferredDisplayPreviewArtifact(artifacts)?.path,
    'timer-display.glb',
  );
});

test('does not let an older multi-color print package override a newer STL build', () => {
  const artifacts = [
    model('new-part.stl', 'stl', '2026-08-23T09:00:00.000Z'),
    model('old-part.3mf', '3mf', '2026-08-23T08:00:00.000Z'),
  ];
  assert.equal(preferredPrintPreviewArtifact(artifacts)?.path, 'new-part.stl');
});

test('prefers 3MF when top-level print outputs share the same timestamp', () => {
  const artifacts = [
    model('timer.stl', 'stl', '2026-08-23T08:00:00.000Z'),
    model('timer.3mf', '3mf', '2026-08-23T08:00:00.000Z'),
  ];
  assert.equal(preferredPrintPreviewArtifact(artifacts)?.path, 'timer.3mf');
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
    defaultPreviewArtifact(artifacts)?.path,
    'focus-bar-logical-assembly.3mf',
  );
});

test('shows GLB, 3MF, and only the matching top-level STL in the file section', () => {
  const artifacts = [
    artifact('part.py', 'source'),
    {
      ...model('part-display.glb', 'glb', '2026-08-23T08:00:05.000Z'),
      primary: true,
    },
    {
      ...model('part.3mf', '3mf', '2026-08-23T08:00:04.500Z'),
      primary: true,
    },
    {
      ...model('part.stl', 'stl', '2026-08-23T08:00:04.250Z'),
      primary: true,
    },
    model('part-shell.stl', 'stl', '2026-08-23T08:00:04.100Z'),
    model('part-assemble.step', undefined, '2026-08-23T08:00:04.000Z'),
    artifact('preview.PNG', 'image'),
    artifact('reference.webp', 'image'),
    artifact('part_report.json', 'report'),
  ];
  assert.deepEqual(
    fileSectionArtifacts(artifacts).map(({ path }) => path),
    ['part-display.glb', 'part.3mf', 'part.stl'],
  );
});

test('pins the preferred visible model at the top of the file section', () => {
  const preferred = {
    ...model('shell_case-display.glb', 'glb', '2026-08-23T08:00:01.000Z'),
    featured: true,
    primary: true,
  };
  const artifacts = [
    {
      ...model('shell_case.stl', 'stl', '2026-08-23T08:00:05.000Z'),
      primary: true,
    },
    artifact('preview.png', 'image', '2026-08-23T08:00:04.000Z'),
    model('shell_case-top-lid.stl', 'stl', '2026-08-23T08:00:03.000Z'),
    preferred,
  ];
  assert.deepEqual(
    fileSectionArtifacts(artifacts).map(({ path }) => path),
    [
      'shell_case-display.glb',
      'shell_case.stl',
    ],
  );
});

test('uses valid-build primary metadata to hide orphaned model revisions', () => {
  const artifacts = [
    {
      ...model('ninekey-display.glb', 'glb', '2026-08-23T09:00:00.000Z'),
      featured: true,
      primary: true,
    },
    {
      ...model('ninekey.3mf', '3mf', '2026-08-23T09:00:00.000Z'),
      primary: true,
    },
    {
      ...model('ninekey.stl', 'stl', '2026-08-23T09:00:00.000Z'),
      primary: true,
    },
    model('relocated/a.glb', 'glb', '2026-08-23T08:00:00.000Z'),
    model('relocated/b.3mf', '3mf', '2026-08-23T08:00:00.000Z'),
    model('relocated/c.stl', 'stl', '2026-08-23T08:00:00.000Z'),
  ];
  assert.deepEqual(
    fileSectionArtifacts(artifacts).map(({ path }) => path),
    ['ninekey-display.glb', 'ninekey.3mf', 'ninekey.stl'],
  );
});
