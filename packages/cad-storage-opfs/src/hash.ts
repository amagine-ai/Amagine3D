const textEncoder = new TextEncoder();

export function encodeText(value: string): Uint8Array {
  return textEncoder.encode(value);
}

export function decodeText(value: Uint8Array): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(value);
}

export function toOwnedBytes(value: Uint8Array): Uint8Array<ArrayBuffer> {
  const copy = new Uint8Array(new ArrayBuffer(value.byteLength));
  copy.set(value);
  return copy;
}

export async function sha256(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', toOwnedBytes(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

export function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  if (left.byteLength !== right.byteLength) {
    return false;
  }

  return left.every((byte, index) => byte === right[index]);
}
