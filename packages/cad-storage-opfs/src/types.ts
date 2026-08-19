import type {
  Artifact,
  Attachment,
  CadProject,
  CadRun,
  ColorRegionPlan,
  DesignBrief,
  MessageHistory,
  ModelProfileSettings,
  ParameterSet,
  ProjectRevision,
  QaReport,
  ResearchPacket,
  VersionedJsonDocument,
  WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

export type BinaryArtifact = {
  metadata: Artifact;
  bytes: Uint8Array;
};

export type StoredAttachment = {
  metadata: Attachment;
  bytes: Uint8Array;
};

export type StoredRun = {
  run: CadRun;
  events: WorkflowEventRecord[];
  artifacts: BinaryArtifact[];
  research?: ResearchPacket;
  designBrief?: DesignBrief;
  colorPlan?: ColorRegionPlan;
  buildReport?: VersionedJsonDocument;
  qaReport?: QaReport;
};

export type ProjectSeed = {
  project: CadProject;
  revision?: ProjectRevision;
  messages?: MessageHistory;
};

export type RecoveryDiagnostic = {
  level: 'info' | 'warning' | 'error';
  code:
    | 'canonical-restored'
    | 'corrupt-file-quarantined'
    | 'migration-applied'
    | 'orphaned-import-quarantined'
    | 'temporary-generation-recovered';
  path: string;
  message: string;
};

export type ImportPreflight = {
  manifestProjectId: string;
  manifestProjectName: string;
  entryCount: number;
  totalBytes: number;
  duplicateProject: boolean;
};

export type StorageCapacityStatus = {
  supported: boolean;
  persisted: boolean | null;
  persistenceRequested: boolean;
  quotaBytes: number | null;
  usageBytes: number | null;
  usageRatio: number | null;
  warning: 'not-supported' | 'persistence-denied' | 'quota-nearly-full' | null;
};

export interface ProjectRepository {
  createProject(seed: ProjectSeed): Promise<void>;
  getProject(projectId: string): Promise<CadProject | undefined>;
  listProjects(): Promise<CadProject[]>;
  updateProject(project: CadProject, expectedRevision: number): Promise<void>;
  saveRevision(revision: ProjectRevision): Promise<void>;
  getRevision(
    projectId: string,
    revisionId: string,
  ): Promise<ProjectRevision | undefined>;
  listRevisions(projectId: string): Promise<ProjectRevision[]>;
  saveParameters(projectId: string, parameters: ParameterSet): Promise<void>;
  getParameters(projectId: string): Promise<ParameterSet | undefined>;
  saveMessages(projectId: string, messages: MessageHistory): Promise<void>;
  getMessages(projectId: string): Promise<MessageHistory | undefined>;
  saveRunMessages(
    projectId: string,
    runId: string,
    messages: MessageHistory,
  ): Promise<void>;
  getRunMessages(
    projectId: string,
    runId: string,
  ): Promise<MessageHistory | undefined>;
  saveRun(projectId: string, storedRun: StoredRun): Promise<void>;
  getRun(projectId: string, runId: string): Promise<StoredRun | undefined>;
  listRuns(projectId: string): Promise<CadRun[]>;
  appendEvent(
    projectId: string,
    runId: string,
    event: WorkflowEventRecord,
  ): Promise<void>;
  getEvents(projectId: string, runId: string): Promise<WorkflowEventRecord[]>;
  getArtifact(
    projectId: string,
    runId: string,
    artifactId: string,
  ): Promise<BinaryArtifact | undefined>;
  exportProject(projectId: string): Promise<Uint8Array>;
  preflightImport(archive: Uint8Array): Promise<ImportPreflight>;
  importProject(archive: Uint8Array): Promise<CadProject>;
  getRecoveryDiagnostics(): readonly RecoveryDiagnostic[];
  saveAttachment(
    projectId: string,
    attachment: StoredAttachment,
  ): Promise<void>;
  getAttachment(
    projectId: string,
    attachmentId: string,
  ): Promise<StoredAttachment | undefined>;
  listAttachments(projectId: string): Promise<Attachment[]>;
  saveModelProfileSettings(settings: ModelProfileSettings): Promise<void>;
  getModelProfileSettings(): Promise<ModelProfileSettings | undefined>;
}
