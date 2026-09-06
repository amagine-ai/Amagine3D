import { access } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';

import trash from 'trash';

import { USER_SESSION_ID } from '../src/session-id.ts';
import { findUserSession, sessionWorkspaceRoot } from './sessions.ts';

type MoveToTrash = (paths: string[]) => Promise<void>;

export const MAX_TRASH_SESSIONS = 100;

const moveToSystemTrash: MoveToTrash = (paths) =>
  trash(paths, { glob: false });

function isInside(parent: string, path: string): boolean {
  const relativePath = relative(resolve(parent), resolve(path));
  return (
    relativePath !== '' &&
    !relativePath.startsWith(`..${sep}`) &&
    relativePath !== '..' &&
    !isAbsolute(relativePath)
  );
}

async function existingPath(path: string): Promise<string | undefined> {
  try {
    await access(path);
    return path;
  } catch {
    return undefined;
  }
}

export async function moveSessionsToTrash(
  sessionRoot: string,
  workspaceRoot: string,
  sessionIds: string[],
  moveToTrash: MoveToTrash = moveToSystemTrash,
): Promise<number | undefined> {
  const uniqueSessionIds = [...new Set(sessionIds)];
  if (
    uniqueSessionIds.length === 0 ||
    uniqueSessionIds.length > MAX_TRASH_SESSIONS ||
    uniqueSessionIds.some((sessionId) => !USER_SESSION_ID.test(sessionId))
  ) {
    return undefined;
  }

  const trashPaths: string[] = [];
  for (const sessionId of uniqueSessionIds) {
    const session = await findUserSession(sessionRoot, sessionId);
    if (!session || !isInside(sessionRoot, session.path)) return undefined;
    trashPaths.push(session.path);
    const workspace = sessionWorkspaceRoot(workspaceRoot, sessionId);
    if (workspace) {
      const path = await existingPath(workspace);
      if (path) trashPaths.push(path);
    }
    const codexState = await existingPath(
      join(dirname(sessionRoot), 'codex', sessionId),
    );
    if (codexState) trashPaths.push(codexState);
  }

  await moveToTrash(trashPaths);
  return uniqueSessionIds.length;
}
