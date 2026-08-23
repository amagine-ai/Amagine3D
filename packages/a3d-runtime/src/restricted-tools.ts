import { constants } from 'node:fs';
import {
  access,
  lstat,
  mkdir,
  readFile,
  writeFile,
} from 'node:fs/promises';
import { isAbsolute, relative, resolve, sep } from 'node:path';

import {
  createEditToolDefinition,
  createWriteToolDefinition,
  type CreateAgentSessionOptions,
} from '@earendil-works/pi-coding-agent';

function isInside(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path));
}

/**
 * Resolve a mutation target and reject traversal, symlink and hard-link escapes.
 * The target may not exist yet, but every existing path segment must be a normal
 * entry below the writable root.
 */
export async function assertWritablePath(
  writableRoot: string,
  target: string,
): Promise<string> {
  const root = resolve(writableRoot);
  const candidate = resolve(target);
  if (!isInside(root, candidate)) {
    throw new Error('File mutations are limited to the current session artifact directory.');
  }

  const path = relative(root, candidate);
  let cursor = root;
  for (const part of path.split(sep).filter(Boolean)) {
    cursor = resolve(cursor, part);
    try {
      const entry = await lstat(cursor);
      if (entry.isSymbolicLink()) {
        throw new Error('File mutations through symbolic links are not allowed.');
      }
      if (cursor === candidate && entry.isFile() && entry.nlink > 1) {
        throw new Error('File mutations through hard links are not allowed.');
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') break;
      throw error;
    }
  }
  return candidate;
}

export function createRestrictedToolDefinitions(writableRoot: string) {
  const editOperations = {
    async access(path: string) {
      await access(await assertWritablePath(writableRoot, path), constants.R_OK | constants.W_OK);
    },
    async readFile(path: string) {
      return readFile(await assertWritablePath(writableRoot, path));
    },
    async writeFile(path: string, content: string) {
      await writeFile(await assertWritablePath(writableRoot, path), content);
    },
  };
  const writeOperations = {
    async mkdir(path: string) {
      await mkdir(await assertWritablePath(writableRoot, path), { recursive: true });
    },
    async writeFile(path: string, content: string) {
      await writeFile(await assertWritablePath(writableRoot, path), content);
    },
  };

  return [
    createEditToolDefinition(writableRoot, { operations: editOperations }),
    createWriteToolDefinition(writableRoot, { operations: writeOperations }),
  ] as unknown as NonNullable<CreateAgentSessionOptions['customTools']>;
}
