import {
  CadDomainError,
  cadExecutionResultSchema,
  cadRunSchema,
  SCHEMA_VERSION,
  type Artifact,
  type CadExecutionResult,
  type CadRun,
  type ColorRegionPlan,
  type DesignBrief,
  type ResearchPacket,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import type { BinaryArtifact, ProjectRepository, StoredRun } from './types';

export type PersistExecutionInput = {
  projectId: string;
  run: CadRun;
  result: CadExecutionResult;
  events?: WorkflowEventRecord[];
  research?: ResearchPacket;
  designBrief?: DesignBrief;
  colorPlan?: ColorRegionPlan;
  now?: () => Date;
};

function requireArtifactKinds(
  result: CadExecutionResult,
  colorPlan?: ColorRegionPlan,
): void {
  const kinds = new Set(result.artifacts.map(({ artifact }) => artifact.kind));
  const required: Artifact['kind'][] = [
    'model-source',
    'build-report',
    'qa-report',
  ];
  if (result.qaReport.workflowKind === 'single-color') {
    required.push('step', 'stl');
  }
  for (const kind of required) {
    if (!kinds.has(kind)) {
      throw new CadDomainError(
        'InvalidExternalData',
        `Successful ${result.qaReport.workflowKind} result is missing ${kind}.`,
        { category: 'protocol', retryable: false, operation: 'persist-run' },
      );
    }
  }
  if (
    result.qaReport.workflowKind === 'multi-color' &&
    (!kinds.has('color-plan') ||
      !kinds.has('model-3mf') ||
      !kinds.has('region-stl'))
  ) {
    throw new CadDomainError(
      'InvalidExternalData',
      'Successful multi-color result requires its color plan, 3MF, and region STL artifacts.',
      { category: 'protocol', retryable: false, operation: 'persist-run' },
    );
  }
  if (result.qaReport.workflowKind === 'multi-color' && colorPlan) {
    const actualRegions = new Set(
      result.artifacts
        .filter(({ artifact }) => artifact.kind === 'region-stl')
        .map(({ artifact }) => artifact.regionName ?? ''),
    );
    if (
      colorPlan.regions.length !== actualRegions.size ||
      colorPlan.regions.some(
        (region) =>
          !actualRegions.has(region.id) && !actualRegions.has(region.name),
      )
    ) {
      throw new CadDomainError(
        'InvalidExternalData',
        'Region STL artifacts do not exactly match the frozen color plan.',
        { category: 'protocol', retryable: false, operation: 'persist-run' },
      );
    }
  }
}

export function toPersistentExecutionArtifacts(
  result: CadExecutionResult,
): BinaryArtifact[] {
  return result.artifacts.map(({ artifact, bytes }) => ({
    metadata: artifact,
    bytes: new Uint8Array(bytes),
  }));
}

function toPersistentEvents(
  events: WorkflowEventRecord[],
  artifactIds: ReadonlySet<string>,
): WorkflowEventRecord[] {
  return events
    .filter(
      (event) =>
        event.type !== 'artifact' || artifactIds.has(event.payload.artifactId),
    )
    .map((event, sequence) => ({ ...event, sequence }));
}

export async function persistSuccessfulExecution(
  repository: ProjectRepository,
  input: PersistExecutionInput,
): Promise<CadRun> {
  const result = cadExecutionResultSchema.parse(input.result);
  if (result.qaReport.status !== 'passed') {
    throw new CadDomainError(
      'QaFailed',
      'A run with failed deterministic QA cannot be persisted as successful.',
      { category: 'qa', retryable: true, operation: 'persist-run' },
    );
  }
  if (
    input.run.id !== result.runId ||
    input.run.projectId !== input.projectId
  ) {
    throw new CadDomainError(
      'InvalidExternalData',
      'Execution result does not belong to the supplied project run.',
      { category: 'protocol', retryable: false, operation: 'persist-run' },
    );
  }
  if (
    input.run.workflowKind !== result.qaReport.workflowKind ||
    input.run.workflowSnapshot == null ||
    input.run.workflowSnapshot.profile !==
      (result.qaReport.workflowKind === 'single-color'
        ? 'hardware-enclosure-single'
        : 'hardware-enclosure-multi')
  ) {
    throw new CadDomainError(
      'InvalidExternalData',
      'Run workflow snapshot does not match the execution profile.',
      { category: 'protocol', retryable: false, operation: 'persist-run' },
    );
  }
  if (
    result.qaReport.workflowKind === 'multi-color' &&
    input.colorPlan === undefined
  ) {
    throw new CadDomainError(
      'InvalidExternalData',
      'A successful multi-color run requires its frozen color plan.',
      { category: 'protocol', retryable: false, operation: 'persist-run' },
    );
  }
  requireArtifactKinds(result, input.colorPlan);

  const now = input.now ?? (() => new Date());
  const completedAt = now().toISOString();
  const artifacts = toPersistentExecutionArtifacts(result);
  const persistentArtifactIds = new Set(
    artifacts.map(({ metadata }) => metadata.id),
  );
  const sourceArtifact = artifacts.find(
    ({ metadata }) => metadata.kind === 'model-source',
  );
  if (sourceArtifact === undefined) {
    throw new CadDomainError(
      'InvalidExternalData',
      'Successful execution is missing its immutable model source.',
      { category: 'protocol', retryable: false, operation: 'persist-run' },
    );
  }
  const run = cadRunSchema.parse({
    ...input.run,
    status: 'succeeded',
    completedAt,
    runtimeVersions: result.runtimeVersions,
    artifactIds: artifacts.map(({ metadata }) => metadata.id),
    sourceHash: sourceArtifact.metadata.sha256,
  });
  const storedRun: StoredRun = {
    run,
    events: toPersistentEvents(input.events ?? [], persistentArtifactIds),
    artifacts,
    buildReport: { schemaVersion: SCHEMA_VERSION, data: result.buildReport },
    qaReport: result.qaReport,
    ...(input.research === undefined ? {} : { research: input.research }),
    ...(input.designBrief === undefined
      ? {}
      : { designBrief: input.designBrief }),
    ...(input.colorPlan === undefined ? {} : { colorPlan: input.colorPlan }),
  };
  await repository.saveRun(input.projectId, storedRun);

  const project = await repository.getProject(input.projectId);
  if (project === undefined) {
    throw new CadDomainError(
      'StorageUnavailable',
      `Project ${input.projectId} disappeared while saving its run.`,
      { category: 'storage', retryable: true, operation: 'persist-run' },
    );
  }
  await repository.updateProject(
    {
      ...project,
      revision: project.revision + 1,
      currentRunId: run.id,
      updatedAt: completedAt,
    },
    project.revision,
  );
  return run;
}
