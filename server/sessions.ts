import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import {
  SessionManager,
  parseSessionEntries,
  type SessionInfo,
} from '@amagine3d/a3d-runtime';

import {
  BUNDLED_POMODORO_SESSION_ID,
  type ArtifactCollection,
  type ChatMessage,
  type SessionCatalog,
  type SessionSummary,
  type WorkspaceStorage,
} from '../src/types.ts';
import { USER_SESSION_ID } from '../src/session-id.ts';
import { scanArtifacts } from './artifacts.ts';
import { bundledPomodoroArtifacts } from './bundled-workspace.ts';
import { discoverModelBuilds } from './model-builds.ts';
import {
  CHAT_TURN_CUSTOM_TYPE,
  restoreChatTurn,
} from '../src/lib/chat-turn.ts';

const BUILTIN_CREATED_AT = '2026-08-19T15:34:44.000Z';
const INTERNAL_PROMPT_SUFFIX =
  /\n*<(?:uploaded_image_files|web_reference_(?:mode|repair)|visual_validation_(?:required|repair))\b[\s\S]*$/u;

export const BUILTIN_POMODORO_SESSION: SessionSummary = {
  createdAt: BUILTIN_CREATED_AT,
  id: BUNDLED_POMODORO_SESSION_ID,
  kind: 'builtin',
  persisted: true,
  readOnly: true,
  title: 'Amagine3D Pomodoro Timer',
  updatedAt: BUILTIN_CREATED_AT,
};

function visibleUserText(value: string): string {
  return value.replace(INTERNAL_PROMPT_SUFFIX, '').trim();
}

function cleanTitle(value: string): string {
  const firstLine = visibleUserText(value)
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

export async function listWorkspaceStorage(
  sessionRoot: string,
  workspaceRoot: string,
  bundledPomodoroRoot: string,
): Promise<WorkspaceStorage> {
  const catalog = await listSessionCatalog(sessionRoot);
  const groups = await Promise.all(
    catalog.sessions.map(async (session) => {
      const collection = await artifactsForSession(
        workspaceRoot,
        bundledPomodoroRoot,
        session.id,
      );
      return collection ? { ...collection, session } : undefined;
    }),
  );
  return {
    groups: groups.filter((group) => group !== undefined),
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
  for (const entry of entries) {
    if (entry.type === 'custom' && entry.customType === CHAT_TURN_CUSTOM_TYPE) {
      const turn = restoreChatTurn(entry.data);
      if (turn) messages.push({ id: entry.id, role: 'assistant', ...turn });
      continue;
    }
    if (entry.type !== 'message' || entry.message.role !== 'user') continue;
    const text = visibleUserText(messageText(entry.message.content));
    if (!text) continue;
    messages.push({
      id: entry.id,
      role: 'user',
      text,
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
  const scannedArtifacts = await scanArtifacts(root);
  const featuredPaths = new Set(
    (await discoverModelBuilds(root, scannedArtifacts)).map(
      ({ displayPreviewPath }) => displayPreviewPath,
    ),
  );
  const artifacts = scannedArtifacts.map((artifact) => ({
    ...artifact,
    ...(featuredPaths.has(artifact.path) ? { featured: true } : {}),
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
