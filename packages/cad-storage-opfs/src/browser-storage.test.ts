import { afterEach, describe, expect, it, vi } from 'vitest';

import { inspectBrowserStorage } from './browser-storage';

describe('browser storage persistence and capacity hint', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reports unsupported environments without throwing', async () => {
    vi.stubGlobal('navigator', {});
    await expect(inspectBrowserStorage()).resolves.toMatchObject({
      supported: false,
      warning: 'not-supported',
    });
  });

  it('requests persistence and warns when storage is nearly full', async () => {
    const persist = vi.fn(async () => false);
    vi.stubGlobal('navigator', {
      storage: {
        persisted: async () => false,
        persist,
        estimate: async () => ({ usage: 90, quota: 100 }),
      },
    });

    await expect(inspectBrowserStorage()).resolves.toEqual({
      supported: true,
      persisted: false,
      persistenceRequested: true,
      quotaBytes: 100,
      usageBytes: 90,
      usageRatio: 0.9,
      warning: 'quota-nearly-full',
    });
    expect(persist).toHaveBeenCalledOnce();
  });
});
