import { CadDomainError, type JsonValue } from '@amagine3d/cad-protocol';

export class StorageConflict extends CadDomainError {
  constructor(message: string, details?: Record<string, JsonValue>) {
    super('IntegrityMismatch', message, {
      category: 'storage',
      retryable: false,
      operation: 'storage-conflict',
      ...(details === undefined ? {} : { details }),
    });
  }
}

export class CorruptStoredData extends CadDomainError {
  constructor(message: string, details?: Record<string, JsonValue>) {
    super('InvalidExternalData', message, {
      category: 'storage',
      retryable: false,
      operation: 'read-stored-data',
      ...(details === undefined ? {} : { details }),
    });
  }
}

export class UnsupportedSchemaVersion extends CadDomainError {
  constructor(documentKind: string, version: number) {
    super(
      'InvalidExternalData',
      `Unsupported ${documentKind} schema version ${String(version)}.`,
      {
        category: 'protocol',
        retryable: false,
        operation: 'migrate-stored-data',
        details: { documentKind, version },
      },
    );
  }
}

export class UnsafeArchivePath extends CadDomainError {
  constructor(path: string) {
    super('InvalidExternalData', `Unsafe ZIP entry path: ${path}`, {
      category: 'integrity',
      retryable: false,
      operation: 'preflight-project-import',
      details: { path },
    });
  }
}

export class ArchiveIntegrityMismatch extends CadDomainError {
  constructor(path: string, reason: string) {
    super('IntegrityMismatch', `Archive entry ${path}: ${reason}`, {
      category: 'integrity',
      retryable: false,
      operation: 'preflight-project-import',
      details: { path, reason },
    });
  }
}

export class StorageNotAvailable extends CadDomainError {
  constructor(message: string, cause?: unknown) {
    super('StorageUnavailable', message, {
      category: 'storage',
      retryable: true,
      operation: 'open-opfs',
      ...(cause === undefined ? {} : { cause }),
    });
  }
}
