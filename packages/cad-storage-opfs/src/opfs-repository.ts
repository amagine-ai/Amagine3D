import {
  SCHEMA_VERSION,
  artifactIndexSchema,
  attachmentSchema,
  cadProjectSchema,
  cadRunSchema,
  colorRegionPlanSchema,
  designBriefSchema,
  messageHistorySchema,
  modelProfileSettingsSchema,
  parameterSetSchema,
  projectRevisionSchema,
  qaReportSchema,
  researchPacketSchema,
  versionedJsonDocumentSchema,
  workflowEventLogSchema,
  workflowEventRecordSchema,
  type Artifact,
  type Attachment,
  type CadProject,
  type CadRun,
  type MessageHistory,
  type ModelProfileSettings,
  type ParameterSet,
  type ProjectRevision,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import {
  assertImportIsUnique,
  createProjectArchive,
  toImportPreflight,
  validateProjectArchive,
} from './archive';
import { CommittedJsonStore } from './committed-json';
import { CorruptStoredData, StorageConflict } from './errors';
import {
  type FileStore,
  MemoryFileStore,
  openOpfsFileStore,
} from './file-store';
import { encodeText, equalBytes, sha256 } from './hash';
import { assertSafeStorageId, joinPath } from './path';
import {
  parseMessages,
  validateEventSequence,
  validateMutableRunUpdate,
  validateProject,
  validateRevision,
  validateStoredRun,
} from './repository-validation';
import type {
  BinaryArtifact,
  ImportPreflight,
  ProjectRepository,
  ProjectSeed,
  RecoveryDiagnostic,
  StoredAttachment,
  StoredRun,
} from './types';

export type RepositoryOptions = {
  now?: () => Date;
  createId?: () => string;
};

function artifactRelativePath(artifact: Artifact): string {
  const fixedPaths: Partial<Record<Artifact['kind'], string>> = {
    'build-report': 'build-report.json',
    'color-plan': 'color-plan.json',
    'design-brief': 'design-brief.json',
    'model-3mf': 'model.3mf',
    'model-source': 'model.py',
    'preview-glb': 'preview.glb',
    'qa-report': 'qa-report.json',
    'research-packet': 'research.json',
    step: 'model.step',
    stl: 'model.stl',
  };
  if (artifact.kind === 'region-stl') {
    assertSafeStorageId(artifact.fileName, 'artifactFileName');
    return joinPath('regions', artifact.fileName);
  }
  if (artifact.kind === 'stl' || artifact.kind === 'step') {
    // Multi-body runs emit one STL and one STEP per separately-printable
    // body. Only the canonical primary keeps the fixed model.stl /
    // model.step path; every per-part file uses its own unique fileName so
    // each body resolves to an independent storage path.
    assertSafeStorageId(artifact.fileName, 'artifactFileName');
    const fixed = fixedPaths[artifact.kind];
    return artifact.fileName === fixed
      ? (fixed ?? artifact.fileName)
      : artifact.fileName;
  }
  const fixed = fixedPaths[artifact.kind];
  if (fixed !== undefined) {
    return fixed;
  }
  assertSafeStorageId(artifact.fileName, 'artifactFileName');
  return artifact.fileName;
}

function isInternalTransactionFile(path: string): boolean {
  return (
    path.endsWith('.commit') ||
    path.includes('.tmp-') ||
    path.includes('/corrupt/') ||
    path.includes('/.cad-worker/') ||
    path.startsWith('.imports/')
  );
}

function structuredArtifactDocument(
  artifact: Artifact,
  storedRun: StoredRun,
): unknown | undefined {
  switch (artifact.kind) {
    case 'build-report':
      return storedRun.buildReport;
    case 'color-plan':
      return storedRun.colorPlan;
    case 'design-brief':
      return storedRun.designBrief;
    case 'qa-report':
      return storedRun.qaReport;
    case 'research-packet':
      return storedRun.research;
    default:
      return undefined;
  }
}

function isStructuredArtifact(artifact: Artifact): boolean {
  return (
    artifact.kind === 'build-report' ||
    artifact.kind === 'color-plan' ||
    artifact.kind === 'design-brief' ||
    artifact.kind === 'qa-report' ||
    artifact.kind === 'research-packet'
  );
}

export class OpfsProjectRepository implements ProjectRepository {
  private readonly diagnostics: RecoveryDiagnostic[] = [];
  private readonly documents: CommittedJsonStore;
  private readonly now: () => Date;

  constructor(
    private readonly files: FileStore,
    options: RepositoryOptions = {},
  ) {
    this.now = options.now ?? (() => new Date());
    this.documents = new CommittedJsonStore(files, {
      ...options,
      onDiagnostic: (diagnostic) => this.diagnostics.push(diagnostic),
    });
  }

  static async open(options: RepositoryOptions = {}) {
    return new OpfsProjectRepository(await openOpfsFileStore(), options);
  }

  static inMemory(options: RepositoryOptions = {}) {
    return new OpfsProjectRepository(new MemoryFileStore(), options);
  }

  async createProject(seed: ProjectSeed): Promise<void> {
    const project = validateProject(seed.project);
    if ((await this.getProject(project.id)) !== undefined) {
      throw new StorageConflict(`Project ${project.id} already exists.`);
    }
    if (seed.revision !== undefined) {
      const revision = await validateRevision(seed.revision);
      if (
        revision.projectId !== project.id ||
        revision.revision !== project.revision
      ) {
        throw new StorageConflict(
          'Initial revision must match the project ID and revision number.',
        );
      }
      await this.saveRevision(revision, false);
    }
    if (seed.messages !== undefined) {
      await this.saveMessages(project.id, seed.messages, false);
    }
    await this.documents.write(this.projectPath(project.id), project);
  }

  async getProject(projectId: string): Promise<CadProject | undefined> {
    assertSafeStorageId(projectId, 'projectId');
    return this.documents.read(
      this.projectPath(projectId),
      'project',
      cadProjectSchema,
    );
  }

  async listProjects(): Promise<CadProject[]> {
    const candidates = (await this.files.list())
      .filter((path) => {
        const parts = path.split('/');
        const name = parts[1];
        return (
          parts.length === 2 &&
          name !== undefined &&
          (name === 'project.json' ||
            name === 'project.json.commit' ||
            name.startsWith('project.json.tmp-'))
        );
      })
      .map((path) => path.split('/')[0])
      .filter((id): id is string => id !== undefined);
    const projects: CadProject[] = [];
    for (const projectId of [...new Set(candidates)].sort()) {
      try {
        const project = await this.getProject(projectId);
        if (project !== undefined) {
          projects.push(project);
        }
      } catch (error) {
        if (!(error instanceof CorruptStoredData)) {
          throw error;
        }
      }
    }
    return projects;
  }

  async updateProject(
    input: CadProject,
    expectedRevision: number,
  ): Promise<void> {
    const project = validateProject(input);
    const previous = await this.requireProject(project.id);
    if (
      previous.revision !== expectedRevision ||
      project.revision !== expectedRevision + 1 ||
      project.createdAt !== previous.createdAt
    ) {
      throw new StorageConflict(
        `Project ${project.id} revision changed while it was being updated.`,
      );
    }
    await this.documents.write(this.projectPath(project.id), project);
  }

  async saveRevision(
    input: ProjectRevision,
    requireProject = true,
  ): Promise<void> {
    const revision = await validateRevision(input);
    if (requireProject) {
      await this.requireProject(revision.projectId);
    }
    const path = this.revisionPath(revision.projectId, revision.id);
    if (
      (await this.documents.read(path, 'revision', projectRevisionSchema)) !==
      undefined
    ) {
      throw new StorageConflict(`Revision ${revision.id} is immutable.`);
    }
    await this.documents.write(path, revision);
    await this.files.write(
      joinPath(revision.projectId, 'cad/model.py'),
      encodeText(revision.modelSource),
    );
    if (revision.parameters !== null) {
      await this.documents.write(
        joinPath(revision.projectId, 'cad/parameters.json'),
        revision.parameters,
      );
    }
  }

  async getRevision(
    projectId: string,
    revisionId: string,
  ): Promise<ProjectRevision | undefined> {
    assertSafeStorageId(projectId, 'projectId');
    assertSafeStorageId(revisionId, 'revisionId');
    return this.documents.read(
      this.revisionPath(projectId, revisionId),
      'revision',
      projectRevisionSchema,
    );
  }

  async listRevisions(projectId: string): Promise<ProjectRevision[]> {
    await this.requireProject(projectId);
    const prefix = joinPath(projectId, 'revisions/');
    const paths = (await this.files.list(prefix)).filter(
      (path) =>
        path.endsWith('/revision.json') && !isInternalTransactionFile(path),
    );
    const revisions = await Promise.all(
      paths.map((path) =>
        this.documents.read(path, 'revision', projectRevisionSchema),
      ),
    );
    return revisions
      .filter((revision): revision is ProjectRevision => revision !== undefined)
      .sort((left, right) => left.revision - right.revision);
  }

  async saveParameters(projectId: string, input: ParameterSet): Promise<void> {
    await this.requireProject(projectId);
    await this.documents.write(
      joinPath(projectId, 'cad/parameters.json'),
      parameterSetSchema.parse(input),
    );
  }

  async getParameters(projectId: string): Promise<ParameterSet | undefined> {
    await this.requireProject(projectId);
    return this.documents.read(
      joinPath(projectId, 'cad/parameters.json'),
      'document',
      parameterSetSchema,
    );
  }

  async saveMessages(
    projectId: string,
    input: MessageHistory,
    requireProject = true,
  ): Promise<void> {
    assertSafeStorageId(projectId, 'projectId');
    if (requireProject) {
      await this.requireProject(projectId);
    }
    await this.documents.write(
      joinPath(projectId, 'chat/messages.json'),
      parseMessages(input),
    );
  }

  async getMessages(projectId: string): Promise<MessageHistory | undefined> {
    await this.requireProject(projectId);
    return this.documents.read(
      joinPath(projectId, 'chat/messages.json'),
      'messages',
      messageHistorySchema,
    );
  }

  async saveRunMessages(
    projectId: string,
    runId: string,
    input: MessageHistory,
  ): Promise<void> {
    await this.requireProject(projectId);
    assertSafeStorageId(runId, 'runId');
    const run = await this.getRun(projectId, runId);
    if (run === undefined) {
      throw new StorageConflict(`Run ${runId} does not exist.`);
    }
    await this.documents.write(
      joinPath(this.runRoot(projectId, runId), 'messages.json'),
      parseMessages(input),
    );
  }

  async getRunMessages(
    projectId: string,
    runId: string,
  ): Promise<MessageHistory | undefined> {
    await this.requireProject(projectId);
    assertSafeStorageId(runId, 'runId');
    return this.documents.read(
      joinPath(this.runRoot(projectId, runId), 'messages.json'),
      'messages',
      messageHistorySchema,
    );
  }

  async saveRun(projectId: string, input: StoredRun): Promise<void> {
    await this.requireProject(projectId);
    const storedRun = await validateStoredRun(projectId, input);
    const previous = await this.getRun(projectId, storedRun.run.id);
    if (previous !== undefined) {
      validateMutableRunUpdate(previous, storedRun);
    }
    const base = this.runRoot(projectId, storedRun.run.id);
    const artifactPaths = new Set<string>();
    for (const artifact of storedRun.artifacts) {
      const path = artifactRelativePath(artifact.metadata);
      if (artifactPaths.has(path)) {
        throw new StorageConflict(`Multiple artifacts resolve to ${path}.`);
      }
      artifactPaths.add(path);
      const document = structuredArtifactDocument(artifact.metadata, storedRun);
      if (isStructuredArtifact(artifact.metadata)) {
        if (document === undefined) {
          throw new StorageConflict(
            `Structured artifact ${artifact.metadata.id} has no matching run document.`,
          );
        }
        const canonical = encodeText(`${JSON.stringify(document)}\n`);
        if (!equalBytes(canonical, artifact.bytes)) {
          throw new StorageConflict(
            `Structured artifact ${artifact.metadata.id} must use canonical JSON bytes.`,
          );
        }
      }
    }
    await this.documents.write(joinPath(base, 'events.json'), {
      schemaVersion: SCHEMA_VERSION,
      runId: storedRun.run.id,
      events: storedRun.events,
    });
    await this.documents.write(joinPath(base, 'artifacts.json'), {
      schemaVersion: SCHEMA_VERSION,
      runId: storedRun.run.id,
      artifacts: storedRun.artifacts.map(({ metadata }) => metadata),
    });
    await this.writeRunDocuments(base, storedRun);
    for (const artifact of storedRun.artifacts) {
      await this.files.write(
        joinPath(base, artifactRelativePath(artifact.metadata)),
        artifact.bytes,
      );
    }
    await this.documents.write(joinPath(base, 'run.json'), storedRun.run);
  }

  async getRun(
    projectId: string,
    runId: string,
  ): Promise<StoredRun | undefined> {
    assertSafeStorageId(projectId, 'projectId');
    assertSafeStorageId(runId, 'runId');
    const base = this.runRoot(projectId, runId);
    const run = await this.documents.read(
      joinPath(base, 'run.json'),
      'run',
      cadRunSchema,
    );
    if (run === undefined) {
      return undefined;
    }
    const eventLog = await this.documents.read(
      joinPath(base, 'events.json'),
      'event-log',
      workflowEventLogSchema,
    );
    const artifactIndex = await this.documents.read(
      joinPath(base, 'artifacts.json'),
      'artifact-index',
      artifactIndexSchema,
    );
    const events = eventLog?.events ?? [];
    validateEventSequence(run.id, events);
    const artifacts = await Promise.all(
      (artifactIndex?.artifacts ?? []).map(async (metadata) => {
        const bytes = await this.files.read(
          joinPath(base, artifactRelativePath(metadata)),
        );
        if (bytes === undefined) {
          throw new CorruptStoredData(`Artifact ${metadata.id} is missing.`);
        }
        return { metadata, bytes };
      }),
    );
    return validateStoredRun(projectId, {
      run,
      events,
      artifacts,
      ...(await this.readRunDocuments(base)),
    });
  }

  async listRuns(projectId: string): Promise<CadRun[]> {
    await this.requireProject(projectId);
    const prefix = joinPath(projectId, 'runs/');
    const paths = (await this.files.list(prefix)).filter(
      (path) => path.endsWith('/run.json') && !isInternalTransactionFile(path),
    );
    const runs = await Promise.all(
      paths.map((path) => this.documents.read(path, 'run', cadRunSchema)),
    );
    return runs
      .filter((run): run is CadRun => run !== undefined)
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  }

  async appendEvent(
    projectId: string,
    runId: string,
    input: WorkflowEventRecord,
  ): Promise<void> {
    const storedRun = await this.getRun(projectId, runId);
    if (storedRun === undefined) {
      throw new StorageConflict(`Run ${runId} does not exist.`);
    }
    if (storedRun.run.status !== 'active') {
      throw new StorageConflict(`Run ${runId} event log is immutable.`);
    }
    const event = workflowEventRecordSchema.parse(input);
    const events = [...storedRun.events, event];
    validateEventSequence(runId, events);
    await this.documents.write(
      joinPath(this.runRoot(projectId, runId), 'events.json'),
      {
        schemaVersion: SCHEMA_VERSION,
        runId,
        events,
      },
    );
  }

  async getEvents(
    projectId: string,
    runId: string,
  ): Promise<WorkflowEventRecord[]> {
    const run = await this.getRun(projectId, runId);
    return run?.events ?? [];
  }

  async getArtifact(
    projectId: string,
    runId: string,
    artifactId: string,
  ): Promise<BinaryArtifact | undefined> {
    assertSafeStorageId(artifactId, 'artifactId');
    const run = await this.getRun(projectId, runId);
    return run?.artifacts.find(({ metadata }) => metadata.id === artifactId);
  }

  async saveAttachment(
    projectId: string,
    input: StoredAttachment,
  ): Promise<void> {
    await this.requireProject(projectId);
    const metadata = attachmentSchema.parse(input.metadata) as Attachment;
    const base = joinPath(projectId, 'attachments', metadata.id);
    if (
      (await this.documents.read(
        joinPath(base, 'metadata.json'),
        'document',
        attachmentSchema,
      )) !== undefined
    ) {
      throw new StorageConflict(`Attachment ${metadata.id} is immutable.`);
    }
    if (
      metadata.byteLength !== input.bytes.byteLength ||
      (await sha256(input.bytes)) !== metadata.sha256
    ) {
      throw new StorageConflict(
        `Attachment ${metadata.id} integrity mismatch.`,
      );
    }
    await this.documents.write(joinPath(base, 'metadata.json'), metadata);
    await this.files.write(joinPath(base, 'payload.bin'), input.bytes);
  }

  async getAttachment(
    projectId: string,
    attachmentId: string,
  ): Promise<StoredAttachment | undefined> {
    assertSafeStorageId(projectId, 'projectId');
    assertSafeStorageId(attachmentId, 'attachmentId');
    const base = joinPath(projectId, 'attachments', attachmentId);
    const metadata = await this.documents.read(
      joinPath(base, 'metadata.json'),
      'document',
      attachmentSchema,
    );
    if (metadata === undefined) return undefined;
    const bytes = await this.files.read(joinPath(base, 'payload.bin'));
    if (bytes === undefined || (await sha256(bytes)) !== metadata.sha256) {
      throw new CorruptStoredData(
        `Attachment ${attachmentId} failed integrity validation.`,
      );
    }
    return { metadata, bytes };
  }

  async listAttachments(projectId: string): Promise<Attachment[]> {
    await this.requireProject(projectId);
    const prefix = joinPath(projectId, 'attachments/');
    const paths = (await this.files.list(prefix)).filter((path) =>
      path.endsWith('/metadata.json'),
    );
    const values = await Promise.all(
      paths.map((path) =>
        this.documents.read(path, 'document', attachmentSchema),
      ),
    );
    return values.filter((value): value is Attachment => value !== undefined);
  }

  async exportProject(projectId: string): Promise<Uint8Array> {
    const project = await this.requireProject(projectId);
    const prefix = `${projectId}/`;
    const paths = (await this.files.list(prefix)).filter(
      (path) => !isInternalTransactionFile(path),
    );
    const files = new Map<string, Uint8Array>();
    for (const path of paths) {
      const bytes = await this.files.read(path);
      if (bytes !== undefined) {
        files.set(path, bytes);
      }
    }
    return createProjectArchive(project, files, this.now());
  }

  async preflightImport(archive: Uint8Array): Promise<ImportPreflight> {
    const validated = await validateProjectArchive(archive);
    const prefix = `${validated.manifest.projectId}/`;
    const duplicate = (await this.files.list(prefix)).length > 0;
    return toImportPreflight(validated, duplicate);
  }

  async importProject(archive: Uint8Array): Promise<CadProject> {
    const validated = await validateProjectArchive(archive);
    const preflight = toImportPreflight(
      validated,
      (await this.files.list(`${validated.manifest.projectId}/`)).length > 0,
    );
    assertImportIsUnique(preflight);
    const projectPrefix = `${validated.manifest.projectId}/`;
    const projectPath = this.projectPath(validated.manifest.projectId);
    const ordered = [...validated.files.entries()].sort(([left], [right]) => {
      if (left === projectPath) return 1;
      if (right === projectPath) return -1;
      return left.localeCompare(right);
    });
    try {
      for (const [path, bytes] of ordered) {
        await this.files.write(path, bytes);
      }
      const project = await this.getProject(validated.manifest.projectId);
      if (project === undefined) {
        throw new CorruptStoredData('Imported archive has no project.json.');
      }
      if (
        project.id !== validated.manifest.projectId ||
        project.name !== validated.manifest.projectName
      ) {
        throw new CorruptStoredData(
          'Imported project identity does not match its ZIP manifest.',
        );
      }
      return project;
    } catch (error) {
      for (const path of await this.files.list(projectPrefix)) {
        await this.files.remove(path);
      }
      throw error;
    }
  }

  getRecoveryDiagnostics(): readonly RecoveryDiagnostic[] {
    return this.diagnostics;
  }

  async saveModelProfileSettings(input: ModelProfileSettings): Promise<void> {
    await this.documents.write(
      'settings/model-profiles.json',
      modelProfileSettingsSchema.parse(input),
    );
  }

  async getModelProfileSettings(): Promise<ModelProfileSettings | undefined> {
    return this.documents.read(
      'settings/model-profiles.json',
      'document',
      modelProfileSettingsSchema,
    );
  }

  private projectPath(projectId: string): string {
    return joinPath(projectId, 'project.json');
  }

  private revisionPath(projectId: string, revisionId: string): string {
    return joinPath(projectId, 'revisions', revisionId, 'revision.json');
  }

  private runRoot(projectId: string, runId: string): string {
    return joinPath(projectId, 'runs', runId);
  }

  private async requireProject(projectId: string): Promise<CadProject> {
    const project = await this.getProject(projectId);
    if (project === undefined) {
      throw new StorageConflict(`Project ${projectId} does not exist.`);
    }
    return project;
  }

  private async writeRunDocuments(base: string, storedRun: StoredRun) {
    if (storedRun.research !== undefined) {
      await this.documents.write(
        joinPath(base, 'research.json'),
        storedRun.research,
      );
    }
    if (storedRun.designBrief !== undefined) {
      await this.documents.write(
        joinPath(base, 'design-brief.json'),
        storedRun.designBrief,
      );
    }
    if (storedRun.colorPlan !== undefined) {
      await this.documents.write(
        joinPath(base, 'color-plan.json'),
        storedRun.colorPlan,
      );
    }
    if (storedRun.buildReport !== undefined) {
      await this.documents.write(
        joinPath(base, 'build-report.json'),
        storedRun.buildReport,
      );
    }
    if (storedRun.qaReport !== undefined) {
      await this.documents.write(
        joinPath(base, 'qa-report.json'),
        storedRun.qaReport,
      );
    }
  }

  private async readRunDocuments(base: string) {
    const [research, designBrief, colorPlan, buildReport, qaReport] =
      await Promise.all([
        this.documents.read(
          joinPath(base, 'research.json'),
          'document',
          researchPacketSchema,
        ),
        this.documents.read(
          joinPath(base, 'design-brief.json'),
          'document',
          designBriefSchema,
        ),
        this.documents.read(
          joinPath(base, 'color-plan.json'),
          'document',
          colorRegionPlanSchema,
        ),
        this.documents.read(
          joinPath(base, 'build-report.json'),
          'document',
          versionedJsonDocumentSchema,
        ),
        this.documents.read(
          joinPath(base, 'qa-report.json'),
          'document',
          qaReportSchema,
        ),
      ]);
    return {
      ...(research === undefined ? {} : { research }),
      ...(designBrief === undefined ? {} : { designBrief }),
      ...(colorPlan === undefined ? {} : { colorPlan }),
      ...(buildReport === undefined ? {} : { buildReport }),
      ...(qaReport === undefined ? {} : { qaReport }),
    };
  }
}
