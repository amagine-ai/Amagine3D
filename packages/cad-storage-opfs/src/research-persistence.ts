import {
  SCHEMA_VERSION,
  cadRunSchema,
  researchPacketSchema,
  workflowEventRecordSchema,
  type CadProject,
  type ResearchPacket,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import type { ProjectRepository } from './types';

export type PersistResearchStageInput = {
  projectId: string;
  projectName: string;
  runId: string;
  createdAt: string;
  research?: ResearchPacket;
  events: WorkflowEventRecord[];
};

export async function persistResearchStage(
  repository: ProjectRepository,
  input: PersistResearchStageInput,
): Promise<CadProject> {
  const research =
    input.research === undefined
      ? undefined
      : researchPacketSchema.parse(input.research);
  const events = input.events.map((event) =>
    workflowEventRecordSchema.parse(event),
  );
  let project = await repository.getProject(input.projectId);
  if (project === undefined) {
    project = {
      schemaVersion: SCHEMA_VERSION,
      id: input.projectId,
      name: input.projectName,
      createdAt: input.createdAt,
      updatedAt: input.createdAt,
      revision: 0,
      currentRunId: null,
    };
    await repository.createProject({ project });
  }
  const run = cadRunSchema.parse({
    schemaVersion: SCHEMA_VERSION,
    id: input.runId,
    projectId: input.projectId,
    createdAt: input.createdAt,
    completedAt: null,
    status: 'active',
    workflowKind: null,
    workflowSelectionReason: null,
    sourceHash: null,
    workflowSnapshot: null,
    artifactIds: [],
  });
  await repository.saveRun(input.projectId, {
    run,
    events,
    artifacts: [],
    ...(research === undefined ? {} : { research }),
  });
  const updated: CadProject = {
    ...project,
    revision: project.revision + 1,
    currentRunId: input.runId,
    updatedAt: input.createdAt,
  };
  await repository.updateProject(updated, project.revision);
  return updated;
}
