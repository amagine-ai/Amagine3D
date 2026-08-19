import { InMemoryProjectRepository } from '@amagine3d/cad-storage-opfs';
import { describe, expect, it, vi } from 'vitest';

import {
  BUNDLED_POMODORO_PROJECT_ID,
  BUNDLED_POMODORO_RUN_ID,
  bundledPomodoroPreviewFileId,
  bundledPomodoroProjectName,
  compareProjectsWithPomodoroLast,
  ensureBundledPomodoroProject,
  isBundledPomodoroProject,
  loadBundledPomodoroViewerModel,
  selectInitialProjectId,
} from './bundled-pomodoro-project';

const FIXED_NOW = new Date('2026-08-19T08:00:00.000Z');
const manifest = {
  schemaVersion: 1,
  name: 'Pomodoro fixture',
  units: 'mm',
  colors: {
    shell: '#EEEDE7',
    accent: '#F05A35',
  },
  parts: {
    housing: [{ file: 'case.stl', color: '#EEEDE7' }],
    button: [{ file: 'button.stl', color: '#F05A35' }],
  },
  exploded_offsets_mm: {
    housing: [0, 0, 0],
    button: [0, 0, 50],
  },
  animation_order: ['assembled', 'explode', 'hold', 'reassemble', 'rotate'],
  assets: [
    { kind: 'model-3mf', fileName: 'Amagine3D-Pomodoro-Timer.3mf' },
    { kind: 'step', fileName: 'pomodoro.step' },
    { kind: 'stl', fileName: 'case.stl' },
    { kind: 'stl', fileName: 'button.stl' },
  ],
};

function bundledFetch() {
  const files = new Map<string, BodyInit>([
    ['/showcase/amagine3d-pomodoro/manifest.json', JSON.stringify(manifest)],
    ['/showcase/amagine3d-pomodoro/Amagine3D-Pomodoro-Timer.3mf', '3MF'],
    ['/showcase/amagine3d-pomodoro/pomodoro.step', 'STEP'],
    ['/showcase/amagine3d-pomodoro/case.stl', 'solid case'],
    ['/showcase/amagine3d-pomodoro/button.stl', 'solid button'],
  ]);
  return vi.fn(async (url: string) => {
    const body = files.get(url);
    return body === undefined
      ? new Response(null, { status: 404 })
      : new Response(body, {
          status: 200,
          headers: {
            'Content-Type': url.endsWith('.json')
              ? 'application/json'
              : 'application/octet-stream',
          },
        });
  });
}

describe('bundled Pomodoro project', () => {
  it('seeds the complete immutable showcase run once', async () => {
    const repository = new InMemoryProjectRepository();
    const fetchAsset = bundledFetch();

    await expect(
      ensureBundledPomodoroProject({
        repository,
        fetchAsset,
        now: () => FIXED_NOW,
      }),
    ).resolves.toEqual({ status: 'created' });

    const project = await repository.getProject(BUNDLED_POMODORO_PROJECT_ID);
    expect(project).toMatchObject({
      id: 'amagine3d-pomodoro',
      name: 'Amagine3D Pomodoro Timer',
      currentRunId: BUNDLED_POMODORO_RUN_ID,
      revision: 1,
    });
    const stored = await repository.getRun(
      BUNDLED_POMODORO_PROJECT_ID,
      BUNDLED_POMODORO_RUN_ID,
    );
    expect(stored?.run.status).toBe('succeeded');
    expect(stored?.artifacts.map(({ metadata }) => metadata.kind)).toEqual([
      'model-3mf',
      'step',
      'stl',
      'stl',
    ]);
    expect(stored?.artifacts[0]?.metadata.fileName).toBe('model.3mf');
    expect(
      stored?.artifacts.every(({ metadata }) => metadata.sha256.length === 64),
    ).toBe(true);

    const callsAfterCreation = fetchAsset.mock.calls.length;
    await expect(
      ensureBundledPomodoroProject({ repository, fetchAsset }),
    ).resolves.toEqual({ status: 'existing' });
    expect(fetchAsset).toHaveBeenCalledTimes(callsAfterCreation);
  });

  it('leaves OPFS untouched while the bundled assets are not present', async () => {
    const repository = new InMemoryProjectRepository();

    await expect(
      ensureBundledPomodoroProject({
        repository,
        fetchAsset: async () => new Response(null, { status: 404 }),
      }),
    ).resolves.toEqual({ status: 'unavailable' });
    await expect(repository.listProjects()).resolves.toEqual([]);
  });

  it('repairs a stale bundled project whose previous run is gone', async () => {
    const repository = new InMemoryProjectRepository();
    await repository.createProject({
      project: {
        schemaVersion: 1,
        id: BUNDLED_POMODORO_PROJECT_ID,
        name: 'Amagine3D Pomodoro Timer',
        createdAt: FIXED_NOW.toISOString(),
        updatedAt: FIXED_NOW.toISOString(),
        revision: 0,
        currentRunId: 'amagine3d-pomodoro-showcase-v2',
      },
      messages: { schemaVersion: 1, messages: [] },
    });

    await expect(
      ensureBundledPomodoroProject({
        repository,
        fetchAsset: bundledFetch(),
        now: () => FIXED_NOW,
      }),
    ).resolves.toEqual({ status: 'created' });
    await expect(
      repository.getProject(BUNDLED_POMODORO_PROJECT_ID),
    ).resolves.toMatchObject({ currentRunId: BUNDLED_POMODORO_RUN_ID });
    await expect(
      repository.getRun(BUNDLED_POMODORO_PROJECT_ID, BUNDLED_POMODORO_RUN_ID),
    ).resolves.toBeDefined();
  });

  it('does not reuse a cached missing manifest response', async () => {
    const repository = new InMemoryProjectRepository();
    const fetchAsset = bundledFetch();

    await ensureBundledPomodoroProject({ repository, fetchAsset });

    expect(fetchAsset).toHaveBeenCalledWith(
      '/showcase/amagine3d-pomodoro/manifest.json',
      { cache: 'no-store' },
    );
    expect(fetchAsset).toHaveBeenCalledWith(
      '/showcase/amagine3d-pomodoro/Amagine3D-Pomodoro-Timer.3mf',
      { cache: 'force-cache' },
    );
  });

  it('builds the assembled and exploded preview from manifest parts', async () => {
    const repository = new InMemoryProjectRepository();
    const fetchAsset = bundledFetch();
    await ensureBundledPomodoroProject({
      repository,
      fetchAsset,
      now: () => FIXED_NOW,
    });

    const model = await loadBundledPomodoroViewerModel({
      repository,
      fetchAsset,
    });
    expect(model).toMatchObject({
      name: 'Pomodoro fixture',
      layout: 'assembled',
      parts: [
        {
          name: 'model.3mf',
          format: '3mf',
        },
      ],
      separatedParts: [
        {
          name: 'case.stl',
          explodedOffset: [0, 0, 0],
          region: { name: 'housing', hex: '#EEEDE7' },
        },
        {
          name: 'button.stl',
          explodedOffset: [0, 0, 50],
          region: { name: 'button', hex: '#F05A35' },
        },
      ],
    });
  });

  it('provides localized protected-project identity', () => {
    expect(bundledPomodoroProjectName('zh')).toBe('Amagine3D 番茄钟');
    expect(bundledPomodoroProjectName('en')).toBe('Amagine3D Pomodoro Timer');
    expect(isBundledPomodoroProject(BUNDLED_POMODORO_PROJECT_ID)).toBe(true);
    expect(isBundledPomodoroProject('user-project')).toBe(false);
  });

  it('selects the bundled model.3mf artifact as the project preview', () => {
    expect(
      bundledPomodoroPreviewFileId([
        { metadata: { id: 'step', kind: 'step' } },
        { metadata: { id: 'pomodoro-3mf', kind: 'model-3mf' } },
        { metadata: { id: 'shell', kind: 'stl' } },
      ]),
    ).toBe('artifact:pomodoro-3mf');
    expect(bundledPomodoroPreviewFileId([])).toBe('model:preview');
  });

  it('sorts the bundled project after user projects', () => {
    expect(
      ['z-user', BUNDLED_POMODORO_PROJECT_ID, 'a-user'].sort(
        compareProjectsWithPomodoroLast,
      ),
    ).toEqual(['a-user', 'z-user', BUNDLED_POMODORO_PROJECT_ID]);
  });

  it('opens the showcase first only when no user project exists', () => {
    const bundled = {
      projectId: BUNDLED_POMODORO_PROJECT_ID,
      updatedAt: '2026-08-19T08:00:00.000Z',
    };
    expect(selectInitialProjectId([bundled])).toBe(BUNDLED_POMODORO_PROJECT_ID);
    expect(selectInitialProjectId([])).toBeUndefined();
    expect(
      selectInitialProjectId([
        bundled,
        {
          projectId: 'older-user-project',
          updatedAt: '2026-08-17T08:00:00.000Z',
        },
        {
          projectId: 'latest-user-project',
          updatedAt: '2026-08-18T08:00:00.000Z',
        },
      ]),
    ).toBe('latest-user-project');
  });
});
