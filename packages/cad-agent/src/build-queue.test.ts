import { describe, expect, it, vi } from 'vitest';

import { BuildSuperseded, LatestBuildQueue } from './build-queue';

describe('LatestBuildQueue', () => {
  it('debounces rapid input and only executes the latest request', async () => {
    vi.useFakeTimers();
    const execute = vi.fn(async (value: number) => value * 2);
    const queue = new LatestBuildQueue({ debounceMs: 100, execute });
    const first = queue.submit(1);
    const second = queue.submit(2);
    await expect(first).rejects.toBeInstanceOf(BuildSuperseded);
    await vi.advanceTimersByTimeAsync(100);
    await expect(second).resolves.toBe(4);
    expect(execute).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('aborts an active build and ignores its late result', async () => {
    vi.useFakeTimers();
    const resolvers: Array<(value: string) => void> = [];
    const signals: AbortSignal[] = [];
    const queue = new LatestBuildQueue<string, string>({
      debounceMs: 10,
      execute: (value, signal) => {
        signals.push(signal);
        return new Promise((resolve) => resolvers.push(() => resolve(value)));
      },
    });
    const first = queue.submit('old');
    await vi.advanceTimersByTimeAsync(10);
    const second = queue.submit('new');
    await expect(first).rejects.toBeInstanceOf(BuildSuperseded);
    expect(signals[0]?.aborted).toBe(true);
    resolvers[0]?.('old');
    await vi.advanceTimersByTimeAsync(10);
    resolvers[1]?.('new');
    await expect(second).resolves.toBe('new');
    vi.useRealTimers();
  });
});
