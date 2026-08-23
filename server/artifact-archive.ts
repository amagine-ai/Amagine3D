import { readFile, realpath } from 'node:fs/promises';
import { relative, sep } from 'node:path';

import { zipSync, type Zippable } from 'fflate';

import { resolveArtifactPath } from './artifacts.ts';

export const MAX_ARCHIVE_FILES = 500;

function toPosixPath(value: string): string {
  return value.split(sep).join('/');
}

export async function createArtifactArchive(
  workspaceRoot: string,
  requestedPaths: string[],
): Promise<Uint8Array | undefined> {
  const canonicalRoot = await realpath(workspaceRoot);
  const entries: Zippable = {};

  for (const requestedPath of new Set(requestedPaths)) {
    const artifactPath = await resolveArtifactPath(
      workspaceRoot,
      requestedPath,
    );
    if (!artifactPath) return undefined;
    const archivePath = toPosixPath(relative(canonicalRoot, artifactPath));
    entries[archivePath] = new Uint8Array(await readFile(artifactPath));
  }

  return zipSync(entries, { level: 6 });
}
