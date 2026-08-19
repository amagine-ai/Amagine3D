import { describe, expect, it } from 'vitest';

import { decodeRuntimeBundle } from './runtime-bundle';

const encoder = new TextEncoder();

function bundle(entries: Array<{ path: string; value: string }>): Uint8Array {
  let offset = 0;
  const payloads = entries.map((entry) => encoder.encode(entry.value));
  const header = encoder.encode(
    JSON.stringify({
      schemaVersion: 1,
      entries: entries.map((entry, index) => {
        const payload = payloads[index];
        if (payload === undefined) throw new Error('Missing test payload.');
        const record = { path: entry.path, offset, length: payload.byteLength };
        offset += payload.byteLength;
        return record;
      }),
    }),
  );
  const result = new Uint8Array(
    12 +
      header.byteLength +
      payloads.reduce((sum, item) => sum + item.byteLength, 0),
  );
  result.set(encoder.encode('AM3DRT01'));
  new DataView(result.buffer).setUint32(8, header.byteLength, true);
  result.set(header, 12);
  let cursor = 12 + header.byteLength;
  for (const payload of payloads) {
    result.set(payload, cursor);
    cursor += payload.byteLength;
  }
  return result;
}

describe('CAD runtime bundle', () => {
  it('decodes contiguous indexed files', () => {
    const files = decodeRuntimeBundle(
      bundle([
        { path: 'pyodide/pyodide-lock.json', value: '{}' },
        { path: 'wheels/build123d.whl', value: 'wheel' },
      ]),
    );
    expect(new TextDecoder().decode(files.get('wheels/build123d.whl'))).toBe(
      'wheel',
    );
  });

  it('rejects traversal, duplicate, and out-of-bounds indexes', () => {
    expect(() =>
      decodeRuntimeBundle(bundle([{ path: '../escape', value: 'x' }])),
    ).toThrow(/paths|traverse/iu);
    const duplicate = bundle([
      { path: 'same', value: 'a' },
      { path: 'same', value: 'b' },
    ]);
    expect(() => decodeRuntimeBundle(duplicate)).toThrow(/repeats/iu);
    const truncated = bundle([{ path: 'one', value: 'abc' }]).slice(0, -1);
    expect(() => decodeRuntimeBundle(truncated)).toThrow(/bounds/iu);
  });
});
