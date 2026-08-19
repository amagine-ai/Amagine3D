'use client';

import {
  CadRunCoordinator,
  createSourceDiff,
  discoverParameterSet,
  ensureParameterCouplings,
  getCadWorkflowProfile,
  parameterOverrides,
  writeParametersToSource,
  type SourceDiffLine,
} from '@amagine3d/cad-agent';
import {
  CAD_RUNTIME_MANIFEST,
  createBrowserCadExecutor,
  validateCadSource,
} from '@amagine3d/cad-execution-browser';
import type { BrowserCadExecutor } from '@amagine3d/cad-execution-browser';
import {
  CadDomainError,
  SCHEMA_VERSION,
  attachmentSchema,
  cadAgentToolNameSchema,
  toJsonValue,
  type Artifact,
  type Attachment,
  type CadBuildQaTargets,
  type CadExecutionResult,
  type CadRun,
  type CadToolOutput,
  type CadWorkflowPreference,
  type DesignBrief,
  type JsonValue,
  type ParameterSet,
  type ModelProfileSnapshot,
  type ModelProfileSettings,
  type RunMode,
  type ProjectRevision,
  type RepairContext,
  type ResearchPacket,
  type VisualReviewInput,
} from '@amagine3d/cad-protocol';
import {
  OpfsProjectRepository,
  persistSuccessfulExecution,
  toPersistentExecutionArtifacts,
  type ProjectRepository,
} from '@amagine3d/cad-storage-opfs';
import type { ViewerModel } from '@amagine3d/cad-viewer';
import { validateImageInputs, type ImageInput } from '@amagine3d/cad-agent';

const LEGACY_PROJECT_ID = 'p6-cad-agent';
const DEFAULT_PROJECT_NAME = 'Hardware enclosure';

export type CadWorkspaceArtifact = {
  metadata: Artifact;
  bytes: Uint8Array;
};

export type CadWorkspaceSnapshot = {
  projectId: string;
  projectName: string;
  runId: string;
  workflowKind: 'single-color' | 'multi-color';
  workflowSelectionReason: string;
  source?: string;
  sourceHash?: string;
  parameters?: ParameterSet;
  revisions: ProjectRevision[];
  artifacts: CadWorkspaceArtifact[];
  research?: ResearchPacket;
  designBrief?: DesignBrief;
  buildReport?: JsonValue;
  qaReport?: CadExecutionResult['qaReport'];
  model?: ViewerModel;
};

export type SourceWritebackPreview = {
  source: string;
  diff: SourceDiffLine[];
};

type SourceSnapshot = {
  source: string;
  parameters: ParameterSet;
  revisionId?: string;
};

function changedSourceLines(before: string, after: string) {
  const beforeLines = before.split('\n');
  const afterLines = after.split('\n');
  let prefix = 0;
  while (
    prefix < beforeLines.length &&
    prefix < afterLines.length &&
    beforeLines[prefix] === afterLines[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < beforeLines.length - prefix &&
    suffix < afterLines.length - prefix &&
    beforeLines[beforeLines.length - suffix - 1] ===
      afterLines[afterLines.length - suffix - 1]
  ) {
    suffix += 1;
  }
  const removedLineCount = beforeLines.length - prefix - suffix;
  const addedLineCount = afterLines.length - prefix - suffix;
  return {
    addedLineCount,
    removedLineCount,
    changedLineRanges:
      addedLineCount === 0 && removedLineCount === 0
        ? []
        : [
            {
              startLine: prefix + 1,
              endLine: Math.max(prefix + 1, prefix + addedLineCount),
            },
          ],
  };
}

function changedParameterNames(
  baseline: ParameterSet,
  candidate: ParameterSet,
): string[] {
  const values = new Map(
    baseline.parameters.map((parameter) => [
      parameter.name,
      JSON.stringify(parameter.value),
    ]),
  );
  return candidate.parameters
    .filter(
      (parameter) =>
        values.get(parameter.name) !== JSON.stringify(parameter.value),
    )
    .map((parameter) => parameter.name)
    .sort();
}

function collectBodyIds(value: JsonValue | undefined, ids: Set<string>): void {
  if (value === undefined || value === null) return;
  if (Array.isArray(value)) {
    for (const item of value) collectBodyIds(item, ids);
    return;
  }
  if (typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (
      ['bodyId', 'movingBodyId', 'stationaryBodyId', 'regionId'].includes(
        key,
      ) &&
      typeof item === 'string'
    ) {
      ids.add(item);
    }
    collectBodyIds(item, ids);
  }
}

function repairFeatureOwnership(
  result: CadExecutionResult | undefined,
  brief: DesignBrief | undefined,
  constraintIds: readonly string[],
): Array<{ bodyId: string; constraintIds: string[] }> {
  const owners = new Map<string, Set<string>>();
  const own = (bodyId: string, constraintId: string) => {
    const constraints = owners.get(bodyId) ?? new Set<string>();
    constraints.add(constraintId);
    owners.set(bodyId, constraints);
  };
  const failed = new Set(constraintIds);
  const reports = result?.qaReport;
  const checks = [
    ...(reports?.checks ?? []),
    ...(reports?.regionReports?.flatMap((region) =>
      region.checks.map((check) => ({ ...check, regionId: region.regionId })),
    ) ?? []),
    ...(reports?.mechanismReports?.flatMap((report) => report.checks) ?? []),
    ...(reports?.overlapCheck === undefined ? [] : [reports.overlapCheck]),
  ];
  for (const check of checks) {
    if (!failed.has(check.id)) continue;
    const bodyIds = new Set<string>();
    collectBodyIds(check.actual, bodyIds);
    if ('regionId' in check && typeof check.regionId === 'string') {
      bodyIds.add(check.regionId);
    }
    for (const bodyId of bodyIds) own(bodyId, check.id);
  }
  for (const mechanism of brief?.mechanisms ?? []) {
    const mechanismConstraints = constraintIds.filter((id) =>
      id.startsWith(`mechanism-${mechanism.id}-`),
    );
    for (const constraintId of mechanismConstraints) {
      for (const bodyId of [
        ...mechanism.movingBodyIds,
        ...mechanism.stationaryBodyIds,
      ]) {
        own(bodyId, constraintId);
      }
    }
  }
  return [...owners.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([bodyId, ids]) => ({
      bodyId,
      constraintIds: [...ids].sort(),
    }));
}

function geometryArtifactChanges(
  baseline: CadExecutionResult | undefined,
  candidate: CadExecutionResult | undefined,
): Array<{
  kind: Artifact['kind'];
  fileName: string;
  baselineSha256?: string;
  candidateSha256?: string;
}> {
  const geometryKinds = new Set<Artifact['kind']>([
    'model-3mf',
    'preview-glb',
    'region-stl',
    'step',
    'stl',
  ]);
  const baselineArtifacts = new Map(
    baseline?.artifacts
      .filter(({ artifact }) => geometryKinds.has(artifact.kind))
      .map(({ artifact }) => [
        `${artifact.kind}:${artifact.fileName}`,
        artifact,
      ]) ?? [],
  );
  const candidateArtifacts = new Map(
    candidate?.artifacts
      .filter(({ artifact }) => geometryKinds.has(artifact.kind))
      .map(({ artifact }) => [
        `${artifact.kind}:${artifact.fileName}`,
        artifact,
      ]) ?? [],
  );
  return [
    ...new Set([...baselineArtifacts.keys(), ...candidateArtifacts.keys()]),
  ]
    .sort()
    .flatMap((key) => {
      const before = baselineArtifacts.get(key);
      const after = candidateArtifacts.get(key);
      if (before?.sha256 === after?.sha256) return [];
      const artifact = after ?? before;
      if (artifact === undefined) return [];
      return [
        {
          kind: artifact.kind,
          fileName: artifact.fileName,
          ...(before === undefined ? {} : { baselineSha256: before.sha256 }),
          ...(after === undefined ? {} : { candidateSha256: after.sha256 }),
        },
      ];
    });
}

function viewerModelFromResult(
  runId: string,
  workflowKind: 'single-color' | 'multi-color',
  artifacts: Array<{
    metadata: Artifact;
    bytes: Uint8Array;
  }>,
  brief?: DesignBrief,
): ViewerModel | undefined {
  const regions = new Map(
    brief?.colorRegionPlan?.regions.flatMap((region) => [
      [region.id, region] as const,
      [region.name, region] as const,
    ]) ?? [],
  );
  const hasRegionStls = artifacts.some(
    ({ metadata }) => metadata.kind === 'region-stl',
  );
  const preferredThreeMf = hasRegionStls
    ? undefined
    : artifacts.find(({ metadata }) => metadata.kind === 'model-3mf');
  const previewArtifacts =
    preferredThreeMf === undefined ? artifacts : [preferredThreeMf];
  const parts: ViewerModel['parts'][number][] = previewArtifacts.flatMap(
    ({ metadata, bytes }) => {
      if (
        metadata.kind !== 'model-3mf' &&
        metadata.kind !== 'stl' &&
        metadata.kind !== 'region-stl'
      ) {
        return [];
      }
      const region =
        metadata.regionName === undefined
          ? undefined
          : regions.get(metadata.regionName);
      return [
        {
          id: metadata.id,
          name: metadata.fileName,
          format: metadata.kind === 'model-3mf' ? '3mf' : 'stl',
          bytes: Uint8Array.from(bytes).buffer,
          ...(region === undefined
            ? {}
            : {
                region: {
                  id: region.id,
                  name: region.name,
                  colorName: region.colorName,
                  hex: region.hex,
                  features: region.features,
                  ...(region.filament === undefined
                    ? {}
                    : { filament: region.filament }),
                },
              }),
        },
      ];
    },
  );
  if (parts.length === 0) return undefined;
  return {
    id: runId,
    name: `${workflowKind} enclosure`,
    parts,
    layout: 'assembled',
  };
}

export type RestoredCadAgentProject = {
  projectId: string;
  runId: string;
  workflowKind: 'single-color' | 'multi-color';
  phase: 'completed' | 'failed' | 'cancelled';
  failureReason?: string;
  messages: JsonValue[];
  research?: ResearchPacket;
  model?: ViewerModel;
  workspace: CadWorkspaceSnapshot;
};

export type CadProjectRecord = {
  projectId: string;
  runId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  status: CadRun['status'];
  workflowKind: 'single-color' | 'multi-color';
};

function titleFromMessages(messages: JsonValue[]): string | undefined {
  for (const value of messages) {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      continue;
    }
    const message = value as Record<string, unknown>;
    if (message.role !== 'user') continue;
    if (typeof message.text === 'string' && message.text.trim().length > 0) {
      return message.text.trim();
    }
    if (!Array.isArray(message.parts)) continue;
    const text = message.parts.find(
      (part): part is Record<string, unknown> =>
        typeof part === 'object' &&
        part !== null &&
        !Array.isArray(part) &&
        (part as Record<string, unknown>).type === 'text' &&
        typeof (part as Record<string, unknown>).text === 'string',
    );
    if (typeof text?.text === 'string' && text.text.trim().length > 0) {
      return text.text.trim();
    }
  }
  return undefined;
}

function fallbackProjectTitle(run: CadRun): string {
  const kind = run.workflowKind === 'multi-color' ? '多色' : '单色';
  return `${kind} CAD 执行 · ${run.id.slice(0, 8)}`;
}

function phaseFromRun(run: CadRun): RestoredCadAgentProject['phase'] {
  return run.status === 'succeeded'
    ? 'completed'
    : run.status === 'cancelled'
      ? 'cancelled'
      : 'failed';
}

async function loadCadAgentProjectRun(
  repository: ProjectRepository,
  projectId: string,
  runId: string,
  currentRunId: string | null,
): Promise<RestoredCadAgentProject | undefined> {
  const project = await repository.getProject(projectId);
  if (project === undefined) return undefined;
  const isCurrent = currentRunId === runId;
  const [stored, runMessages, projectMessages, revisions, parameters] =
    await Promise.all([
      repository.getRun(projectId, runId),
      repository.getRunMessages(projectId, runId),
      isCurrent
        ? repository.getMessages(projectId)
        : Promise.resolve(undefined),
      isCurrent ? repository.listRevisions(projectId) : Promise.resolve([]),
      isCurrent
        ? repository.getParameters(projectId)
        : Promise.resolve(undefined),
    ]);
  if (stored?.run.workflowKind == null) return undefined;

  const model = viewerModelFromResult(
    stored.run.id,
    stored.run.workflowKind,
    stored.artifacts,
    stored.designBrief,
  );
  const activeRevision =
    revisions.findLast(
      (revision) => revision.sourceHash === stored.run.sourceHash,
    ) ?? revisions.at(-1);
  const sourceArtifact = stored.artifacts.find(
    ({ metadata }) => metadata.kind === 'model-source',
  );
  const source =
    activeRevision?.modelSource ??
    (sourceArtifact === undefined
      ? undefined
      : new TextDecoder().decode(sourceArtifact.bytes));
  const activeSourceHash = activeRevision?.sourceHash ?? stored.run.sourceHash;
  const restoredParameters =
    activeRevision?.parameters !== null &&
    activeRevision?.parameters !== undefined
      ? activeRevision.parameters
      : parameters === undefined
        ? undefined
        : source === undefined
          ? parameters
          : ensureParameterCouplings(parameters, source);
  const workspace: CadWorkspaceSnapshot = {
    projectId: project.id,
    projectName: project.name,
    runId: stored.run.id,
    workflowKind: stored.run.workflowKind,
    workflowSelectionReason:
      stored.run.workflowSelectionReason ?? 'Stored workflow selection.',
    revisions,
    artifacts: stored.artifacts.map(({ metadata, bytes }) => ({
      metadata,
      bytes: bytes.slice(),
    })),
    ...(source === undefined ? {} : { source }),
    ...(activeSourceHash === null ? {} : { sourceHash: activeSourceHash }),
    ...(restoredParameters === undefined
      ? {}
      : { parameters: restoredParameters }),
    ...(stored.research === undefined ? {} : { research: stored.research }),
    ...(stored.designBrief === undefined
      ? {}
      : { designBrief: stored.designBrief }),
    ...(stored.buildReport === undefined
      ? {}
      : { buildReport: stored.buildReport.data }),
    ...(stored.qaReport === undefined ? {} : { qaReport: stored.qaReport }),
    ...(model === undefined ? {} : { model }),
  };
  return {
    projectId: project.id,
    runId: stored.run.id,
    workflowKind: stored.run.workflowKind,
    phase: phaseFromRun(stored.run),
    ...(stored.run.failureReason === undefined
      ? {}
      : { failureReason: stored.run.failureReason }),
    messages: runMessages?.messages ?? projectMessages?.messages ?? [],
    ...(stored.research === undefined ? {} : { research: stored.research }),
    ...(model === undefined ? {} : { model }),
    workspace,
  };
}

export async function loadLatestCadAgentProject(): Promise<
  RestoredCadAgentProject | undefined
> {
  const repository = await OpfsProjectRepository.open();
  const projects = (await repository.listProjects()).sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt),
  );
  for (const project of projects) {
    if (project.currentRunId === null) continue;
    const restored = await loadCadAgentProjectRun(
      repository,
      project.id,
      project.currentRunId,
      project.currentRunId,
    );
    if (restored !== undefined) return restored;
  }
  return undefined;
}

export async function loadCadAgentProject(
  projectId: string,
): Promise<RestoredCadAgentProject | undefined> {
  const repository = await OpfsProjectRepository.open();
  const project = await repository.getProject(projectId);
  if (project?.currentRunId == null) return undefined;
  return loadCadAgentProjectRun(
    repository,
    project.id,
    project.currentRunId,
    project.currentRunId,
  );
}

export async function listCadAgentProjects(): Promise<CadProjectRecord[]> {
  const repository = await OpfsProjectRepository.open();
  const projects = await repository.listProjects();
  const records = await Promise.all(
    projects.map(async (project): Promise<CadProjectRecord | undefined> => {
      if (project.currentRunId === null) return undefined;
      const [stored, history] = await Promise.all([
        repository.getRun(project.id, project.currentRunId),
        repository.getMessages(project.id),
      ]);
      const run = stored?.run;
      if (run?.workflowKind == null) return undefined;
      return {
        projectId: project.id,
        runId: run.id,
        title:
          titleFromMessages(history?.messages ?? []) ??
          (project.name === DEFAULT_PROJECT_NAME
            ? fallbackProjectTitle(run)
            : project.name),
        createdAt: project.createdAt,
        updatedAt: project.updatedAt,
        status: run.status,
        workflowKind: run.workflowKind,
      };
    }),
  );
  return records
    .filter((record): record is CadProjectRecord => record !== undefined)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

async function sha256Text(source: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(source),
  );
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}

export type VisualReviewDecision = {
  passed: boolean;
  summary: string;
};

export type CadAgentProjectControllerOptions = {
  projectId: string;
  projectName?: string;
  runId: string;
  userRequest: string;
  preference: CadWorkflowPreference;
  researchEnabled: boolean;
  research?: ResearchPacket;
  visualReviewConsent: 'approved' | 'declined';
  requestVisualReview: (
    input: VisualReviewInput,
    result: CadExecutionResult,
  ) => Promise<VisualReviewDecision>;
  onExecutorEvent?: ConstructorParameters<
    typeof BrowserCadExecutor
  >[0]['onEvent'];
  repository?: ProjectRepository;
  executor?: BrowserCadExecutor;
  parentRunId?: string;
  baseRevisionId?: string;
  modelProfileId?: string;
  modelSnapshot?: ModelProfileSnapshot;
  mode?: RunMode;
};

export type RestoreCadAgentProjectControllerOptions = Pick<
  CadAgentProjectControllerOptions,
  'executor' | 'onExecutorEvent' | 'repository' | 'requestVisualReview'
> & { projectId?: string };

export class CadAgentProjectController {
  readonly coordinator: CadRunCoordinator;
  readonly #repository: ProjectRepository;
  readonly #executor: BrowserCadExecutor;
  readonly #ownsExecutor: boolean;
  readonly #requestVisualReview: CadAgentProjectControllerOptions['requestVisualReview'];
  readonly #projectId: string;
  readonly #projectName: string;
  #brief: DesignBrief | undefined;
  #source: string | undefined;
  #sourceHash: string | undefined;
  #parameters: ParameterSet | undefined;
  #result: CadExecutionResult | undefined;
  #abortController: AbortController | undefined;
  #restoredSelectionReason: string | undefined;
  #parameterBuildSequence = 0;
  #candidate: boolean;
  #parentRunId: string | undefined;
  #baseRevisionId: string | undefined;
  #modelProfileId: string | undefined;
  #modelSnapshot: ModelProfileSnapshot | undefined;
  #mode: RunMode;
  readonly #sourceSnapshots = new Map<string, SourceSnapshot>();
  readonly #resultSnapshots = new Map<string, CadExecutionResult>();

  private constructor(
    options: CadAgentProjectControllerOptions,
    repository: ProjectRepository,
    executor: BrowserCadExecutor,
    ownsExecutor: boolean,
  ) {
    this.#repository = repository;
    this.#executor = executor;
    this.#ownsExecutor = ownsExecutor;
    this.#requestVisualReview = options.requestVisualReview;
    this.#projectId = options.projectId;
    this.#projectName = options.projectName ?? DEFAULT_PROJECT_NAME;
    this.#candidate = options.mode === 'modification';
    this.#parentRunId = options.parentRunId;
    this.#baseRevisionId = options.baseRevisionId;
    this.#modelProfileId = options.modelProfileId;
    this.#modelSnapshot = options.modelSnapshot;
    this.#mode = options.mode ?? 'baseline';
    this.coordinator = new CadRunCoordinator({
      runId: options.runId,
      userRequest: options.userRequest,
      preference: options.preference,
      researchEnabled: options.researchEnabled,
      visualReviewConsent: options.visualReviewConsent,
      ...(options.research === undefined ? {} : { research: options.research }),
    });
  }

  static async open(
    options: CadAgentProjectControllerOptions,
  ): Promise<CadAgentProjectController> {
    const repository =
      options.repository ?? (await OpfsProjectRepository.open());
    const ownsExecutor = options.executor === undefined;
    const executor =
      options.executor ??
      createBrowserCadExecutor({
        ...(options.onExecutorEvent === undefined
          ? {}
          : { onEvent: options.onExecutorEvent }),
      });
    const controller = new CadAgentProjectController(
      options,
      repository,
      executor,
      ownsExecutor,
    );
    await controller.initialize(options.research);
    return controller;
  }

  static async restore(
    options: RestoreCadAgentProjectControllerOptions,
  ): Promise<CadAgentProjectController | undefined> {
    const repository =
      options.repository ?? (await OpfsProjectRepository.open());
    const projectId = options.projectId ?? LEGACY_PROJECT_ID;
    const project = await repository.getProject(projectId);
    if (project?.currentRunId == null) return undefined;
    const [stored, parameters, revisions] = await Promise.all([
      repository.getRun(projectId, project.currentRunId),
      repository.getParameters(projectId),
      repository.listRevisions(projectId),
    ]);
    if (stored?.run.workflowKind == null) return undefined;
    const ownsExecutor = options.executor === undefined;
    const executor =
      options.executor ??
      createBrowserCadExecutor({
        ...(options.onExecutorEvent === undefined
          ? {}
          : { onEvent: options.onExecutorEvent }),
      });
    const controller = new CadAgentProjectController(
      {
        projectId,
        projectName: project.name,
        runId: stored.run.id,
        userRequest: 'Restore the stored CAD workspace.',
        preference: stored.run.workflowKind,
        researchEnabled: stored.research !== undefined,
        visualReviewConsent: 'declined',
        requestVisualReview: options.requestVisualReview,
        repository,
        executor,
      },
      repository,
      executor,
      ownsExecutor,
    );
    for (const revision of revisions) {
      controller.#sourceSnapshots.set(revision.sourceHash, {
        source: revision.modelSource,
        parameters:
          revision.parameters ??
          discoverParameterSet(revision.modelSource, revision.sourceHash),
        revisionId: revision.id,
      });
    }
    const activeRevision =
      revisions.findLast(
        (revision) => revision.sourceHash === stored.run.sourceHash,
      ) ?? revisions.at(-1);
    const sourceArtifact = stored.artifacts.find(
      ({ metadata }) => metadata.kind === 'model-source',
    );
    controller.#source =
      activeRevision?.modelSource ??
      (sourceArtifact === undefined
        ? undefined
        : new TextDecoder().decode(sourceArtifact.bytes));
    controller.#sourceHash =
      activeRevision?.sourceHash ?? stored.run.sourceHash ?? undefined;
    controller.#parameters =
      parameters === undefined || controller.#source === undefined
        ? (activeRevision?.parameters ?? parameters ?? undefined)
        : activeRevision?.parameters !== null &&
            activeRevision?.parameters !== undefined
          ? activeRevision.parameters
          : ensureParameterCouplings(parameters, controller.#source);
    controller.#brief = stored.designBrief;
    controller.#restoredSelectionReason =
      stored.run.workflowSelectionReason ?? undefined;
    if (
      stored.buildReport !== undefined &&
      stored.qaReport !== undefined &&
      stored.run.runtimeVersions !== undefined
    ) {
      controller.#result = {
        schemaVersion: SCHEMA_VERSION,
        runId: stored.run.id,
        qaReport: stored.qaReport,
        buildReport: stored.buildReport.data,
        runtimeVersions: stored.run.runtimeVersions,
        artifacts: stored.artifacts.map(({ metadata: artifact, bytes }) => ({
          artifact,
          bytes: Uint8Array.from(bytes).buffer,
        })),
      };
    }
    return controller;
  }

  private frozenQaTargets(
    requested: CadBuildQaTargets | undefined,
  ): CadBuildQaTargets | undefined {
    const mechanism = this.#brief?.mechanisms?.[0];
    if (mechanism === undefined) return requested;
    const bodyCount = new Set([
      ...mechanism.movingBodyIds,
      ...mechanism.stationaryBodyIds,
    ]).size;
    return { ...requested, componentCount: bodyCount };
  }

  get workflowSelectionReason(): string {
    return this.#restoredSelectionReason ?? this.coordinator.selection.reason;
  }

  private async initialize(research?: ResearchPacket): Promise<void> {
    const now = new Date().toISOString();
    let project = await this.#repository.getProject(this.#projectId);
    if (project === undefined) {
      project = {
        schemaVersion: SCHEMA_VERSION,
        id: this.#projectId,
        name: this.#projectName,
        createdAt: now,
        updatedAt: now,
        revision: 0,
        currentRunId: null,
      };
      await this.#repository.createProject({ project });
    }
    const profile = getCadWorkflowProfile(this.coordinator.workflowKind);
    await this.#repository.saveRun(this.#projectId, {
      run: {
        schemaVersion: SCHEMA_VERSION,
        id: this.coordinator.state.runId,
        projectId: this.#projectId,
        createdAt: now,
        completedAt: null,
        status: 'active',
        workflowKind: this.coordinator.workflowKind,
        workflowSelectionReason: this.workflowSelectionReason,
        sourceHash: null,
        workflowSnapshot: {
          engine: 'Amagine3D-CAD',
          revision: CAD_RUNTIME_MANIFEST.workflowRevision,
          profile: profile.profileId,
        },
        artifactIds: [],
        parentRunId: this.#parentRunId ?? null,
        baseRevisionId: this.#baseRevisionId ?? null,
        modelProfileId: this.#modelProfileId ?? null,
        mode: this.#mode,
        modelSnapshot: this.#modelSnapshot ?? null,
      },
      events: [...this.coordinator.events],
      artifacts: [],
      ...(research === undefined ? {} : { research }),
    });
    if (!this.#candidate) {
      await this.#repository.updateProject(
        {
          ...project,
          currentRunId: this.coordinator.state.runId,
          revision: project.revision + 1,
          updatedAt: now,
        },
        project.revision,
      );
    }
  }

  private async updateActiveRun(
    update: {
      sourceHash?: string;
      designBrief?: DesignBrief;
    } = {},
  ): Promise<void> {
    const runId = this.coordinator.state.runId;
    const stored = await this.#repository.getRun(this.#projectId, runId);
    if (stored === undefined)
      throw new Error(`Active run ${runId} is missing.`);
    await this.#repository.saveRun(this.#projectId, {
      ...stored,
      run: {
        ...stored.run,
        sourceHash: update.sourceHash ?? stored.run.sourceHash,
      },
      events: [...this.coordinator.events],
      ...(update.designBrief === undefined
        ? {}
        : { designBrief: update.designBrief }),
    });
  }

  private async saveSourceRevision(
    source: string,
    sourceHash: string,
    parameters: ParameterSet,
    metadata: Pick<
      ProjectRevision,
      'parentRevisionId' | 'reason' | 'repairContext' | 'restoredFromRevisionId'
    > = {},
  ): Promise<string | undefined> {
    this.#sourceSnapshots.set(sourceHash, { source, parameters });
    const project = await this.#repository.getProject(this.#projectId);
    if (project === undefined) throw new Error('CAD Agent project is missing.');
    const now = new Date().toISOString();
    if (this.#candidate && metadata.reason !== 'automatic-rollback') {
      return undefined;
    }
    const revisionId = crypto.randomUUID();
    await this.#repository.saveRevision({
      schemaVersion: SCHEMA_VERSION,
      id: revisionId,
      projectId: this.#projectId,
      revision: project.revision + 1,
      createdAt: now,
      sourceHash,
      modelSource: source,
      parameters,
      ...metadata,
    });
    await this.#repository.updateProject(
      {
        ...project,
        revision: project.revision + 1,
        updatedAt: now,
      },
      project.revision,
    );
    this.#sourceSnapshots.set(sourceHash, {
      source,
      parameters,
      revisionId,
    });
    return revisionId;
  }

  private async materializeRepairContext(
    output: CadToolOutput,
    result?: CadExecutionResult,
  ): Promise<CadToolOutput> {
    if (
      output.tool !== 'buildAndCheck' ||
      output.status !== 'failed' ||
      output.repairContext === undefined ||
      this.#source === undefined ||
      this.#parameters === undefined
    ) {
      return output;
    }
    const candidateSource = this.#source;
    const candidateHash = this.#sourceHash;
    const candidateParameters = this.#parameters;
    const baselineHash = output.repairContext.baselineSourceHash;
    let baseline = this.#sourceSnapshots.get(baselineHash);
    if (baseline === undefined) {
      const revision = (
        await this.#repository.listRevisions(this.#projectId)
      ).findLast((item) => item.sourceHash === baselineHash);
      if (revision !== undefined) {
        baseline = {
          source: revision.modelSource,
          parameters:
            revision.parameters ??
            discoverParameterSet(revision.modelSource, revision.sourceHash),
          revisionId: revision.id,
        };
        this.#sourceSnapshots.set(baselineHash, baseline);
      }
    }
    const sourceDelta =
      baseline === undefined
        ? undefined
        : changedSourceLines(baseline.source, candidateSource);
    const parameterNames =
      baseline === undefined
        ? []
        : changedParameterNames(baseline.parameters, candidateParameters);
    const featureOwnership = repairFeatureOwnership(
      result,
      this.#brief,
      output.failedCheckIds,
    );
    const affectedBodyIds = featureOwnership.map(({ bodyId }) => bodyId);
    const repairContext: RepairContext = {
      ...output.repairContext,
      ...(baseline?.revisionId === undefined
        ? {}
        : { baselineRevisionId: baseline.revisionId }),
      rollbackApplied: false,
      affectedConstraintIds: [...output.failedCheckIds],
      featureOwnership,
      ...(sourceDelta === undefined ? {} : { sourceDelta }),
      geometryDelta: {
        affectedBodyIds,
        affectedArtifactIds:
          result?.artifacts.map(({ artifact }) => artifact.id) ?? [],
        artifactChanges: geometryArtifactChanges(
          this.#resultSnapshots.get(baselineHash),
          result,
        ),
      },
      allowedMutationScope: {
        bodyIds: affectedBodyIds,
        parameterNames,
        sourceLineRanges: sourceDelta?.changedLineRanges ?? [],
      },
    };
    if (!repairContext.regression || baseline === undefined) {
      return { ...output, repairContext };
    }

    this.coordinator.restoreRepairBaseline(baselineHash);
    this.#source = baseline.source;
    this.#sourceHash = baselineHash;
    this.#parameters = baseline.parameters;
    repairContext.rollbackApplied = true;
    repairContext.directive =
      `${repairContext.directive} The host has already restored the baseline source; apply the next repair only inside allowedMutationScope.`.slice(
        0,
        2_000,
      );
    await this.saveSourceRevision(
      baseline.source,
      baselineHash,
      baseline.parameters,
      {
        reason: 'automatic-rollback',
        ...(candidateHash === undefined ||
        this.#sourceSnapshots.get(candidateHash)?.revisionId === undefined
          ? {}
          : {
              parentRevisionId:
                this.#sourceSnapshots.get(candidateHash)?.revisionId,
            }),
        ...(this.#sourceSnapshots.get(baselineHash)?.revisionId === undefined
          ? {}
          : {
              restoredFromRevisionId:
                this.#sourceSnapshots.get(baselineHash)?.revisionId,
            }),
        repairContext,
      },
    );
    return { ...output, repairContext };
  }

  async handleToolCall(
    toolNameInput: string,
    input: unknown,
  ): Promise<CadToolOutput> {
    const toolName = cadAgentToolNameSchema.parse(toolNameInput);
    switch (toolName) {
      case 'saveDesignBrief': {
        const { designBriefSchema } = await import('@amagine3d/cad-protocol');
        const brief = designBriefSchema.parse(input);
        const output = this.coordinator.saveDesignBrief(brief);
        this.#brief = brief;
        await this.updateActiveRun({ designBrief: brief });
        return output;
      }
      case 'writeCadSource': {
        const { writeCadSourceInputSchema } =
          await import('@amagine3d/cad-protocol');
        const sourceInput = writeCadSourceInputSchema.parse(input);
        validateCadSource(sourceInput.source, this.coordinator.workflowKind);
        const sourceHash = await sha256Text(sourceInput.source);
        const parameters = discoverParameterSet(sourceInput.source, sourceHash);
        const output = this.coordinator.writeCadSource(sourceInput, sourceHash);
        this.#source = sourceInput.source;
        this.#sourceHash = sourceHash;
        this.#parameters = parameters;
        await this.saveSourceRevision(
          sourceInput.source,
          sourceHash,
          parameters,
          { reason: 'generated' },
        );
        await this.updateActiveRun({ sourceHash });
        return output;
      }
      case 'buildAndCheck': {
        const { buildAndCheckInputSchema } =
          await import('@amagine3d/cad-protocol');
        const build = this.coordinator.validateBuildInput(
          buildAndCheckInputSchema.parse(input),
        );
        if (this.#source === undefined || this.#brief === undefined) {
          return this.coordinator.recordBuildFailure(
            'Generated source or design brief is missing.',
          );
        }
        this.#abortController = new AbortController();
        try {
          const qaTargets = this.frozenQaTargets(build.qaTargets);
          const result = await this.#executor.execute(
            {
              schemaVersion: SCHEMA_VERSION,
              requestId: crypto.randomUUID(),
              type: 'build',
              projectId: this.#projectId,
              runId: this.coordinator.state.runId,
              workflowKind: this.coordinator.workflowKind,
              source: this.#source,
              sourceHash: build.sourceHash,
              parameterOverrides:
                this.#parameters === undefined
                  ? {}
                  : parameterOverrides(this.#parameters),
              ...(qaTargets === undefined ? {} : { qaTargets }),
              ...(this.#brief.colorRegionPlan === undefined
                ? {}
                : { colorRegionPlan: this.#brief.colorRegionPlan }),
              ...(this.#brief.mechanisms === undefined
                ? {}
                : { mechanisms: this.#brief.mechanisms }),
              ...(this.#brief.featureChecks === undefined
                ? {}
                : { featureChecks: this.#brief.featureChecks }),
            },
            { signal: this.#abortController.signal },
          );
          const recorded = this.coordinator.recordBuildResult(result);
          this.#resultSnapshots.set(build.sourceHash, result);
          this.#result = result;
          const output = await this.materializeRepairContext(recorded, result);
          await this.updateActiveRun(
            this.#sourceHash === undefined
              ? {}
              : { sourceHash: this.#sourceHash },
          );
          return output;
        } catch (error) {
          // Runtime bootstrap failures are infrastructure failures, not
          // evidence that the generated source or deterministic QA is wrong.
          // Keep the workflow in building so an automatic retry reuses the
          // same source instead of sending the model back to coding.
          if (
            error instanceof CadDomainError &&
            error.operation === 'bootstrap'
          ) {
            throw error;
          }
          const signature =
            error instanceof Error ? error.message : 'CAD Worker failed.';
          const recorded = this.coordinator.recordBuildFailure(signature);
          const output = await this.materializeRepairContext(recorded);
          await this.updateActiveRun(
            this.#sourceHash === undefined
              ? {}
              : { sourceHash: this.#sourceHash },
          );
          return output;
        } finally {
          this.#abortController = undefined;
        }
      }
      case 'requestVisualReview': {
        const { visualReviewInputSchema } =
          await import('@amagine3d/cad-protocol');
        const review = this.coordinator.requestVisualReview(
          visualReviewInputSchema.parse(input),
        );
        if (this.#result === undefined) {
          throw new Error('Visual review has no verified build result.');
        }
        const decision = await this.#requestVisualReview(review, this.#result);
        const output = this.coordinator.recordVisualReview(
          decision.passed,
          decision.summary,
        );
        await this.updateActiveRun();
        return output;
      }
      case 'finishCadRun': {
        const { finishCadRunInputSchema } =
          await import('@amagine3d/cad-protocol');
        const finishInput = finishCadRunInputSchema.parse(input);
        const output = this.coordinator.finish(finishInput);
        if (this.#result === undefined || this.#brief === undefined) {
          throw new Error('Cannot finish without a verified result and brief.');
        }
        const stored = await this.#repository.getRun(
          this.#projectId,
          this.coordinator.state.runId,
        );
        if (stored === undefined) throw new Error('Active run is missing.');
        await persistSuccessfulExecution(this.#repository, {
          projectId: this.#projectId,
          run: stored.run,
          result: this.#result,
          events: [...this.coordinator.events],
          designBrief: this.#brief,
          ...(stored.research === undefined
            ? {}
            : { research: stored.research }),
          ...(this.#brief.colorRegionPlan === undefined
            ? {}
            : { colorPlan: this.#brief.colorRegionPlan }),
        });
        if (
          this.#candidate &&
          this.#source !== undefined &&
          this.#sourceHash !== undefined &&
          this.#parameters !== undefined
        ) {
          const project = await this.#repository.getProject(this.#projectId);
          if (project !== undefined) {
            const now = new Date().toISOString();
            await this.#repository.saveRevision({
              schemaVersion: SCHEMA_VERSION,
              id: crypto.randomUUID(),
              projectId: this.#projectId,
              revision: project.revision + 1,
              createdAt: now,
              sourceHash: this.#sourceHash,
              modelSource: this.#source,
              parameters: this.#parameters,
              reason: 'generated',
            });
            await this.#repository.updateProject(
              { ...project, revision: project.revision + 1, updatedAt: now },
              project.revision,
            );
          }
        }
        return output;
      }
    }
  }

  viewerModel(): ViewerModel | undefined {
    const result = this.#result;
    if (result === undefined) return undefined;
    return viewerModelFromResult(
      result.runId,
      this.coordinator.workflowKind,
      result.artifacts.map(({ artifact: metadata, bytes }) => ({
        metadata,
        bytes: new Uint8Array(bytes),
      })),
      this.#brief,
    );
  }

  async modelProfileSettings(): Promise<ModelProfileSettings | undefined> {
    return this.#repository.getModelProfileSettings();
  }

  async saveModelProfileSettings(
    settings: ModelProfileSettings,
  ): Promise<void> {
    await this.#repository.saveModelProfileSettings(settings);
  }

  async saveImageAttachments(
    inputs: readonly ImageInput[],
  ): Promise<Attachment[]> {
    const checked = validateImageInputs(inputs);
    const attachments: Attachment[] = [];
    for (const { input, width, height } of checked) {
      const ownedBytes = new Uint8Array(
        new ArrayBuffer(input.bytes.byteLength),
      );
      ownedBytes.set(input.bytes);
      const digest = await crypto.subtle.digest('SHA-256', ownedBytes);
      const metadata = attachmentSchema.parse({
        schemaVersion: SCHEMA_VERSION,
        id: crypto.randomUUID(),
        fileName: input.fileName,
        mediaType: input.mediaType,
        byteLength: input.bytes.byteLength,
        width,
        height,
        sha256: [...new Uint8Array(digest)]
          .map((value) => value.toString(16).padStart(2, '0'))
          .join(''),
        createdAt: new Date().toISOString(),
      });
      await this.#repository.saveAttachment(this.#projectId, {
        metadata,
        bytes: input.bytes,
      });
      attachments.push(metadata);
    }
    return attachments;
  }

  async workspaceSnapshot(): Promise<CadWorkspaceSnapshot> {
    const project = await this.#repository.getProject(this.#projectId);
    if (project === undefined) throw new Error('CAD Agent project is missing.');
    const revisions = await this.#repository.listRevisions(this.#projectId);
    const stored = await this.#repository.getRun(
      this.#projectId,
      project.currentRunId ?? this.coordinator.state.runId,
    );
    const resultArtifacts =
      this.#result?.artifacts.map(({ artifact: metadata, bytes }) => ({
        metadata,
        bytes: new Uint8Array(bytes),
      })) ??
      stored?.artifacts ??
      [];
    const model =
      this.#result === undefined
        ? stored === undefined || stored.run.workflowKind === null
          ? undefined
          : viewerModelFromResult(
              stored.run.id,
              stored.run.workflowKind,
              stored.artifacts,
              stored.designBrief,
            )
        : this.viewerModel();
    return {
      projectId: project.id,
      projectName: project.name,
      runId:
        this.#result?.runId ?? stored?.run.id ?? this.coordinator.state.runId,
      workflowKind: this.coordinator.workflowKind,
      workflowSelectionReason: this.workflowSelectionReason,
      revisions,
      artifacts: resultArtifacts.map(({ metadata, bytes }) => ({
        metadata,
        bytes: bytes.slice(),
      })),
      ...(this.#source === undefined ? {} : { source: this.#source }),
      ...(this.#sourceHash === undefined
        ? {}
        : { sourceHash: this.#sourceHash }),
      ...(this.#parameters === undefined
        ? {}
        : { parameters: this.#parameters }),
      ...(this.#brief === undefined ? {} : { designBrief: this.#brief }),
      ...(this.#result === undefined
        ? stored?.buildReport === undefined
          ? {}
          : { buildReport: stored.buildReport.data }
        : { buildReport: this.#result.buildReport }),
      ...(this.#result === undefined
        ? stored?.qaReport === undefined
          ? {}
          : { qaReport: stored.qaReport }
        : { qaReport: this.#result.qaReport }),
      ...(stored?.research === undefined ? {} : { research: stored.research }),
      ...(model === undefined ? {} : { model }),
    };
  }

  async saveParameters(parameters: ParameterSet): Promise<void> {
    if (
      this.#sourceHash === undefined ||
      parameters.sourceHash !== this.#sourceHash
    ) {
      throw new CadDomainError(
        'SourceHashConflict',
        'Parameter changes do not match the active source revision.',
        {
          category: 'integrity',
          retryable: false,
          operation: 'save-parameters',
        },
      );
    }
    this.#parameters = parameters;
    await this.#repository.saveParameters(this.#projectId, parameters);
  }

  async rebuildWithParameters(
    parameters: ParameterSet,
    signal: AbortSignal,
  ): Promise<CadWorkspaceSnapshot> {
    if (this.#source === undefined || this.#sourceHash === undefined) {
      throw new Error('A verified model source is required.');
    }
    this.#parameterBuildSequence += 1;
    const buildSequence = this.#parameterBuildSequence;
    await this.saveParameters(parameters);
    const qaTargets = this.frozenQaTargets(undefined);
    const result = await this.#executor.execute(
      {
        schemaVersion: SCHEMA_VERSION,
        requestId: crypto.randomUUID(),
        type: 'build',
        projectId: this.#projectId,
        runId: this.coordinator.state.runId,
        workflowKind: this.coordinator.workflowKind,
        source: this.#source,
        sourceHash: this.#sourceHash,
        parameterOverrides: parameterOverrides(parameters),
        ...(qaTargets === undefined ? {} : { qaTargets }),
        ...(this.#brief?.colorRegionPlan === undefined
          ? {}
          : { colorRegionPlan: this.#brief.colorRegionPlan }),
        ...(this.#brief?.mechanisms === undefined
          ? {}
          : { mechanisms: this.#brief.mechanisms }),
        ...(this.#brief?.featureChecks === undefined
          ? {}
          : { featureChecks: this.#brief.featureChecks }),
      },
      { signal },
    );
    if (signal.aborted || buildSequence !== this.#parameterBuildSequence) {
      throw new DOMException(
        'A newer parameter build superseded this result.',
        'AbortError',
      );
    }
    // Manual parameter adjustment is a parallel preview module, not a new
    // design run. It only updates the in-memory result (driving the preview)
    // and the saved parameters. QA is advisory: a failed report travels with
    // the snapshot for the UI to warn the user, but no run state changes.
    this.#parameters = parameters;
    this.#result = result;
    return this.workspaceSnapshot();
  }

  prepareSourceWriteback(parameters: ParameterSet): SourceWritebackPreview {
    if (this.#source === undefined || this.#sourceHash === undefined) {
      throw new Error('No active source is available.');
    }
    const source = writeParametersToSource(
      this.#source,
      this.#sourceHash,
      parameters,
    );
    return { source, diff: createSourceDiff(this.#source, source) };
  }

  async confirmSourceWriteback(
    preview: SourceWritebackPreview,
  ): Promise<CadWorkspaceSnapshot> {
    const sourceHash = await sha256Text(preview.source);
    const parameters = discoverParameterSet(preview.source, sourceHash);
    await this.saveSourceRevision(preview.source, sourceHash, parameters, {
      reason: 'parameter-writeback',
    });
    this.#source = preview.source;
    this.#sourceHash = sourceHash;
    this.#parameters = parameters;
    return this.workspaceSnapshot();
  }

  async restoreRevision(revisionId: string): Promise<CadWorkspaceSnapshot> {
    const revision = await this.#repository.getRevision(
      this.#projectId,
      revisionId,
    );
    if (revision === undefined)
      throw new Error('Source revision was not found.');
    const parameters =
      revision.parameters ??
      discoverParameterSet(revision.modelSource, revision.sourceHash);
    await this.saveSourceRevision(
      revision.modelSource,
      revision.sourceHash,
      parameters,
      {
        reason: 'manual-restore',
        restoredFromRevisionId: revision.id,
      },
    );
    this.#source = revision.modelSource;
    this.#sourceHash = revision.sourceHash;
    this.#parameters = parameters;
    return this.workspaceSnapshot();
  }

  async saveMessages(messages: readonly unknown[]): Promise<void> {
    const history = {
      schemaVersion: SCHEMA_VERSION,
      messages: messages.map(toJsonValue),
    };
    await Promise.all([
      this.#repository.saveMessages(this.#projectId, history),
      this.#repository.saveRunMessages(
        this.#projectId,
        this.coordinator.state.runId,
        history,
      ),
    ]);
  }

  private async saveTerminalRun(
    status: 'failed' | 'cancelled',
    reason: string,
  ): Promise<void> {
    const runId = this.coordinator.state.runId;
    const stored = await this.#repository.getRun(this.#projectId, runId);
    if (stored === undefined) return;
    if (stored.run.status !== 'active') return;
    const run = {
      ...stored.run,
      status,
      failureReason: reason.trim().slice(0, 4_000),
      completedAt: new Date().toISOString(),
    };
    const result = this.#result;
    if (result === undefined) {
      await this.#repository.saveRun(this.#projectId, {
        ...stored,
        run,
        events: [...this.coordinator.events],
      });
      return;
    }
    const artifacts = toPersistentExecutionArtifacts(result);
    await this.#repository.saveRun(this.#projectId, {
      ...stored,
      run: {
        ...run,
        artifactIds: artifacts.map(({ metadata }) => metadata.id),
        runtimeVersions: result.runtimeVersions,
      },
      artifacts,
      events: [...this.coordinator.events],
      buildReport: { schemaVersion: SCHEMA_VERSION, data: result.buildReport },
      qaReport: result.qaReport,
      ...(this.#brief === undefined ? {} : { designBrief: this.#brief }),
      ...(this.#brief?.colorRegionPlan === undefined
        ? {}
        : { colorPlan: this.#brief.colorRegionPlan }),
    });
  }

  async cancel(): Promise<void> {
    this.#abortController?.abort();
    this.coordinator.cancel();
    await this.saveTerminalRun('cancelled', 'The user cancelled the CAD run.');
  }

  async fail(reason: string): Promise<void> {
    this.#abortController?.abort();
    this.coordinator.fail(reason);
    await this.saveTerminalRun('failed', reason);
  }

  dispose(): void {
    if (this.#ownsExecutor) this.#executor.dispose();
  }
}
