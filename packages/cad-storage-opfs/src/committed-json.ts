import type { RecoveryDiagnostic } from './types';
import type { FileStore } from './file-store';
import { CorruptStoredData } from './errors';
import { decodeText, encodeText, equalBytes, sha256 } from './hash';
import {
  migratePersistedDocument,
  type PersistedDocumentKind,
} from './migrations';
import { baseName, joinPath, parentPath } from './path';

type Parser<T> = { parse(value: unknown): T };

type CommitMarker = {
  schemaVersion: 1;
  generation: string;
  sha256: string;
  byteLength: number;
  committedAt: string;
};

type CommittedJsonOptions = {
  now?: () => Date;
  createId?: () => string;
  onDiagnostic?: (diagnostic: RecoveryDiagnostic) => void;
};

function parseJson(bytes: Uint8Array, path: string): unknown {
  try {
    return JSON.parse(decodeText(bytes));
  } catch (error) {
    throw new CorruptStoredData(`Invalid JSON in ${path}.`, {
      path,
      reason: error instanceof Error ? error.message : 'invalid JSON',
    });
  }
}

function parseCommitMarker(value: unknown, path: string): CommitMarker {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new CorruptStoredData(`Invalid commit marker ${path}.`);
  }
  const marker = value as Record<string, unknown>;
  if (
    marker.schemaVersion !== 1 ||
    typeof marker.generation !== 'string' ||
    !/^[A-Za-z0-9._-]+$/u.test(marker.generation) ||
    typeof marker.sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/u.test(marker.sha256) ||
    typeof marker.byteLength !== 'number' ||
    !Number.isInteger(marker.byteLength) ||
    typeof marker.committedAt !== 'string'
  ) {
    throw new CorruptStoredData(`Invalid commit marker ${path}.`);
  }
  return {
    schemaVersion: 1,
    generation: marker.generation,
    sha256: marker.sha256,
    byteLength: marker.byteLength,
    committedAt: marker.committedAt,
  };
}

function isDeferredCleanupError(error: unknown): boolean {
  return (
    error instanceof DOMException && error.name === 'NoModificationAllowedError'
  );
}

export class CommittedJsonStore {
  private readonly now: () => Date;
  private readonly createId: () => string;
  private readonly onDiagnostic: (diagnostic: RecoveryDiagnostic) => void;
  private readonly writeQueues = new Map<string, Promise<void>>();

  constructor(
    private readonly files: FileStore,
    options: CommittedJsonOptions = {},
  ) {
    this.now = options.now ?? (() => new Date());
    this.createId = options.createId ?? (() => crypto.randomUUID());
    this.onDiagnostic = options.onDiagnostic ?? (() => undefined);
  }

  async write<T>(path: string, value: T): Promise<void> {
    const bytes = encodeText(`${JSON.stringify(value)}\n`);
    const previousWrite = this.writeQueues.get(path) ?? Promise.resolve();
    const queuedWrite = previousWrite
      .catch(() => undefined)
      .then(() => this.commitWrite(path, bytes));
    this.writeQueues.set(path, queuedWrite);

    try {
      await queuedWrite;
    } finally {
      if (this.writeQueues.get(path) === queuedWrite) {
        this.writeQueues.delete(path);
      }
    }
  }

  private async commitWrite(path: string, bytes: Uint8Array): Promise<void> {
    const digest = await sha256(bytes);
    const generation = `${baseName(path)}.tmp-${String(this.now().getTime()).padStart(13, '0')}-${this.createId()}`;
    const generationPath = joinPath(parentPath(path), generation);
    const markerPath = `${path}.commit`;
    const marker: CommitMarker = {
      schemaVersion: 1,
      generation,
      sha256: digest,
      byteLength: bytes.byteLength,
      committedAt: this.now().toISOString(),
    };

    await this.files.write(generationPath, bytes);
    await this.files.write(
      markerPath,
      encodeText(`${JSON.stringify(marker)}\n`),
    );
    await this.files.write(path, bytes);
    await this.removeOldGenerations(path, generationPath);
  }

  async read<T>(
    path: string,
    kind: PersistedDocumentKind,
    parser: Parser<T>,
  ): Promise<T | undefined> {
    const markerPath = `${path}.commit`;
    const markerBytes = await this.files.read(markerPath);

    if (markerBytes !== undefined) {
      try {
        const marker = parseCommitMarker(
          parseJson(markerBytes, markerPath),
          markerPath,
        );
        const generationPath = joinPath(parentPath(path), marker.generation);
        const generationBytes = await this.files.read(generationPath);
        if (
          generationBytes !== undefined &&
          generationBytes.byteLength === marker.byteLength &&
          (await sha256(generationBytes)) === marker.sha256
        ) {
          const result = this.parseAndMigrate(
            path,
            kind,
            parser,
            generationBytes,
          );
          if (result.migrated) {
            await this.write(path, result.parsed);
            return result.parsed;
          }
          const canonical = await this.files.read(path);
          if (
            canonical === undefined ||
            !equalBytes(canonical, generationBytes)
          ) {
            if (canonical !== undefined) {
              await this.quarantine(path, canonical);
            }
            await this.files.write(path, generationBytes);
            this.onDiagnostic({
              level: 'warning',
              code: 'canonical-restored',
              path,
              message:
                'Restored the canonical JSON from its committed generation.',
            });
          }
          return result.parsed;
        }
      } catch (error) {
        await this.quarantine(markerPath, markerBytes);
        if (!(error instanceof CorruptStoredData)) {
          throw error;
        }
      }
    }

    const recovered = await this.recoverGeneration(path, kind, parser);
    if (recovered !== undefined) {
      return recovered;
    }

    const canonical = await this.files.read(path);
    if (canonical === undefined) {
      return undefined;
    }

    try {
      const result = this.parseAndMigrate(path, kind, parser, canonical);
      await this.write(path, result.parsed);
      return result.parsed;
    } catch (error) {
      await this.quarantine(path, canonical);
      throw error;
    }
  }

  private parseAndMigrate<T>(
    path: string,
    kind: PersistedDocumentKind,
    parser: Parser<T>,
    bytes: Uint8Array,
  ): { parsed: T; migrated: boolean } {
    const migrated = migratePersistedDocument(kind, parseJson(bytes, path));
    const parsed = parser.parse(migrated.value);
    if (migrated.migratedFrom !== undefined) {
      this.onDiagnostic({
        level: 'info',
        code: 'migration-applied',
        path,
        message: `Migrated ${kind} from schema version ${String(migrated.migratedFrom)} to 1.`,
      });
    }
    return { parsed, migrated: migrated.migratedFrom !== undefined };
  }

  private async recoverGeneration<T>(
    path: string,
    kind: PersistedDocumentKind,
    parser: Parser<T>,
  ): Promise<T | undefined> {
    const prefix = `${path}.tmp-`;
    const candidates = (await this.files.list(prefix)).sort().reverse();
    for (const candidate of candidates) {
      const bytes = await this.files.read(candidate);
      if (bytes === undefined) {
        continue;
      }
      try {
        const result = this.parseAndMigrate(path, kind, parser, bytes);
        await this.write(path, result.parsed);
        this.onDiagnostic({
          level: 'warning',
          code: 'temporary-generation-recovered',
          path,
          message: `Recovered ${path} from a complete temporary generation.`,
        });
        return result.parsed;
      } catch (error) {
        await this.quarantine(candidate, bytes);
        if (
          !(error instanceof CorruptStoredData) &&
          !(error instanceof Error)
        ) {
          throw error;
        }
      }
    }
    return undefined;
  }

  private async removeOldGenerations(
    path: string,
    retainedPath: string,
  ): Promise<void> {
    for (const candidate of await this.files.list(`${path}.tmp-`)) {
      if (candidate !== retainedPath) {
        try {
          await this.files.remove(candidate);
        } catch (error) {
          if (!isDeferredCleanupError(error)) throw error;
          // The committed generation and canonical file are already durable.
          // A later write or recovery pass can remove a temporarily locked
          // generation without failing the successful transaction.
        }
      }
    }
  }

  private async quarantine(path: string, bytes: Uint8Array): Promise<void> {
    const quarantinePath = joinPath(
      parentPath(path),
      'corrupt',
      `${String(this.now().getTime())}-${this.createId()}-${baseName(path)}`,
    );
    await this.files.write(quarantinePath, bytes);
    await this.files.remove(path);
    this.onDiagnostic({
      level: 'error',
      code: 'corrupt-file-quarantined',
      path,
      message: `Moved corrupt data to ${quarantinePath}.`,
    });
  }
}
