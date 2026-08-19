import { describe, expect, it } from 'vitest';

import { readJsonBody } from './server-request';

describe('bounded JSON request reader', () => {
  it('parses a body within the limit', async () => {
    await expect(
      readJsonBody(
        new Request('http://localhost/test', {
          method: 'POST',
          body: JSON.stringify({ ok: true }),
        }),
        1_024,
      ),
    ).resolves.toEqual({ ok: true });
  });

  it('rejects declared and streamed bodies over the limit', async () => {
    await expect(
      readJsonBody(
        new Request('http://localhost/test', {
          method: 'POST',
          headers: { 'Content-Length': '100' },
          body: '{}',
        }),
        10,
      ),
    ).rejects.toThrow(/exceeds/iu);
    await expect(
      readJsonBody(
        new Request('http://localhost/test', {
          method: 'POST',
          body: JSON.stringify({ value: 'too large' }),
        }),
        8,
      ),
    ).rejects.toThrow(/exceeds/iu);
  });
});
