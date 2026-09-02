import { strict as assert } from 'node:assert';
import type { ChildProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';

import {
  runPythonJsonProcess,
  terminateProcessTree,
} from '../packages/a3d-runtime/src/python-json-process.ts';

const stubbornChild = String.raw`
const fs = require('node:fs');
fs.writeFileSync(process.argv[1], String(process.pid));
process.on('SIGTERM', () => {});
setInterval(() => {}, 1000);
`;

const inheritedGrandchild = String.raw`
const fs = require('node:fs');
fs.writeFileSync(process.argv[1], 'ready');
process.on('SIGTERM', () => {});
setInterval(() => {}, 1000);
`;

const inheritedGrandchildParent = `
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const grandchildSource = ${JSON.stringify(inheritedGrandchild)};
const grandchild = spawn(
  process.execPath,
  ['-e', grandchildSource, process.argv[2]],
  { stdio: 'inherit' },
);
fs.writeFileSync(
  process.argv[1],
  JSON.stringify({ grandchild: grandchild.pid, parent: process.pid }),
);
grandchild.unref();
`;

const silentGrandchildParent = `
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const grandchildSource = ${JSON.stringify(inheritedGrandchild)};
const grandchild = spawn(
  process.execPath,
  ['-e', grandchildSource, process.argv[2]],
  { stdio: 'ignore' },
);
fs.writeFileSync(
  process.argv[1],
  JSON.stringify({ grandchild: grandchild.pid, parent: process.pid }),
);
grandchild.unref();
setInterval(() => {}, 1000);
`;

async function waitForPid(path: string): Promise<number> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      const pid = Number.parseInt(await readFile(path, 'utf8'), 10);
      if (Number.isInteger(pid) && pid > 0) return pid;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    await delay(10);
  }
  throw new Error('child process did not publish its pid');
}

async function waitForFile(path: string): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      await readFile(path);
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    await delay(10);
  }
  throw new Error('child process did not publish its readiness marker');
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ESRCH';
  }
}

async function waitForProcessExit(pid: number): Promise<boolean> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (!processExists(pid)) return true;
    await delay(10);
  }
  return !processExists(pid);
}

test('Windows taskkill fallback is bounded when the helper never exits', async () => {
  const targetSignals: NodeJS.Signals[] = [];
  let killerKilled = false;
  const target = {
    kill(signal: NodeJS.Signals) {
      targetSignals.push(signal);
      return true;
    },
    pid: 42,
  } as unknown as ChildProcess;
  const killer = Object.assign(new EventEmitter(), {
    kill() {
      killerKilled = true;
      return true;
    },
  }) as unknown as ChildProcess;
  const startedAt = Date.now();

  await terminateProcessTree(target, 0, {
    platform: 'win32',
    spawnProcess: (() => killer) as never,
    windowsTaskkillTimeoutMs: 30,
  });

  assert.deepEqual(targetSignals, ['SIGKILL']);
  assert.equal(killerKilled, true);
  assert.ok(Date.now() - startedAt < 1_000);
});

test(
  'Python utility timeout escalates past ignored SIGTERM and settles after exit',
  { skip: process.platform === 'win32' },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-python-timeout-'));
    const pidPath = join(root, 'child.pid');
    let pid: number | undefined;
    try {
      const startedAt = Date.now();
      const execution = runPythonJsonProcess(
        process.execPath,
        ['-e', stubbornChild, pidPath],
        {
          cwd: root,
          terminateGraceMs: 50,
          timeoutMs: 250,
        },
      );
      pid = await waitForPid(pidPath);
      await assert.rejects(execution, /timed out after 250 ms/u);
      assert.equal(processExists(pid), false);
      assert.ok(Date.now() - startedAt < 2_000);
    } finally {
      if (pid && processExists(pid)) process.kill(pid, 'SIGKILL');
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'Python utility abort waits for a stubborn child to exit before rejecting',
  { skip: process.platform === 'win32' },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-python-abort-'));
    const pidPath = join(root, 'child.pid');
    const controller = new AbortController();
    let pid: number | undefined;
    try {
      const execution = runPythonJsonProcess(
        process.execPath,
        ['-e', stubbornChild, pidPath],
        {
          cwd: root,
          signal: controller.signal,
          terminateGraceMs: 50,
          timeoutMs: 5_000,
        },
      );
      pid = await waitForPid(pidPath);
      controller.abort();
      await assert.rejects(execution, /was cancelled/u);
      assert.equal(processExists(pid), false);
    } finally {
      controller.abort();
      if (pid && processExists(pid)) process.kill(pid, 'SIGKILL');
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'Python utility timeout kills a grandchild that inherited captured stdio',
  { skip: process.platform === 'win32' },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-python-tree-timeout-'));
    const pidPath = join(root, 'tree.json');
    const readyPath = join(root, 'grandchild.ready');
    let grandchildPid: number | undefined;
    let parentPid: number | undefined;
    try {
      const execution = runPythonJsonProcess(
        process.execPath,
        ['-e', inheritedGrandchildParent, pidPath, readyPath],
        {
          cwd: root,
          terminateGraceMs: 50,
          timeoutMs: 400,
        },
      );
      await waitForFile(readyPath);
      const pids = JSON.parse(await readFile(pidPath, 'utf8')) as {
        grandchild: number;
        parent: number;
      };
      grandchildPid = pids.grandchild;
      parentPid = pids.parent;
      await assert.rejects(execution, /timed out after 400 ms/u);
      assert.equal(await waitForProcessExit(parentPid), true);
      assert.equal(await waitForProcessExit(grandchildPid), true);
    } finally {
      if (parentPid && processExists(parentPid)) {
        process.kill(parentPid, 'SIGKILL');
      }
      if (grandchildPid && processExists(grandchildPid)) {
        process.kill(grandchildPid, 'SIGKILL');
      }
      await rm(root, { force: true, recursive: true });
    }
  },
);

test(
  'Python utility timeout kills a silent grandchild after its parent closes',
  { skip: process.platform === 'win32' },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'amagine-python-silent-tree-'));
    const pidPath = join(root, 'tree.json');
    const readyPath = join(root, 'grandchild.ready');
    let grandchildPid: number | undefined;
    let parentPid: number | undefined;
    try {
      const execution = runPythonJsonProcess(
        process.execPath,
        ['-e', silentGrandchildParent, pidPath, readyPath],
        {
          cwd: root,
          terminateGraceMs: 50,
          timeoutMs: 400,
        },
      );
      await waitForFile(readyPath);
      const pids = JSON.parse(await readFile(pidPath, 'utf8')) as {
        grandchild: number;
        parent: number;
      };
      grandchildPid = pids.grandchild;
      parentPid = pids.parent;
      await assert.rejects(execution, /timed out after 400 ms/u);
      assert.equal(await waitForProcessExit(parentPid), true);
      assert.equal(await waitForProcessExit(grandchildPid), true);
    } finally {
      if (parentPid && processExists(parentPid)) {
        process.kill(parentPid, 'SIGKILL');
      }
      if (grandchildPid && processExists(grandchildPid)) {
        process.kill(grandchildPid, 'SIGKILL');
      }
      await rm(root, { force: true, recursive: true });
    }
  },
);
