import {
  projectArchiveManifestSchema,
  type CadProject,
  type ProjectArchiveManifest,
} from '@amagine3d/cad-protocol';

import { ArchiveIntegrityMismatch, StorageConflict } from './errors';
import { decodeText, encodeText, sha256 } from './hash';
import { assertSafeArchivePath, assertSafeStorageId } from './path';
import type { ImportPreflight } from './types';
import { decodeZip, encodeZip } from './zip';

export type ValidatedArchive = {
  manifest: ProjectArchiveManifest;
  files: Map<string, Uint8Array>;
};

export async function createProjectArchive(
  project: CadProject,
  files: Map<string, Uint8Array>,
  exportedAt: Date,
): Promise<Uint8Array> {
  assertSafeStorageId(project.id, 'projectId');
  const prefix = `${project.id}/`;
  const entries: ProjectArchiveManifest['entries'] = [];
  for (const [path, bytes] of [...files.entries()].sort(([left], [right]) =>
    left.localeCompare(right),
  )) {
    assertSafeArchivePath(path);
    if (!path.startsWith(prefix)) {
      throw new ArchiveIntegrityMismatch(path, `must start with ${prefix}`);
    }
    entries.push({
      path,
      byteLength: bytes.byteLength,
      sha256: await sha256(bytes),
    });
  }

  const manifest = projectArchiveManifestSchema.parse({
    schemaVersion: 1,
    format: 'amagine3d-project',
    projectId: project.id,
    projectName: project.name,
    exportedAt: exportedAt.toISOString(),
    entries,
  });
  return encodeZip([
    {
      path: 'manifest.json',
      bytes: encodeText(`${JSON.stringify(manifest)}\n`),
    },
    ...[...files.entries()].map(([path, bytes]) => ({ path, bytes })),
  ]);
}

export async function validateProjectArchive(
  archive: Uint8Array,
): Promise<ValidatedArchive> {
  const decoded = decodeZip(archive);
  const manifestBytes = decoded.get('manifest.json');
  if (manifestBytes === undefined) {
    throw new ArchiveIntegrityMismatch('manifest.json', 'missing manifest');
  }

  let manifestInput: unknown;
  try {
    manifestInput = JSON.parse(decodeText(manifestBytes));
  } catch (error) {
    throw new ArchiveIntegrityMismatch(
      'manifest.json',
      error instanceof Error ? error.message : 'invalid JSON',
    );
  }
  const manifest = projectArchiveManifestSchema.parse(manifestInput);
  assertSafeStorageId(manifest.projectId, 'projectId');
  const files = new Map(decoded);
  files.delete('manifest.json');
  if (files.size !== manifest.entries.length) {
    throw new ArchiveIntegrityMismatch(
      'manifest.json',
      'entry count does not match archive',
    );
  }

  const listedPaths = new Set<string>();
  for (const entry of manifest.entries) {
    assertSafeArchivePath(entry.path);
    if (!entry.path.startsWith(`${manifest.projectId}/`)) {
      throw new ArchiveIntegrityMismatch(
        entry.path,
        'entry is outside the project root',
      );
    }
    if (listedPaths.has(entry.path)) {
      throw new ArchiveIntegrityMismatch(entry.path, 'duplicate manifest path');
    }
    listedPaths.add(entry.path);
    const bytes = files.get(entry.path);
    if (bytes === undefined) {
      throw new ArchiveIntegrityMismatch(entry.path, 'missing archive entry');
    }
    if (
      bytes.byteLength !== entry.byteLength ||
      (await sha256(bytes)) !== entry.sha256
    ) {
      throw new ArchiveIntegrityMismatch(entry.path, 'SHA-256 mismatch');
    }
  }
  for (const path of files.keys()) {
    if (!listedPaths.has(path)) {
      throw new ArchiveIntegrityMismatch(path, 'entry is not in the manifest');
    }
  }
  return { manifest, files };
}

export function toImportPreflight(
  validated: ValidatedArchive,
  duplicateProject: boolean,
): ImportPreflight {
  return {
    manifestProjectId: validated.manifest.projectId,
    manifestProjectName: validated.manifest.projectName,
    entryCount: validated.manifest.entries.length,
    totalBytes: validated.manifest.entries.reduce(
      (total, entry) => total + entry.byteLength,
      0,
    ),
    duplicateProject,
  };
}

export function assertImportIsUnique(preflight: ImportPreflight): void {
  if (preflight.duplicateProject) {
    throw new StorageConflict(
      `Project ${preflight.manifestProjectId} already exists.`,
      { projectId: preflight.manifestProjectId },
    );
  }
}
