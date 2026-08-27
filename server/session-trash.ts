import { access } from 'node:fs/promises';
import { isAbsolute, relative, resolve, sep } from 'node:path';

import trash from 'trash';

import { SessionManager } from '@amagine3d/a3d-runtime';

import { USER_SESSION_ID } from '../src/session-id.ts';
import { sessionWorkspaceRoot } from './sessions.ts';

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

  const sessions = new Map(
    (await SessionManager.listAll(sessionRoot))
      .filter(({ id, path }) => USER_SESSION_ID.test(id) && isInside(sessionRoot, path))
      .map((session) => [session.id, session]),
  );
  const trashPaths: string[] = [];
  for (const sessionId of uniqueSessionIds) {
    const session = sessions.get(sessionId);
    if (!session) return undefined;
    trashPaths.push(session.path);
    const workspace = sessionWorkspaceRoot(workspaceRoot, sessionId);
    if (workspace) {
      const path = await existingPath(workspace);
      if (path) trashPaths.push(path);
    }
  }

  await moveToTrash(trashPaths);
  return uniqueSessionIds.length;
}
