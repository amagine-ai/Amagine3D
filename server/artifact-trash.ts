import trash from 'trash';

import { resolveArtifactPath } from './artifacts.ts';

type MoveToTrash = (paths: string[]) => Promise<void>;

export const MAX_TRASH_FILES = 500;

const moveToSystemTrash: MoveToTrash = (paths) =>
  trash(paths, { glob: false });

export async function moveArtifactsToTrash(
  workspaceRoot: string,
  requestedPaths: string[],
  moveToTrash: MoveToTrash = moveToSystemTrash,
): Promise<number | undefined> {
  const artifactPaths: string[] = [];
  for (const requestedPath of new Set(requestedPaths)) {
    const artifactPath = await resolveArtifactPath(
      workspaceRoot,
      requestedPath,
    );
    if (!artifactPath) return undefined;
    artifactPaths.push(artifactPath);
  }
  await moveToTrash(artifactPaths);
  return artifactPaths.length;
}
