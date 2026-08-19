'use client';

import { SCHEMA_VERSION, type Artifact } from '@amagine3d/cad-protocol';
import {
  OpfsProjectRepository,
  type BinaryArtifact,
  type ProjectRepository,
} from '@amagine3d/cad-storage-opfs';
import type { ViewerModel, ViewerVector } from '@amagine3d/cad-viewer';

export const BUNDLED_POMODORO_PROJECT_ID = 'amagine3d-pomodoro';
export const BUNDLED_POMODORO_RUN_ID = 'amagine3d-pomodoro-showcase-v3';

const BUNDLED_POMODORO_PROJECT_NAME = 'Amagine3D Pomodoro Timer';
const MANIFEST_URL = '/showcase/amagine3d-pomodoro/manifest.json';
const ASSET_ROOT = '/showcase/amagine3d-pomodoro';
const SAFE_FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/u;

type BundledAssetKind = 'model-3mf' | 'step' | 'stl';

type BundledManifestPartEntry = {
  file: string;
  color: string;
};

type BundledProjectManifest = {
  schemaVersion: 1;
  name?: string;
  units?: 'mm';
  colors?: Readonly<Record<string, string>>;
  parts?: Readonly<Record<string, readonly BundledManifestPartEntry[]>>;
  exploded_offsets_mm?: Readonly<Record<string, ViewerVector>>;
  animation_order?: readonly string[];
  assets: Array<{
    kind: BundledAssetKind;
    fileName: string;
  }>;
};

type FetchResponse = Pick<Response, 'arrayBuffer' | 'json' | 'ok' | 'status'>;

type EnsureBundledProjectOptions = {
  repository?: ProjectRepository;
  fetchAsset?: (url: string, init?: RequestInit) => Promise<FetchResponse>;
  now?: () => Date;
};

export type BundledProjectInitialization = {
  status: 'created' | 'existing' | 'unavailable';
};

let defaultInitialization: Promise<BundledProjectInitialization> | undefined;

export function isBundledPomodoroProject(projectId: string | undefined) {
  return projectId === BUNDLED_POMODORO_PROJECT_ID;
}

export function compareProjectsWithPomodoroLast(
  leftProjectId: string,
  rightProjectId: string,
): number {
  const leftIsPomodoro = isBundledPomodoroProject(leftProjectId);
  const rightIsPomodoro = isBundledPomodoroProject(rightProjectId);
  if (leftIsPomodoro !== rightIsPomodoro) return leftIsPomodoro ? 1 : -1;
  return leftProjectId.localeCompare(rightProjectId);
}

export function selectInitialProjectId(
  records: readonly { projectId: string; updatedAt: string }[],
): string | undefined {
  const latestUserProject = records
    .filter(({ projectId }) => !isBundledPomodoroProject(projectId))
    .sort(
      (left, right) =>
        right.updatedAt.localeCompare(left.updatedAt) ||
        left.projectId.localeCompare(right.projectId),
    )[0];
  if (latestUserProject !== undefined) return latestUserProject.projectId;
  return records.find(({ projectId }) => isBundledPomodoroProject(projectId))
    ?.projectId;
}

export function bundledPomodoroProjectName(language: 'en' | 'zh'): string {
  return language === 'zh' ? 'Amagine3D 番茄钟' : BUNDLED_POMODORO_PROJECT_NAME;
}

export function bundledPomodoroPreviewFileId(
  artifacts: readonly {
    metadata: Pick<Artifact, 'id' | 'kind'>;
  }[],
): string {
  const modelThreeMf = artifacts.find(
    ({ metadata }) => metadata.kind === 'model-3mf',
  );
  return modelThreeMf === undefined
    ? 'model:preview'
    : `artifact:${modelThreeMf.metadata.id}`;
}

function isBundledAssetKind(value: unknown): value is BundledAssetKind {
  return value === 'model-3mf' || value === 'step' || value === 'stl';
}

function expectedExtension(kind: BundledAssetKind): string {
  switch (kind) {
    case 'model-3mf':
      return '.3mf';
    case 'step':
      return '.step';
    case 'stl':
      return '.stl';
  }
}

function parseManifest(input: unknown): BundledProjectManifest {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) {
    throw new Error('The bundled Pomodoro manifest must be an object.');
  }
  const value = input as Record<string, unknown>;
  if (value.schemaVersion !== 1 || !Array.isArray(value.assets)) {
    throw new Error('The bundled Pomodoro manifest is not version 1.');
  }
  const assets = value.assets.map((asset, index) => {
    if (typeof asset !== 'object' || asset === null || Array.isArray(asset)) {
      throw new Error(`Bundled Pomodoro asset ${String(index)} is invalid.`);
    }
    const entry = asset as Record<string, unknown>;
    if (
      !isBundledAssetKind(entry.kind) ||
      typeof entry.fileName !== 'string' ||
      !SAFE_FILE_NAME.test(entry.fileName) ||
      !entry.fileName.toLowerCase().endsWith(expectedExtension(entry.kind))
    ) {
      throw new Error(`Bundled Pomodoro asset ${String(index)} is invalid.`);
    }
    return { kind: entry.kind, fileName: entry.fileName };
  });
  const kinds = new Set(assets.map(({ kind }) => kind));
  if (
    assets.filter(({ kind }) => kind === 'model-3mf').length !== 1 ||
    !kinds.has('step') ||
    !kinds.has('stl')
  ) {
    throw new Error(
      'The bundled Pomodoro project requires one 3MF, at least one STEP, and at least one STL.',
    );
  }
  if (new Set(assets.map(({ fileName }) => fileName)).size !== assets.length) {
    throw new Error('Bundled Pomodoro asset file names must be unique.');
  }
  if (value.parts === undefined) return { schemaVersion: 1, assets };
  if (
    value.units !== 'mm' ||
    typeof value.name !== 'string' ||
    value.name.trim().length === 0 ||
    value.parts === null ||
    typeof value.parts !== 'object' ||
    Array.isArray(value.parts) ||
    value.exploded_offsets_mm === null ||
    typeof value.exploded_offsets_mm !== 'object' ||
    Array.isArray(value.exploded_offsets_mm)
  ) {
    throw new Error('The bundled Pomodoro logical-parts manifest is invalid.');
  }
  const stlAssets = new Set(
    assets.filter(({ kind }) => kind === 'stl').map(({ fileName }) => fileName),
  );
  const usedStls = new Set<string>();
  const parts: Record<string, BundledManifestPartEntry[]> = {};
  const offsets: Record<string, ViewerVector> = {};
  for (const [partName, rawEntries] of Object.entries(value.parts)) {
    if (
      partName.trim().length === 0 ||
      partName.length > 120 ||
      !Array.isArray(rawEntries) ||
      rawEntries.length === 0
    ) {
      throw new Error(`Bundled Pomodoro logical part ${partName} is invalid.`);
    }
    parts[partName] = rawEntries.map((rawEntry) => {
      if (
        rawEntry === null ||
        typeof rawEntry !== 'object' ||
        Array.isArray(rawEntry)
      ) {
        throw new Error(
          `Bundled Pomodoro logical part ${partName} has an invalid entry.`,
        );
      }
      const entry = rawEntry as Record<string, unknown>;
      if (
        typeof entry.file !== 'string' ||
        !SAFE_FILE_NAME.test(entry.file) ||
        !stlAssets.has(entry.file) ||
        usedStls.has(entry.file) ||
        typeof entry.color !== 'string' ||
        !/^#[0-9A-Fa-f]{6}$/u.test(entry.color)
      ) {
        throw new Error(
          `Bundled Pomodoro logical part ${partName} references an invalid STL.`,
        );
      }
      usedStls.add(entry.file);
      return { file: entry.file, color: entry.color };
    });
    const rawOffset = (value.exploded_offsets_mm as Record<string, unknown>)[
      partName
    ];
    if (
      !Array.isArray(rawOffset) ||
      rawOffset.length !== 3 ||
      rawOffset.some(
        (component) =>
          typeof component !== 'number' || !Number.isFinite(component),
      )
    ) {
      throw new Error(
        `Bundled Pomodoro logical part ${partName} has no valid exploded offset.`,
      );
    }
    offsets[partName] = [
      rawOffset[0],
      rawOffset[1],
      rawOffset[2],
    ] as ViewerVector;
  }
  if (usedStls.size !== stlAssets.size) {
    throw new Error(
      'The bundled Pomodoro logical parts must reference every split STL exactly once.',
    );
  }
  const colors = value.colors;
  if (
    colors === null ||
    typeof colors !== 'object' ||
    Array.isArray(colors) ||
    Object.values(colors).some(
      (color) => typeof color !== 'string' || !/^#[0-9A-Fa-f]{6}$/u.test(color),
    )
  ) {
    throw new Error('The bundled Pomodoro color palette is invalid.');
  }
  const animationOrder = value.animation_order;
  if (
    !Array.isArray(animationOrder) ||
    animationOrder.length === 0 ||
    animationOrder.some(
      (stage) => typeof stage !== 'string' || stage.trim().length === 0,
    )
  ) {
    throw new Error('The bundled Pomodoro animation order is invalid.');
  }
  return {
    schemaVersion: 1,
    name: value.name,
    units: 'mm',
    colors: colors as Record<string, string>,
    parts,
    exploded_offsets_mm: offsets,
    animation_order: animationOrder as string[],
    assets,
  };
}

export async function loadBundledPomodoroViewerModel(
  options: Pick<EnsureBundledProjectOptions, 'fetchAsset' | 'repository'> = {},
): Promise<ViewerModel | undefined> {
  const repository = options.repository ?? (await OpfsProjectRepository.open());
  const project = await repository.getProject(BUNDLED_POMODORO_PROJECT_ID);
  if (project?.currentRunId == null) return undefined;
  const stored = await repository.getRun(
    BUNDLED_POMODORO_PROJECT_ID,
    project.currentRunId,
  );
  if (stored === undefined) return undefined;
  const fetchAsset =
    options.fetchAsset ??
    ((url: string, init?: RequestInit) => globalThis.fetch(url, init));
  const response = await fetchAsset(MANIFEST_URL, { cache: 'no-store' });
  if (!response.ok) return undefined;
  const manifest = parseManifest(await response.json());
  if (
    manifest.parts === undefined ||
    manifest.exploded_offsets_mm === undefined
  ) {
    return undefined;
  }
  const stls = new Map(
    stored.artifacts
      .filter(({ metadata }) => ['region-stl', 'stl'].includes(metadata.kind))
      .map((artifact) => [artifact.metadata.fileName, artifact] as const),
  );
  const palette = Object.entries(manifest.colors ?? {});
  const separatedParts: ViewerModel['parts'][number][] = [];
  for (const [logicalPart, entries] of Object.entries(manifest.parts)) {
    const explodedOffset = manifest.exploded_offsets_mm[logicalPart];
    if (explodedOffset === undefined) {
      throw new Error(
        `Bundled Pomodoro logical part ${logicalPart} has no exploded offset.`,
      );
    }
    for (const [index, entry] of entries.entries()) {
      const artifact = stls.get(entry.file);
      if (artifact === undefined) {
        throw new Error(
          `Bundled Pomodoro logical part ${logicalPart} is missing ${entry.file} in OPFS.`,
        );
      }
      const colorName =
        palette.find(
          ([, hex]) => hex.toLowerCase() === entry.color.toLowerCase(),
        )?.[0] ?? entry.color;
      separatedParts.push({
        id: artifact.metadata.id,
        name: artifact.metadata.fileName,
        format: 'stl',
        bytes: Uint8Array.from(artifact.bytes).buffer,
        explodedOffset,
        region: {
          id: `${logicalPart}-${String(index + 1)}`,
          name: logicalPart,
          colorName,
          hex: entry.color,
          features: [logicalPart],
          metadata: { logicalPart },
        },
      });
    }
  }
  const threeMf = stored.artifacts.find(
    ({ metadata }) => metadata.kind === 'model-3mf',
  );
  if (threeMf === undefined) {
    throw new Error('Bundled Pomodoro model.3mf is missing in OPFS.');
  }
  return {
    id: stored.run.id,
    name: manifest.name ?? BUNDLED_POMODORO_PROJECT_NAME,
    parts: [
      {
        id: threeMf.metadata.id,
        name: 'model.3mf',
        format: '3mf',
        bytes: Uint8Array.from(threeMf.bytes).buffer,
      },
    ],
    separatedParts,
    layout: 'assembled',
  };
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const owned = Uint8Array.from(bytes);
  const digest = await crypto.subtle.digest('SHA-256', owned.buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

function mediaType(kind: BundledAssetKind): string {
  switch (kind) {
    case 'model-3mf':
      return 'model/3mf';
    case 'step':
      return 'model/step';
    case 'stl':
      return 'model/stl';
  }
}

async function loadArtifacts(
  manifest: BundledProjectManifest,
  fetchAsset: (url: string, init?: RequestInit) => Promise<FetchResponse>,
  createdAt: string,
): Promise<BinaryArtifact[]> {
  return Promise.all(
    manifest.assets.map(async (asset, index) => {
      const response = await fetchAsset(
        `${ASSET_ROOT}/${encodeURIComponent(asset.fileName)}`,
        { cache: 'force-cache' },
      );
      if (!response.ok) {
        throw new Error(
          `Unable to load bundled Pomodoro asset ${asset.fileName}: HTTP ${String(response.status)}.`,
        );
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength === 0) {
        throw new Error(`Bundled Pomodoro asset ${asset.fileName} is empty.`);
      }
      const metadata: Artifact = {
        schemaVersion: SCHEMA_VERSION,
        id: `pomodoro-${asset.kind}-${String(index + 1)}`,
        runId: BUNDLED_POMODORO_RUN_ID,
        kind: asset.kind,
        fileName: asset.kind === 'model-3mf' ? 'model.3mf' : asset.fileName,
        mediaType: mediaType(asset.kind),
        byteLength: bytes.byteLength,
        sha256: await sha256(bytes),
        createdAt,
      };
      return { metadata, bytes };
    }),
  );
}

async function initializeBundledPomodoroProject(
  options: EnsureBundledProjectOptions,
): Promise<BundledProjectInitialization> {
  const repository = options.repository ?? (await OpfsProjectRepository.open());
  const existing = await repository.getProject(BUNDLED_POMODORO_PROJECT_ID);
  if (existing?.currentRunId === BUNDLED_POMODORO_RUN_ID) {
    const currentRun = await repository.getRun(
      BUNDLED_POMODORO_PROJECT_ID,
      BUNDLED_POMODORO_RUN_ID,
    );
    if (currentRun !== undefined) return { status: 'existing' };
  }

  const fetchAsset =
    options.fetchAsset ??
    ((url: string, init?: RequestInit) => globalThis.fetch(url, init));
  const manifestResponse = await fetchAsset(MANIFEST_URL, {
    cache: 'no-store',
  });
  if (manifestResponse.status === 404) return { status: 'unavailable' };
  if (!manifestResponse.ok) {
    throw new Error(
      `Unable to load the bundled Pomodoro manifest: HTTP ${String(manifestResponse.status)}.`,
    );
  }
  const manifest = parseManifest(await manifestResponse.json());
  const now = (options.now ?? (() => new Date()))().toISOString();
  const artifacts = await loadArtifacts(manifest, fetchAsset, now);

  let project = existing;
  if (project === undefined) {
    await repository.createProject({
      project: {
        schemaVersion: SCHEMA_VERSION,
        id: BUNDLED_POMODORO_PROJECT_ID,
        name: BUNDLED_POMODORO_PROJECT_NAME,
        createdAt: now,
        updatedAt: now,
        revision: 0,
        currentRunId: null,
      },
      messages: { schemaVersion: SCHEMA_VERSION, messages: [] },
    });
    project = await repository.getProject(BUNDLED_POMODORO_PROJECT_ID);
  }
  if (project === undefined) {
    throw new Error('The bundled Pomodoro project could not be created.');
  }

  const storedRun = await repository.getRun(
    BUNDLED_POMODORO_PROJECT_ID,
    BUNDLED_POMODORO_RUN_ID,
  );
  if (storedRun === undefined) {
    await repository.saveRun(BUNDLED_POMODORO_PROJECT_ID, {
      run: {
        schemaVersion: SCHEMA_VERSION,
        id: BUNDLED_POMODORO_RUN_ID,
        projectId: BUNDLED_POMODORO_PROJECT_ID,
        createdAt: now,
        completedAt: now,
        status: 'succeeded',
        workflowKind: 'single-color',
        workflowSelectionReason: 'Bundled Amagine3D Pomodoro Timer showcase.',
        sourceHash: null,
        workflowSnapshot: null,
        runtimeVersions: { bundledShowcase: '3' },
        artifactIds: artifacts.map(({ metadata }) => metadata.id),
        parentRunId: null,
        baseRevisionId: null,
        modelProfileId: null,
        mode: 'baseline',
        modelSnapshot: null,
      },
      events: [],
      artifacts,
    });
  }
  await repository.updateProject(
    {
      ...project,
      currentRunId: BUNDLED_POMODORO_RUN_ID,
      revision: project.revision + 1,
      updatedAt: now,
    },
    project.revision,
  );
  return { status: 'created' };
}

export async function ensureBundledPomodoroProject(
  options: EnsureBundledProjectOptions = {},
): Promise<BundledProjectInitialization> {
  if (
    options.repository !== undefined ||
    options.fetchAsset !== undefined ||
    options.now !== undefined
  ) {
    return initializeBundledPomodoroProject(options);
  }
  const initialization =
    defaultInitialization ?? initializeBundledPomodoroProject({});
  defaultInitialization = initialization;
  try {
    return await initialization;
  } finally {
    if (defaultInitialization === initialization) {
      defaultInitialization = undefined;
    }
  }
}
