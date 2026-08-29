export const API_VERSION = 9;
export const BUNDLED_POMODORO_SESSION_ID = 'builtin:amagine3d-pomodoro';
export const ACCEPTED_IMAGE_TYPES = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
] as const;
export const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
export const MAX_IMAGE_COUNT = 4;
export const MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024;

export type AcceptedImageType = (typeof ACCEPTED_IMAGE_TYPES)[number];
export type ArtifactKind = 'image' | 'model' | 'report' | 'source' | 'other';
export type PreviewFormat = '3mf' | 'glb' | 'stl';

export interface ImageAttachment {
  data: string;
  mimeType: AcceptedImageType;
  name: string;
}

export interface ChatImagePreview {
  name: string;
  url: string;
}

export type ChatStepStatus =
  | 'cancelled'
  | 'completed'
  | 'failed'
  | 'running';

export type ChatTurnTerminalStatus = Exclude<ChatStepStatus, 'running'>;

export interface ChatStep {
  id: string;
  label: string;
  occurredAt: number;
  progressText?: string;
  stage: string;
  status: ChatStepStatus;
}

export interface ChatTurn {
  finishedAt?: number;
  replyText: string;
  steps: ChatStep[];
}

export interface UserChatMessage {
  id: string;
  images?: ChatImagePreview[];
  role: 'user';
  text: string;
}

export interface AssistantChatMessage extends ChatTurn {
  id: string;
  role: 'assistant';
}

export type ChatMessage = AssistantChatMessage | UserChatMessage;

export interface SkillSummary {
  description: string;
  name: string;
}

export interface ArtifactSummary {
  featured?: boolean;
  format?: PreviewFormat;
  kind: ArtifactKind;
  modifiedAt: string;
  name: string;
  path: string;
  readOnly?: boolean;
  size: number;
  url: string;
}

export interface ArtifactWorkspace {
  id: string;
  name: string;
  path: string;
  readOnly: boolean;
  sessionId: string;
}

export interface ArtifactCollection {
  artifacts: ArtifactSummary[];
  artifactWorkspace: ArtifactWorkspace;
}

export interface ModelParameter {
  affects: string[];
  defaultValue: number;
  group?: string;
  groupZh?: string;
  id: string;
  kind: 'integer' | 'number';
  label: string;
  labelZh?: string;
  maximum: number;
  minimum: number;
  name: string;
  step: number;
  unit?: string;
  value: number;
}

export interface ParameterModel {
  artifactPaths: string[];
  displayPreviewPath: string;
  modelId: string;
  parameterError?: string;
  parameters: ModelParameter[];
  primaryPreviewPath: string;
  reportPath: string;
  sourceHash: string;
  sourcePath: string;
}

export interface ParameterCollection {
  models: ParameterModel[];
}

export interface ParameterBuildResult
  extends ArtifactCollection,
    ParameterCollection {}

export type SessionKind = 'builtin' | 'user';

export interface SessionSummary {
  createdAt: string;
  id: string;
  kind: SessionKind;
  persisted: boolean;
  readOnly: boolean;
  title: string;
  updatedAt: string;
}

export interface SessionCatalog {
  initialSessionId: string;
  sessions: SessionSummary[];
}

export interface SessionDetail extends ArtifactCollection {
  messages: ChatMessage[];
  session: SessionSummary;
}

export interface StorageSessionGroup extends ArtifactCollection {
  session: SessionSummary;
}

export interface WorkspaceStorage {
  groups: StorageSessionGroup[];
}

export interface PythonHealth {
  executable: string | null;
  ready: boolean;
  version: string | null;
}

export interface HealthResponse {
  apiVersion: number;
  configured: boolean;
  model: string;
  python: PythonHealth;
  runtimeError?: string;
  runtimeReady: boolean;
  skills: SkillSummary[];
  webSearchConfigured: boolean;
  workspace: string;
}

export type AgentEvent =
  | { type: 'step'; step: ChatStep }
  | { type: 'step_delta'; content: string; stepId: string }
  | {
      type: 'artifacts';
      artifacts: ArtifactSummary[];
      artifactWorkspace?: ArtifactWorkspace;
      sessionId: string;
    }
  | {
      type: 'complete';
      content: string;
      finishedAt: number;
      sessionId: string;
      sourceStepId?: string;
    }
  | { type: 'error'; message: string; code?: string; finishedAt: number };
