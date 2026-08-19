import { SCHEMA_VERSION } from '@amagine3d/cad-protocol';

import { CorruptStoredData, UnsupportedSchemaVersion } from './errors';

export type PersistedDocumentKind =
  | 'artifact-index'
  | 'document'
  | 'event-log'
  | 'messages'
  | 'project'
  | 'revision'
  | 'run';

type JsonObject = Record<string, unknown>;

function asObject(value: unknown, documentKind: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new CorruptStoredData(`${documentKind} must be a JSON object.`);
  }
  return value as JsonObject;
}

function readVersion(value: JsonObject, documentKind: string): number {
  const version = value.schemaVersion;
  if (version === undefined) {
    return 0;
  }
  if (
    !Number.isInteger(version) ||
    typeof version !== 'number' ||
    version < 0
  ) {
    throw new CorruptStoredData(
      `${documentKind} has an invalid schemaVersion.`,
    );
  }
  return version;
}

function migrateVersionZero(
  kind: PersistedDocumentKind,
  value: JsonObject,
): JsonObject {
  switch (kind) {
    case 'project':
      return {
        ...value,
        schemaVersion: SCHEMA_VERSION,
        revision: value.revision ?? 0,
        currentRunId: value.currentRunId ?? null,
      };
    case 'run':
      return {
        ...value,
        schemaVersion: SCHEMA_VERSION,
        workflowSelectionReason: value.workflowSelectionReason ?? null,
        workflowSnapshot: value.workflowSnapshot ?? null,
        artifactIds: value.artifactIds ?? [],
      };
    case 'messages':
      return {
        schemaVersion: SCHEMA_VERSION,
        messages: Array.isArray(value.messages) ? value.messages : [],
      };
    case 'event-log':
    case 'artifact-index':
    case 'document':
    case 'revision':
      return { ...value, schemaVersion: SCHEMA_VERSION };
  }
}

export function migratePersistedDocument(
  kind: PersistedDocumentKind,
  input: unknown,
): { value: unknown; migratedFrom: number | undefined } {
  let value = asObject(input, kind);
  const version = readVersion(value, kind);
  if (version > SCHEMA_VERSION) {
    throw new UnsupportedSchemaVersion(kind, version);
  }

  if (version === SCHEMA_VERSION) {
    return { value, migratedFrom: undefined };
  }

  if (version === 0) {
    value = migrateVersionZero(kind, value);
  }

  return { value, migratedFrom: version };
}
