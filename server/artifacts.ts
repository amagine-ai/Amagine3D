import { createReadStream } from 'node:fs';
import { opendir, realpath, stat } from 'node:fs/promises';
import { extname, relative, resolve, sep } from 'node:path';

import type {
  ArtifactKind,
  ArtifactSummary,
  PreviewFormat,
} from '../src/types.ts';

const MODEL_EXTENSIONS = new Set(['.3mf', '.glb', '.step', '.stl', '.stp']);
const IMAGE_EXTENSIONS = new Set(['.gif', '.jpeg', '.jpg', '.png', '.webp']);
const REPORT_EXTENSIONS = new Set(['.json', '.md', '.txt']);
const SOURCE_EXTENSIONS = new Set(['.py']);
const IGNORED_DIRECTORIES = new Set([
  '.git',
  '.amagine-state',
  '.venv',
  '__pycache__',
  'node_modules',
]);
const MAX_ARTIFACTS = 500;
const MAX_DEPTH = 8;

function kindForExtension(extension: string): ArtifactKind {
  if (MODEL_EXTENSIONS.has(extension)) return 'model';
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (REPORT_EXTENSIONS.has(extension)) return 'report';
  if (SOURCE_EXTENSIONS.has(extension)) return 'source';
  return 'other';
}

function previewFormat(extension: string): PreviewFormat | undefined {
  if (extension === '.3mf') return '3mf';
  if (extension === '.glb') return 'glb';
  if (extension === '.stl') return 'stl';
  return undefined;
}

function toPosixPath(value: string): string {
  return value.split(sep).join('/');
}

export async function scanArtifacts(
  workspaceRoot: string,
): Promise<ArtifactSummary[]> {
  const artifacts: ArtifactSummary[] = [];

  async function walk(directory: string, depth: number): Promise<void> {
    if (depth > MAX_DEPTH || artifacts.length >= MAX_ARTIFACTS) return;
    let entries;
    try {
      entries = await opendir(directory);
    } catch {
      return;
    }

    for await (const entry of entries) {
      if (artifacts.length >= MAX_ARTIFACTS) return;
      if (entry.name.startsWith('.') || IGNORED_DIRECTORIES.has(entry.name)) {
        continue;
      }
      const absolutePath = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        await walk(absolutePath, depth + 1);
        continue;
      }
      if (!entry.isFile()) continue;
      const extension = extname(entry.name).toLowerCase();
      const kind = kindForExtension(extension);
      if (kind === 'other') continue;
      const metadata = await stat(absolutePath);
      const path = toPosixPath(relative(workspaceRoot, absolutePath));
      artifacts.push({
        ...(previewFormat(extension)
          ? { format: previewFormat(extension) }
          : {}),
        kind,
        modifiedAt: metadata.mtime.toISOString(),
        name: entry.name,
        path,
        size: metadata.size,
        url: `/api/artifacts/file?path=${encodeURIComponent(path)}`,
      });
    }
  }

  await walk(workspaceRoot, 0);
  return artifacts.sort(
    (left, right) =>
      Date.parse(right.modifiedAt) - Date.parse(left.modifiedAt) ||
      left.path.localeCompare(right.path),
  );
}

export async function resolveArtifactPath(
  workspaceRoot: string,
  requestedPath: string,
): Promise<string | undefined> {
  const root = await realpath(workspaceRoot);
  const candidate = resolve(root, requestedPath);
  const relativePath = relative(root, candidate);
  if (
    relativePath === '' ||
    relativePath.startsWith(`..${sep}`) ||
    relativePath === '..'
  ) {
    return undefined;
  }
  try {
    const canonical = await realpath(candidate);
    const canonicalRelative = relative(root, canonical);
    if (
      canonicalRelative.startsWith(`..${sep}`) ||
      canonicalRelative === '..'
    ) {
      return undefined;
    }
    const metadata = await stat(canonical);
    return metadata.isFile() ? canonical : undefined;
  } catch {
    return undefined;
  }
}

export function artifactContentType(path: string): string {
  switch (extname(path).toLowerCase()) {
    case '.3mf':
      return 'model/3mf';
    case '.glb':
      return 'model/gltf-binary';
    case '.gif':
      return 'image/gif';
    case '.jpeg':
    case '.jpg':
      return 'image/jpeg';
    case '.json':
      return 'application/json; charset=utf-8';
    case '.md':
      return 'text/markdown; charset=utf-8';
    case '.png':
      return 'image/png';
    case '.py':
      return 'text/x-python; charset=utf-8';
    case '.step':
    case '.stp':
      return 'model/step';
    case '.stl':
      return 'model/stl';
    case '.txt':
      return 'text/plain; charset=utf-8';
    case '.webp':
      return 'image/webp';
    default:
      return 'application/octet-stream';
  }
}

export { createReadStream };
