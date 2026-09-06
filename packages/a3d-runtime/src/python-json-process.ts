import {
  spawn,
  type ChildProcess,
  type SpawnOptions,
} from 'node:child_process';

const DEFAULT_TERMINATE_GRACE_MS = 250;
const DEFAULT_WINDOWS_TASKKILL_TIMEOUT_MS = 5_000;

type SpawnProcess = (
  command: string,
  args: readonly string[],
  options: SpawnOptions,
) => ChildProcess;

export interface ProcessTreeTerminationOptions {
  /** Test seam for exercising Windows termination on another host. */
  platform?: NodeJS.Platform;
  /** Test seam for a bounded taskkill child. */
  spawnProcess?: SpawnProcess;
  windowsTaskkillTimeoutMs?: number;
}

export interface PythonJsonProcessResult {
  exitCode: number;
  stderr: string;
  stdout: string;
}

export interface PythonJsonProcessOptions {
  cwd: string;
  maxBufferBytes?: number;
  signal?: AbortSignal;
  terminateGraceMs?: number;
  timeoutMs?: number;
}

function killPosixProcessGroup(
  child: ChildProcess,
  signal: NodeJS.Signals,
): void {
  const pid = child.pid;
  if (!pid) return;
  try {
    process.kill(-pid, signal);
  } catch {
    // The process group may already have exited.
  }
}

function closeCapturedStdio(child: ChildProcess): void {
  child.stdin?.destroy();
  child.stdout?.destroy();
  child.stderr?.destroy();
}

async function forceKillWindowsProcessTree(
  child: ChildProcess,
  timeoutMs: number,
  spawnProcess: SpawnProcess,
): Promise<void> {
  const pid = child.pid;
  if (!pid) return;
  const fallback = (): void => {
    try {
      child.kill('SIGKILL');
    } catch {
      // The process tree may already have exited.
    }
  };
  await new Promise<void>((resolvePromise) => {
    let finished = false;
    let killer: ChildProcess | undefined;
    let timeout: NodeJS.Timeout | undefined;
    const finish = (useFallback: boolean): void => {
      if (finished) return;
      finished = true;
      if (timeout) clearTimeout(timeout);
      if (useFallback) fallback();
      resolvePromise();
    };
    try {
      killer = spawnProcess(
        'taskkill',
        ['/pid', String(pid), '/t', '/f'],
        { shell: false, stdio: 'ignore', windowsHide: true },
      );
      killer.once('error', () => finish(true));
      killer.once('close', (code) => finish(code !== 0));
      if (!finished) {
        timeout = setTimeout(() => {
          if (finished) return;
          // A wedged taskkill must not turn an abort or watchdog into an
          // unbounded wait. Fall back to the direct child kill and then reap
          // the helper when the operating system permits it.
          finish(true);
          try {
            killer?.kill('SIGKILL');
          } catch {
            // The helper may already have exited concurrently.
          }
        }, timeoutMs);
      }
    } catch {
      finish(true);
    }
  });
}

/** Terminate one detached child process tree, including its descendants. */
export async function terminateProcessTree(
  child: ChildProcess,
  graceMs: number,
  options: ProcessTreeTerminationOptions = {},
): Promise<void> {
  const pid = child.pid;
  if (!pid) return;
  const platform = options.platform ?? process.platform;
  if (platform === 'win32') {
    const timeoutMs =
      options.windowsTaskkillTimeoutMs ??
      DEFAULT_WINDOWS_TASKKILL_TIMEOUT_MS;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new Error('Windows taskkill timeout must be a positive number.');
    }
    await forceKillWindowsProcessTree(
      child,
      timeoutMs,
      options.spawnProcess ?? spawn,
    );
    closeCapturedStdio(child);
    return;
  }
  killPosixProcessGroup(child, 'SIGTERM');
  await new Promise<void>((resolvePromise) => {
    setTimeout(resolvePromise, graceMs);
  });
  killPosixProcessGroup(child, 'SIGKILL');
  closeCapturedStdio(child);
}

/** Run one argv-only Python utility and capture its bounded JSON output. */
export async function runPythonJsonProcess(
  executable: string,
  argv: readonly string[],
  options: PythonJsonProcessOptions,
): Promise<PythonJsonProcessResult> {
  if (options.signal?.aborted) {
    throw new Error('Python utility was cancelled.');
  }
  const timeoutMs = options.timeoutMs ?? 30_000;
  const terminateGraceMs =
    options.terminateGraceMs ?? DEFAULT_TERMINATE_GRACE_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error('Python utility timeout must be a positive number.');
  }
  if (!Number.isFinite(terminateGraceMs) || terminateGraceMs < 0) {
    throw new Error('Python utility termination grace must not be negative.');
  }
  const maxBufferBytes = options.maxBufferBytes ?? 4 * 1024 * 1024;
  if (!Number.isSafeInteger(maxBufferBytes) || maxBufferBytes <= 0) {
    throw new Error('Python utility output bound must be a positive integer.');
  }
  return await new Promise<PythonJsonProcessResult>((resolvePromise, reject) => {
    let settled = false;
    let childClosed = false;
    let terminationReason: 'cancelled' | 'output-limit' | 'timeout' | undefined;
    let terminationPromise: Promise<void> | undefined;
    let timeoutTimer: NodeJS.Timeout | undefined;
    const stderrChunks: Buffer[] = [];
    const stdoutChunks: Buffer[] = [];
    let stderrBytes = 0;
    let stdoutBytes = 0;
    const finish = (callback: () => void): void => {
      if (settled) return;
      settled = true;
      if (timeoutTimer) clearTimeout(timeoutTimer);
      options.signal?.removeEventListener('abort', abort);
      callback();
    };
    const terminate = (
      reason: 'cancelled' | 'output-limit' | 'timeout',
    ): void => {
      if (childClosed || terminationReason) return;
      terminationReason = reason;
      terminationPromise = terminateProcessTree(child, terminateGraceMs);
    };
    const abort = () => terminate('cancelled');
    const child = spawn(
      executable,
      [...argv],
      {
        cwd: options.cwd,
        detached: process.platform !== 'win32',
        shell: false,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      },
    );
    const capture = (
      chunks: Buffer[],
      currentBytes: number,
      chunk: Buffer,
    ): number => {
      const remaining = maxBufferBytes - currentBytes;
      if (remaining > 0) chunks.push(chunk.subarray(0, remaining));
      const nextBytes = currentBytes + chunk.length;
      if (nextBytes > maxBufferBytes) terminate('output-limit');
      return Math.min(nextBytes, maxBufferBytes);
    };
    child.stdout.on('data', (chunk: Buffer) => {
      stdoutBytes = capture(stdoutChunks, stdoutBytes, chunk);
    });
    child.stderr.on('data', (chunk: Buffer) => {
      stderrBytes = capture(stderrChunks, stderrBytes, chunk);
    });
    child.once('error', (error) => finish(() => reject(error)));
    child.once('close', (code, signal) => {
      childClosed = true;
      if (timeoutTimer) clearTimeout(timeoutTimer);
      options.signal?.removeEventListener('abort', abort);
      void (async () => {
        await terminationPromise;
        if (terminationReason === 'cancelled') {
          finish(() => reject(new Error('Python utility was cancelled.')));
          return;
        }
        if (terminationReason === 'timeout') {
          finish(() =>
            reject(new Error(`Python utility timed out after ${timeoutMs} ms.`)),
          );
          return;
        }
        if (terminationReason === 'output-limit') {
          finish(() =>
            reject(new Error('Python utility produced too much output.')),
          );
          return;
        }
        if (code === null) {
          finish(() =>
            reject(
              new Error(
                `Python utility exited without a status${signal ? ` (${signal})` : ''}.`,
              ),
            ),
          );
          return;
        }
        finish(() =>
          resolvePromise({
            exitCode: code,
            stderr: Buffer.concat(stderrChunks, stderrBytes).toString('utf8'),
            stdout: Buffer.concat(stdoutChunks, stdoutBytes).toString('utf8'),
          }),
        );
      })();
    });
    timeoutTimer = setTimeout(
      () => terminate('timeout'),
      timeoutMs,
    );
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) abort();
  });
}
