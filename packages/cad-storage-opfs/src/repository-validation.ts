import {
  artifactSchema,
  cadProjectSchema,
  cadRunSchema,
  colorRegionPlanSchema,
  designBriefSchema,
  messageHistorySchema,
  projectRevisionSchema,
  qaReportSchema,
  researchPacketSchema,
  versionedJsonDocumentSchema,
  workflowEventRecordSchema,
  type Artifact,
  type CadProject,
  type ProjectRevision,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import { ArchiveIntegrityMismatch, StorageConflict } from './errors';
import { sha256 } from './hash';
import { assertSafeStorageId } from './path';
import type { StoredRun } from './types';

export function validateProject(project: CadProject): CadProject {
  const parsed = cadProjectSchema.parse(project);
  assertSafeStorageId(parsed.id, 'projectId');
  return parsed;
}

export async function validateRevision(
  revision: ProjectRevision,
): Promise<ProjectRevision> {
  const parsed = projectRevisionSchema.parse(revision);
  assertSafeStorageId(parsed.projectId, 'projectId');
  assertSafeStorageId(parsed.id, 'revisionId');
  const actualHash = await sha256(new TextEncoder().encode(parsed.modelSource));
  if (actualHash !== parsed.sourceHash) {
    throw new ArchiveIntegrityMismatch(
      parsed.id,
      'revision sourceHash does not match modelSource',
    );
  }
  return parsed;
}

export async function validateStoredRun(
  projectId: string,
  input: StoredRun,
): Promise<StoredRun> {
  assertSafeStorageId(projectId, 'projectId');
  const run = cadRunSchema.parse(input.run);
  assertSafeStorageId(run.id, 'runId');
  if (run.projectId !== projectId) {
    throw new StorageConflict(
      'Run projectId does not match its repository path.',
    );
  }
  const events = input.events.map((event) =>
    workflowEventRecordSchema.parse(event),
  );
  validateEventSequence(run.id, events);
  const artifacts = await Promise.all(
    input.artifacts.map(async (artifact) => {
      const metadata = artifactSchema.parse(artifact.metadata);
      if (metadata.runId !== run.id) {
        throw new StorageConflict(
          `Artifact ${metadata.id} belongs to another run.`,
        );
      }
      if (
        metadata.byteLength !== artifact.bytes.byteLength ||
        metadata.sha256 !== (await sha256(artifact.bytes))
      ) {
        throw new ArchiveIntegrityMismatch(
          metadata.id,
          'artifact metadata does not match its bytes',
        );
      }
      return { metadata, bytes: artifact.bytes.slice() };
    }),
  );
  const actualArtifactIds = new Set(
    artifacts.map(({ metadata }) => metadata.id),
  );
  if (
    actualArtifactIds.size !== run.artifactIds.length ||
    run.artifactIds.some((id) => !actualArtifactIds.has(id))
  ) {
    throw new StorageConflict(
      'run.artifactIds does not match saved artifacts.',
    );
  }

  const designBrief =
    input.designBrief === undefined
      ? undefined
      : designBriefSchema.parse(input.designBrief);
  const qaReport =
    input.qaReport === undefined
      ? undefined
      : qaReportSchema.parse(input.qaReport);
  if (designBrief !== undefined && designBrief.runId !== run.id) {
    throw new StorageConflict('Design brief belongs to another run.');
  }
  if (qaReport !== undefined && qaReport.runId !== run.id) {
    throw new StorageConflict('QA report belongs to another run.');
  }

  return {
    run,
    events,
    artifacts,
    ...(input.research === undefined
      ? {}
      : { research: researchPacketSchema.parse(input.research) }),
    ...(designBrief === undefined ? {} : { designBrief }),
    ...(input.colorPlan === undefined
      ? {}
      : { colorPlan: colorRegionPlanSchema.parse(input.colorPlan) }),
    ...(input.buildReport === undefined
      ? {}
      : { buildReport: versionedJsonDocumentSchema.parse(input.buildReport) }),
    ...(qaReport === undefined ? {} : { qaReport }),
  };
}

export function validateEventSequence(
  runId: string,
  events: WorkflowEventRecord[],
): void {
  const ids = new Set<string>();
  events.forEach((event, index) => {
    if (
      event.runId !== runId ||
      event.sequence !== index ||
      ids.has(event.id)
    ) {
      throw new StorageConflict(
        `Event log for ${runId} must have unique IDs and contiguous zero-based sequence numbers.`,
      );
    }
    ids.add(event.id);
  });
}

export function validateMutableRunUpdate(
  previous: StoredRun,
  next: StoredRun,
): void {
  if (previous.run.status !== 'active') {
    throw new StorageConflict(`Run ${previous.run.id} is immutable.`);
  }
  const immutableKeys: Array<keyof typeof previous.run> = [
    'id',
    'projectId',
    'createdAt',
    'workflowKind',
    'workflowSelectionReason',
    'workflowSnapshot',
  ];
  for (const key of immutableKeys) {
    if (JSON.stringify(previous.run[key]) !== JSON.stringify(next.run[key])) {
      throw new StorageConflict(
        `Run field ${key} cannot change after creation.`,
      );
    }
  }
  const previousArtifacts = new Map<string, Artifact>(
    previous.artifacts.map(({ metadata }) => [metadata.id, metadata]),
  );
  for (const { metadata } of next.artifacts) {
    const existing = previousArtifacts.get(metadata.id);
    if (existing !== undefined && existing.sha256 !== metadata.sha256) {
      throw new StorageConflict(
        `Artifact ${metadata.id} cannot be replaced with different bytes.`,
      );
    }
  }
}

export function parseMessages(input: unknown) {
  return messageHistorySchema.parse(input);
}
