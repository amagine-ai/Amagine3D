import { describe, expect, it } from 'vitest';

import { toJsonValue } from './json-value';

describe('toJsonValue', () => {
  it('removes undefined object properties before protocol validation', () => {
    expect(
      toJsonValue({
        id: 'message-1',
        metadata: undefined,
        nested: { keep: true, omit: undefined },
      }),
    ).toEqual({
      id: 'message-1',
      nested: { keep: true },
    });
  });

  it('uses JSON array semantics for undefined entries', () => {
    expect(toJsonValue(['text', undefined])).toEqual(['text', null]);
  });

  it('rejects values that have no JSON representation', () => {
    expect(() => toJsonValue(undefined)).toThrow(
      'Value cannot be serialized as JSON.',
    );
  });
});
