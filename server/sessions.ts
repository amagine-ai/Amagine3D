import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import {
  SessionManager,
  parseSessionEntries,
  type SessionInfo,
} from '@earendil-works/pi-coding-agent';

import {
  BUNDLED_POMODORO_SESSION_ID,
  type ArtifactCollection,
  type ChatMessage,
  type SessionCatalog,
  type SessionSummary,
} from '../src/types.ts';
import { scanArtifacts } from './artifacts.ts';
import { bundledPomodoroArtifacts } from './bundled-workspace.ts';
import {
  restoredChatStages,
  RUN_STAGES_CUSTOM_TYPE,
} from '../src/lib/chat-stages.ts';

export const USER_SESSION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

const BUILTIN_CREATED_AT = '2026-08-19T15:34:44.000Z';

export const BUILTIN_POMODORO_SESSION: SessionSummary = {
  createdAt: BUILTIN_CREATED_AT,
  id: BUNDLED_POMODORO_SESSION_ID,
  kind: 'builtin',
  persisted: true,
  readOnly: true,
  title: 'Amagine3D Pomodoro Timer',
  updatedAt: BUILTIN_CREATED_AT,
};

function cleanTitle(value: string): string {
  const firstLine = value
    .replace(/<uploaded_image_files>[\s\S]*$/u, '')
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .find(Boolean);
  if (!firstLine) return 'Untitled CAD session';
  return firstLine.length > 60 ? `${firstLine.slice(0, 59)}…` : firstLine;
}

function sessionSummary(info: SessionInfo): SessionSummary {
  return {
    createdAt: info.created.toISOString(),
    id: info.id,
    kind: 'user',
    persisted: true,
    readOnly: false,
    title: cleanTitle(info.name?.trim() || info.firstMessage),
    updatedAt: info.modified.toISOString(),
  };
}

export function sessionWorkspaceRoot(
  workspaceRoot: string,
  sessionId: string,
): string | undefined {
  return USER_SESSION_ID.test(sessionId)
    ? join(workspaceRoot, 'sessions', sessionId)
    : undefined;
}

export async function listSessionCatalog(
  sessionRoot: string,
): Promise<SessionCatalog> {
  const userSessions = (await SessionManager.listAll(sessionRoot))
    .filter(({ id }) => USER_SESSION_ID.test(id))
    .map(sessionSummary);
  return {
    initialSessionId: userSessions[0]?.id ?? BUNDLED_POMODORO_SESSION_ID,
    sessions: [...userSessions, BUILTIN_POMODORO_SESSION],
  };
}

export async function findUserSession(
  sessionRoot: string,
  sessionId: string,
): Promise<SessionInfo | undefined> {
  if (!USER_SESSION_ID.test(sessionId)) return undefined;
  return (await SessionManager.listAll(sessionRoot)).find(
    ({ id }) => id === sessionId,
  );
}

function messageText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(
      (block): block is { text: string; type: 'text' } =>
        Boolean(
          block &&
            typeof block === 'object' &&
            (block as { type?: unknown }).type === 'text' &&
            typeof (block as { text?: unknown }).text === 'string',
        ),
    )
    .map(({ text }) => text)
    .join('');
}

export async function readSessionMessages(path: string): Promise<ChatMessage[]> {
  const entries = parseSessionEntries(await readFile(path, 'utf8'));
  const messages: ChatMessage[] = [];
  let lastTracedAssistantIndex = -1;
  for (const entry of entries) {
    if (entry.type === 'custom' && entry.customType === RUN_STAGES_CUSTOM_TYPE) {
      const stages = restoredChatStages(entry.data);
      const assistantIndex = messages.findLastIndex(
        (message, index) =>
          index > lastTracedAssistantIndex && message.role === 'assistant',
      );
      if (stages.length === 0) continue;
      if (assistantIndex >= 0) {
        messages[assistantIndex] = { ...messages[assistantIndex]!, stages };
        lastTracedAssistantIndex = assistantIndex;
      } else {
        messages.push({
          id: entry.id,
          role: 'assistant',
          stages,
          state: 'complete',
          text: '',
        });
        lastTracedAssistantIndex = messages.length - 1;
      }
      continue;
    }
    if (entry.type !== 'message') continue;
    const role = entry.message.role;
    if (role !== 'assistant' && role !== 'user') continue;
    const text = messageText(entry.message.content).trim();
    if (!text) continue;
    messages.push({
      id: entry.id,
      role,
      state: 'complete',
      text:
        role === 'user'
          ? text.replace(/\n*<uploaded_image_files>[\s\S]*$/u, '').trim()
          : text,
    });
  }
  return messages;
}

export async function userSessionArtifacts(
  workspaceRoot: string,
  sessionId: string,
): Promise<ArtifactCollection | undefined> {
  const root = sessionWorkspaceRoot(workspaceRoot, sessionId);
  if (!root) return undefined;
  const artifacts = (await scanArtifacts(root)).map((artifact) => ({
    ...artifact,
    url: `/api/sessions/${encodeURIComponent(sessionId)}/artifacts/file?path=${encodeURIComponent(artifact.path)}`,
  }));
  return {
    artifacts,
    artifactWorkspace: {
      id: sessionId,
      name: 'Workspace',
      path: `workspace/sessions/${sessionId}/`,
      readOnly: false,
      sessionId,
    },
  };
}

export async function artifactsForSession(
  workspaceRoot: string,
  bundledPomodoroRoot: string,
  sessionId: string,
): Promise<ArtifactCollection | undefined> {
  if (sessionId === BUNDLED_POMODORO_SESSION_ID) {
    return bundledPomodoroArtifacts(bundledPomodoroRoot);
  }
  return userSessionArtifacts(workspaceRoot, sessionId);
}
