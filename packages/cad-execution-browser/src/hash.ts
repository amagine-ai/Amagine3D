export async function sha256Hex(
  data: ArrayBuffer | Uint8Array,
): Promise<string> {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  const exact = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  const digest = await crypto.subtle.digest('SHA-256', exact);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

export async function assertSha256(
  name: string,
  data: ArrayBuffer | Uint8Array,
  expected: string,
): Promise<void> {
  const actual = await sha256Hex(data);
  if (actual !== expected) {
    throw new Error(
      `${name} integrity mismatch: expected ${expected}, received ${actual}`,
    );
  }
}
