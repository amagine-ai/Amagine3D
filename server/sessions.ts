import { randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import {
  BUNDLED_POMODORO_SESSION_ID,
  type ArtifactCollection,
  type ChatMessage,
  type ChatTurn,
  type SessionCatalog,
  type SessionSummary,
  type WorkspaceStorage,
} from '../src/types.ts';
import { USER_SESSION_ID } from '../src/session-id.ts';
import { scanArtifacts } from './artifacts.ts';
import { bundledPomodoroArtifacts } from './bundled-workspace.ts';
import { discoverModelBuilds } from './model-builds.ts';

const SESSION_VERSION = 1;
const BUILTIN_CREATED_AT = '2026-08-19T15:34:44.000Z';

interface StoredSession {
  codexThreadId?: string;
  createdAt: string;
  id: string;
  messages: ChatMessage[];
  updatedAt: string;
  version: typeof SESSION_VERSION;
}

export interface UserSessionInfo {
  createdAt: string;
  id: string;
  path: string;
  updatedAt: string;
}

export const BUILTIN_POMODORO_SESSION: SessionSummary = {
  createdAt: BUILTIN_CREATED_AT,
  id: BUNDLED_POMODORO_SESSION_ID,
  kind: 'builtin',
  persisted: true,
  readOnly: true,
  title: 'Amagine3D Pomodoro Timer',
  updatedAt: BUILTIN_CREATED_AT,
};

function sessionPath(sessionRoot: string, sessionId: string): string {
  return join(sessionRoot, `${sessionId}.json`);
}

function cleanTitle(value: string): string {
  const firstLine = value
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .find(Boolean);
  if (!firstLine) return 'Image CAD session';
  return firstLine.length > 60 ? `${firstLine.slice(0, 59)}…` : firstLine;
}

function validStoredSession(value: unknown): value is StoredSession {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<StoredSession>;
  return (
    item.version === SESSION_VERSION &&
    typeof item.id === 'string' &&
    USER_SESSION_ID.test(item.id) &&
    typeof item.createdAt === 'string' &&
    typeof item.updatedAt === 'string' &&
    Array.isArray(item.messages) &&
    (item.codexThreadId === undefined || typeof item.codexThreadId === 'string')
  );
}

async function readStoredSession(path: string): Promise<StoredSession | undefined> {
  try {
    const value: unknown = JSON.parse(await readFile(path, 'utf8'));
    return validStoredSession(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

async function writeStoredSession(
  sessionRoot: string,
  session: StoredSession,
): Promise<void> {
  await mkdir(sessionRoot, { recursive: true });
  const destination = sessionPath(sessionRoot, session.id);
  const temporary = join(sessionRoot, `.${session.id}.${randomUUID()}.tmp`);
  await writeFile(temporary, `${JSON.stringify(session, null, 2)}\n`, {
    flag: 'wx',
  });
  await rename(temporary, destination);
}

async function loadOrCreateSession(
  sessionRoot: string,
  sessionId: string,
): Promise<StoredSession> {
  if (!USER_SESSION_ID.test(sessionId)) throw new Error('Invalid session id.');
  const existing = await readStoredSession(sessionPath(sessionRoot, sessionId));
  if (existing) return existing;
  const timestamp = new Date().toISOString();
  return {
    createdAt: timestamp,
    id: sessionId,
    messages: [],
    updatedAt: timestamp,
    version: SESSION_VERSION,
  };
}

function summary(session: StoredSession): SessionSummary {
  const firstUserMessage = session.messages.find(
    (message) => message.role === 'user',
  );
  return {
    createdAt: session.createdAt,
    id: session.id,
    kind: 'user',
    persisted: true,
    readOnly: false,
    title: cleanTitle(firstUserMessage?.text ?? ''),
    updatedAt: session.updatedAt,
  };
}

async function storedSessions(
  sessionRoot: string,
): Promise<Array<{ path: string; session: StoredSession }>> {
  let names: string[];
  try {
    names = await readdir(sessionRoot);
  } catch {
    return [];
  }
  const loaded = await Promise.all(
    names
      .filter((name) => name.endsWith('.json'))
      .map(async (name) => {
        const path = join(sessionRoot, name);
        const session = await readStoredSession(path);
        return session ? { path, session } : undefined;
      }),
  );
  return loaded
    .filter((item) => item !== undefined)
    .sort((left, right) =>
      right.session.updatedAt.localeCompare(left.session.updatedAt),
    );
}

export function sessionWorkspaceRoot(
  workspaceRoot: string,
  sessionId: string,
): string | undefined {
  return USER_SESSION_ID.test(sessionId)
    ? join(workspaceRoot, 'sessions', sessionId)
    : undefined;
}

export async function appendSessionUserMessage(
  sessionRoot: string,
  sessionId: string,
  text: string,
): Promise<void> {
  const session = await loadOrCreateSession(sessionRoot, sessionId);
  session.messages.push({ id: randomUUID(), role: 'user', text });
  session.updatedAt = new Date().toISOString();
  await writeStoredSession(sessionRoot, session);
}

export async function appendSessionAssistantTurn(
  sessionRoot: string,
  sessionId: string,
  turn: ChatTurn,
): Promise<void> {
  const session = await loadOrCreateSession(sessionRoot, sessionId);
  session.messages.push({ id: randomUUID(), role: 'assistant', ...turn });
  session.updatedAt = new Date().toISOString();
  await writeStoredSession(sessionRoot, session);
}

export async function readSessionThreadId(
  sessionRoot: string,
  sessionId: string,
): Promise<string | undefined> {
  return (await readStoredSession(sessionPath(sessionRoot, sessionId)))
    ?.codexThreadId;
}

export async function setSessionThreadId(
  sessionRoot: string,
  sessionId: string,
  threadId: string,
): Promise<void> {
  const session = await loadOrCreateSession(sessionRoot, sessionId);
  session.codexThreadId = threadId;
  session.updatedAt = new Date().toISOString();
  await writeStoredSession(sessionRoot, session);
}

export async function listSessionCatalog(
  sessionRoot: string,
): Promise<SessionCatalog> {
  const userSessions = (await storedSessions(sessionRoot)).map(({ session }) =>
    summary(session),
  );
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
  return { groups: groups.filter((group) => group !== undefined) };
}

export async function findUserSession(
  sessionRoot: string,
  sessionId: string,
): Promise<UserSessionInfo | undefined> {
  if (!USER_SESSION_ID.test(sessionId)) return undefined;
  const path = sessionPath(sessionRoot, sessionId);
  const session = await readStoredSession(path);
  return session
    ? {
        createdAt: session.createdAt,
        id: session.id,
        path,
        updatedAt: session.updatedAt,
      }
    : undefined;
}

export async function readSessionMessages(path: string): Promise<ChatMessage[]> {
  return (await readStoredSession(path))?.messages ?? [];
}

export async function userSessionArtifacts(
  workspaceRoot: string,
  sessionId: string,
): Promise<ArtifactCollection | undefined> {
  const root = sessionWorkspaceRoot(workspaceRoot, sessionId);
  if (!root) return undefined;
  const scannedArtifacts = await scanArtifacts(root);
  const builds = await discoverModelBuilds(root, scannedArtifacts);
  const featuredPaths = new Set(
    builds.map(({ displayPreviewPath }) => displayPreviewPath),
  );
  const primaryPaths = new Set(
    builds.flatMap(({ topLevelArtifactPaths }) => topLevelArtifactPaths),
  );
  const artifacts = scannedArtifacts.map((artifact) => ({
    ...artifact,
    ...(featuredPaths.has(artifact.path) ? { featured: true } : {}),
    ...(primaryPaths.has(artifact.path) ? { primary: true } : {}),
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
