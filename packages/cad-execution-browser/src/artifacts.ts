import {
  CadDomainError,
  artifactSchema,
  cadWorkerArtifactPayloadSchema,
  SCHEMA_VERSION,
  type CadWorkerArtifactPayload,
} from '@amagine3d/cad-protocol';

import { RUNTIME_LIMITS } from './runtime-manifest';
import { sha256Hex } from './hash';
import type { RuntimeArtifact } from './types';

function exactArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}

export async function createArtifactPayloads(
  runId: string,
  artifacts: RuntimeArtifact[],
  createdAt: string,
): Promise<CadWorkerArtifactPayload[]> {
  let totalBytes = 0;
  const result: CadWorkerArtifactPayload[] = [];

  for (const [index, artifact] of artifacts.entries()) {
    if (artifact.bytes.byteLength > RUNTIME_LIMITS.artifactBytes) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        `${artifact.fileName} exceeds the ${RUNTIME_LIMITS.artifactBytes} byte artifact limit.`,
        {
          category: 'execution',
          retryable: false,
          operation: 'export',
        },
      );
    }
    totalBytes += artifact.bytes.byteLength;
    if (totalBytes > RUNTIME_LIMITS.totalArtifactBytes) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        `Build artifacts exceed the ${RUNTIME_LIMITS.totalArtifactBytes} byte total limit.`,
        {
          category: 'execution',
          retryable: false,
          operation: 'export',
        },
      );
    }

    const metadata = artifactSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      id: `${runId}-${artifact.kind}-${index + 1}`,
      runId,
      kind: artifact.kind,
      fileName: artifact.fileName,
      mediaType: artifact.mediaType,
      byteLength: artifact.bytes.byteLength,
      sha256: await sha256Hex(artifact.bytes),
      createdAt,
      regionName: artifact.regionName,
    });
    result.push(
      cadWorkerArtifactPayloadSchema.parse({
        artifact: metadata,
        bytes: exactArrayBuffer(artifact.bytes),
      }),
    );
  }
  return result;
}
