import { z } from 'zod';

const MAGIC = 'AM3DRT01';
const PREFIX_BYTES = 12;
const textDecoder = new TextDecoder();

const entrySchema = z.object({
  path: z
    .string()
    .min(1)
    .max(1_024)
    .regex(/^[A-Za-z0-9][A-Za-z0-9._/-]*$/u)
    .refine(
      (path) => !path.split('/').some((segment) => segment === '..'),
      'Runtime bundle paths cannot traverse directories.',
    ),
  offset: z.number().int().nonnegative(),
  length: z.number().int().nonnegative(),
});

const headerSchema = z.object({
  schemaVersion: z.literal(1),
  entries: z.array(entrySchema).min(1).max(10_000),
});

function readHeaderLength(bytes: Uint8Array): number {
  if (bytes.byteLength < PREFIX_BYTES) {
    throw new Error('CAD runtime bundle is truncated.');
  }
  if (textDecoder.decode(bytes.subarray(0, MAGIC.length)) !== MAGIC) {
    throw new Error('CAD runtime bundle has an invalid signature.');
  }
  return new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  ).getUint32(MAGIC.length, true);
}

export function decodeRuntimeBundle(
  bytes: Uint8Array,
): ReadonlyMap<string, Uint8Array> {
  const headerLength = readHeaderLength(bytes);
  const payloadStart = PREFIX_BYTES + headerLength;
  if (payloadStart > bytes.byteLength) {
    throw new Error('CAD runtime bundle header exceeds its file bounds.');
  }
  let header: unknown;
  try {
    header = JSON.parse(
      textDecoder.decode(bytes.subarray(PREFIX_BYTES, payloadStart)),
    );
  } catch (cause) {
    throw new Error('CAD runtime bundle header is not valid JSON.', { cause });
  }
  const parsed = headerSchema.parse(header);
  const files = new Map<string, Uint8Array>();
  let expectedOffset = 0;
  for (const entry of parsed.entries) {
    if (files.has(entry.path)) {
      throw new Error(`CAD runtime bundle repeats ${entry.path}.`);
    }
    if (entry.offset !== expectedOffset) {
      throw new Error('CAD runtime bundle entries are not contiguous.');
    }
    const start = payloadStart + entry.offset;
    const end = start + entry.length;
    if (end > bytes.byteLength) {
      throw new Error(`CAD runtime bundle entry ${entry.path} exceeds bounds.`);
    }
    files.set(entry.path, bytes.subarray(start, end));
    expectedOffset += entry.length;
  }
  if (payloadStart + expectedOffset !== bytes.byteLength) {
    throw new Error('CAD runtime bundle contains unindexed trailing bytes.');
  }
  return files;
}
