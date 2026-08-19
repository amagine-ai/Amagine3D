import { openOpfsFileStore, type FileStore } from './file-store';
import { assertSafeArchivePath, baseName } from './path';

export type OpfsWorkspaceFile = {
  path: string;
  fileName: string;
  projectId: string;
  runId?: string;
  category: 'model' | 'execution';
  byteLength: number;
};

const MODEL_EXTENSIONS = new Set([
  '.3mf',
  '.glb',
  '.obj',
  '.py',
  '.step',
  '.stl',
  '.stp',
]);

function extension(path: string): string {
  const name = baseName(path).toLowerCase();
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot);
}

function isInternalPath(path: string): boolean {
  return (
    path.endsWith('.commit') ||
    path.includes('.tmp-') ||
    path.includes('/corrupt/') ||
    path.includes('/.cad-worker/') ||
    path.startsWith('.imports/')
  );
}

function classify(path: string): OpfsWorkspaceFile['category'] | undefined {
  const suffix = extension(path);
  if (MODEL_EXTENSIONS.has(suffix)) return 'model';
  return suffix === '.json' ? 'execution' : undefined;
}

function identifiers(path: string): { projectId: string; runId?: string } {
  const segments = path.split('/');
  const projectId = segments[0] ?? 'unknown';
  const runIndex = segments.indexOf('runs');
  const runId = runIndex === -1 ? undefined : segments[runIndex + 1];
  return {
    projectId,
    ...(runId === undefined ? {} : { runId }),
  };
}

export async function listWorkspaceFiles(
  files: FileStore,
): Promise<OpfsWorkspaceFile[]> {
  const entries: OpfsWorkspaceFile[] = [];
  for (const path of await files.list()) {
    if (isInternalPath(path)) continue;
    const category = classify(path);
    if (category === undefined) continue;
    const byteLength = await files.size(path);
    if (byteLength === undefined) continue;
    entries.push({
      path,
      fileName: baseName(path),
      category,
      byteLength,
      ...identifiers(path),
    });
  }
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}

export async function listOpfsWorkspaceFiles(): Promise<OpfsWorkspaceFile[]> {
  return listWorkspaceFiles(await openOpfsFileStore());
}

export async function readWorkspaceFile(
  files: FileStore,
  path: string,
): Promise<Uint8Array | undefined> {
  const allowed = new Set(
    (await listWorkspaceFiles(files)).map(({ path: entry }) => entry),
  );
  assertSafeArchivePath(path);
  if (!allowed.has(path)) {
    throw new Error(`OPFS read refused unknown or internal path: ${path}`);
  }
  return files.read(path);
}

export async function readOpfsWorkspaceFile(
  path: string,
): Promise<Uint8Array | undefined> {
  return readWorkspaceFile(await openOpfsFileStore(), path);
}

async function removeLogicalFile(
  files: FileStore,
  path: string,
): Promise<void> {
  await files.remove(`${path}.commit`);
  for (const generation of await files.list(`${path}.tmp-`)) {
    await files.remove(generation);
  }
  await files.remove(path);
}

export async function removeWorkspaceFiles(
  files: FileStore,
  paths: readonly string[],
): Promise<void> {
  const allowed = new Set(
    (await listWorkspaceFiles(files)).map(({ path }) => path),
  );
  for (const path of new Set(paths)) {
    assertSafeArchivePath(path);
    if (!allowed.has(path)) {
      throw new Error(`OPFS cleanup refused unknown or internal path: ${path}`);
    }
    await removeLogicalFile(files, path);
  }
}

export async function removeOpfsWorkspaceFiles(
  paths: readonly string[],
): Promise<void> {
  await removeWorkspaceFiles(await openOpfsFileStore(), paths);
}
