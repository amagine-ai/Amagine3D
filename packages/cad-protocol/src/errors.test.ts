import { describe, expect, it } from 'vitest';

import {
  CadDomainError,
  deserializeCadError,
  serializeCadError,
} from './errors';

describe('serializable CAD errors', () => {
  it('round-trips a named domain error across a Worker boundary', () => {
    const error = new CadDomainError('WorkerTimeout', 'Worker timed out.', {
      category: 'execution',
      retryable: true,
      operation: 'build',
      details: { timeoutMs: 30_000 },
    });

    const serialized = serializeCadError(error);
    expect(structuredClone(serialized)).toEqual(serialized);

    const restored = deserializeCadError(structuredClone(serialized));
    expect(restored).toMatchObject({
      code: 'WorkerTimeout',
      category: 'execution',
      retryable: true,
      operation: 'build',
    });
  });

  it('normalizes unknown thrown values without exposing a stack', () => {
    const serialized = serializeCadError(new Error('boom'));

    expect(serialized).toEqual({
      schemaVersion: 1,
      code: 'UnexpectedFailure',
      category: 'unknown',
      message: 'boom',
      retryable: false,
    });
    expect(serialized).not.toHaveProperty('stack');
  });

  it('rejects invalid serialized error payloads', () => {
    expect(() =>
      deserializeCadError({
        schemaVersion: 1,
        code: 'MadeUpError',
        category: 'unknown',
        message: 'No taxonomy entry.',
        retryable: false,
      }),
    ).toThrow();
  });
});
