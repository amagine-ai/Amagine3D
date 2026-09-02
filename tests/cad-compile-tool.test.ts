import { strict as assert } from 'node:assert';
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  CAD_COMPILE_TOOL_NAME,
  createCadCompileResultExtension,
  createCadCompileTool,
  type CadCompileResult,
} from '../packages/a3d-runtime/src/cad-compile-tool.ts';

const fakePythonSource = `#!/usr/bin/env node
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const argv = process.argv.slice(3);
function option(name) {
  const index = argv.indexOf(name);
  if (index < 0 || index + 1 >= argv.length) throw new Error('missing ' + name);
  return argv[index + 1];
}
const log = option('--log');
const outputDir = option('--output-dir');
const source = option('--source');
const marker = option('--marker');
function artifact(file, corrupt = false) {
  const sha256 = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  return { path: file, sha256: corrupt ? '0'.repeat(64) : sha256 };
}
if (!fs.statSync(marker).isFile()) throw new Error('marker is not a file');
if (argv.includes('--report')) throw new Error('tool must let the compiler derive its backend-compatible report name');
const mode = fs.readFileSync(source, 'utf8').trim();
fs.appendFileSync(log, 'stage=source\\n');

if (mode === 'hang') {
  const descendant = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    stdio: 'ignore',
  });
  fs.writeFileSync(path.join(process.cwd(), 'descendant.pid'), String(descendant.pid));
  setInterval(() => {}, 1000);
} else {
  setTimeout(() => {
    fs.appendFileSync(log, 'stage=render\\n');
    const pass = mode !== 'fail';
    const artifacts = { log: artifact(log, mode === 'bad-hash') };
    if (pass) {
      const preview = path.join(outputDir, 'model_views.png');
      const renderEvidence = path.join(outputDir, 'model_render.json');
      fs.writeFileSync(preview, 'preview');
      fs.writeFileSync(renderEvidence, '{}');
      artifacts.preview = artifact(preview);
      artifacts.renderEvidence = artifact(renderEvidence);
    }
    const result = {
      artifacts,
      issues: pass ? [] : [{ code: 'QA.MESH_FAILED', severity: 'error', stage: 'mesh-qa' }],
      pass,
      schema: 'evidence-cad-compile-result/v1',
      status: pass ? 'awaiting-visual-review' : 'failed',
      receivedMarker: path.relative(process.cwd(), marker),
      reportOverride: argv.includes('--report'),
      visualReviewRequired: true,
    };
    process.stdout.write(JSON.stringify(result));
    process.exit(pass ? 0 : 1);
  }, 80);
}
`;

interface Fixture {
  cleanup(): Promise<void>;
  outside: string;
  projectRoot: string;
  workspaceRoot: string;
}

async function createFixture(
  mode: 'bad-hash' | 'fail' | 'hang' | 'pass',
): Promise<Fixture> {
  const temporary = await mkdtemp(join(tmpdir(), 'amagine-cad-tool-'));
  const canonicalTemporary = await realpath(temporary);
  const projectRoot = join(canonicalTemporary, 'project');
  const workspaceRoot = join(projectRoot, 'workspace', 'session-id');
  const executable =
    process.platform === 'win32'
      ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
      : join(projectRoot, '.venv', 'bin', 'python');
  await mkdir(join(projectRoot, 'skills', 'text-a3d'), { recursive: true });
  await mkdir(join(executable, '..'), { recursive: true });
  await mkdir(workspaceRoot, { recursive: true });
  await writeFile(
    join(projectRoot, 'skills', 'text-a3d', 'cad_compile.py'),
    '# fake compiler entry point\n',
  );
  await writeFile(executable, fakePythonSource);
  if (process.platform !== 'win32') await chmod(executable, 0o755);
  await writeFile(join(workspaceRoot, '.generation-start'), 'generation started\n');
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
  await writeFile(join(workspaceRoot, 'intent.json'), '{}\n');
  await writeFile(join(workspaceRoot, 'model.py'), `${mode}\n`);
  await writeFile(join(canonicalTemporary, 'outside.json'), '{}\n');
  return {
    async cleanup() {
      await rm(canonicalTemporary, { force: true, recursive: true });
    },
    outside: join(canonicalTemporary, 'outside.json'),
    projectRoot,
    workspaceRoot,
  };
}

function executeTool(
  fixture: Fixture,
  options: {
    intent?: string;
    marker?: string;
    onUpdate?: (value: unknown) => void;
    signal?: AbortSignal;
  } = {},
) {
  const tool = createCadCompileTool(fixture.projectRoot, fixture.workspaceRoot, {
    logPollIntervalMs: 10,
    terminateGraceMs: 50,
  });
  return tool.execute(
    'compile-call',
    {
      intent: options.intent ?? 'intent.json',
      marker: options.marker ?? '.generation-start',
      output_dir: 'artifacts',
      scene: 'scene.json',
      source: 'model.py',
    },
    options.signal,
    options.onUpdate as never,
    {} as never,
  );
}

test(
  'cad_compile is sequential, streams only real log growth, and preserves compact result details',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('pass');
    try {
      const tool = createCadCompileTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
      );
      assert.equal(tool.name, CAD_COMPILE_TOOL_NAME);
      assert.equal(tool.executionMode, 'sequential');

      const updates: unknown[] = [];
      const result = await executeTool(fixture, {
        onUpdate: (update) => updates.push(update),
      });
      const details = result.details as CadCompileResult;
      assert.equal(details.pass, true);
      assert.equal(details.status, 'awaiting-visual-review');
      assert.equal(details.receivedMarker, '.generation-start');
      assert.equal(details.reportOverride, false);
      assert.equal(
        typeof (details.artifacts.preview as { path: string }).path,
        'string',
      );
      assert.equal(
        typeof (details.artifacts.renderEvidence as { path: string }).path,
        'string',
      );
      assert.deepEqual(
        JSON.parse(
          result.content[0]?.type === 'text' ? result.content[0].text : '',
        ),
        details,
      );
      assert.ok(updates.length >= 2, 'each delayed log growth should refresh progress');
      assert.match(JSON.stringify(updates[0]), /stage=source/u);
      const lastUpdate = JSON.stringify(updates.at(-1));
      assert.match(lastUpdate, /stage=render/u);
      assert.doesNotMatch(lastUpdate, /stage=source/u);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'cad_compile returns pass=false diagnostics instead of discarding structured issues',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('fail');
    try {
      const result = await executeTool(fixture);
      const details = result.details as CadCompileResult;
      assert.equal(details.pass, false);
      assert.equal(details.status, 'failed');
      assert.equal(details.issues[0]?.code, 'QA.MESH_FAILED');
      assert.deepEqual(
        JSON.parse(
          result.content[0]?.type === 'text' ? result.content[0].text : '',
        ),
        details,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'cad_compile rejects a returned artifact whose SHA-256 does not match',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('bad-hash');
    try {
      await assert.rejects(
        executeTool(fixture),
        /TOOL\.RESULT_HASH_MISMATCH/u,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test('cad_compile result extension marks only structured semantic failures as errors', async () => {
  let toolResultHandler: ((event: Record<string, unknown>) => unknown) | undefined;
  const extension = createCadCompileResultExtension();
  assert.equal(typeof extension, 'object');
  if (typeof extension === 'function') throw new Error('expected named extension');
  extension.factory({
    on(eventName: string, handler: (event: Record<string, unknown>) => unknown) {
      if (eventName === 'tool_result') toolResultHandler = handler;
    },
  } as never);
  assert.ok(toolResultHandler);

  const failed = {
    artifacts: {},
    issues: [{ code: 'QA.MESH_FAILED' }],
    pass: false,
    schema: 'evidence-cad-compile-result/v1',
    status: 'failed',
  };
  const usage = { input: 1, output: 2 };
  assert.deepEqual(
    await toolResultHandler({
      details: failed,
      isError: false,
      toolName: CAD_COMPILE_TOOL_NAME,
      usage,
    }),
    { details: failed, isError: true, usage },
  );
  assert.equal(
    await toolResultHandler({
      details: { ...failed, pass: true, status: 'awaiting-visual-review' },
      isError: false,
      toolName: CAD_COMPILE_TOOL_NAME,
    }),
    undefined,
  );
});

test(
  'cad_compile rejects traversal and symbolic-link inputs before execution',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('pass');
    try {
      await assert.rejects(
        executeTool(fixture, { intent: '../outside.json' }),
        /TOOL\.PATH_TRAVERSAL/u,
      );
      await symlink(fixture.outside, join(fixture.workspaceRoot, 'linked.json'));
      await assert.rejects(
        executeTool(fixture, { intent: 'linked.json' }),
        /TOOL\.SYMLINK_REJECTED/u,
      );
      await assert.rejects(
        executeTool(fixture, { marker: 'missing-marker' }),
        /TOOL\.PATH_MISSING/u,
      );
      await assert.rejects(
        executeTool(fixture, { marker: 'linked.json' }),
        /TOOL\.SYMLINK_REJECTED/u,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'cad_compile requires the generation marker to predate intent and source',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('pass');
    try {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
      await writeFile(
        join(fixture.workspaceRoot, '.generation-start'),
        'marker replaced too late\n',
      );
      await assert.rejects(executeTool(fixture), /TOOL\.MARKER_TOO_NEW/u);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'cad_compile abort terminates the detached compiler process group',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture('hang');
    let descendantPid: number | undefined;
    try {
      const controller = new AbortController();
      const updates: unknown[] = [];
      const execution = executeTool(fixture, {
        onUpdate: (update) => updates.push(update),
        signal: controller.signal,
      });
      const pidPath = join(fixture.workspaceRoot, 'descendant.pid');
      for (let attempt = 0; attempt < 100; attempt += 1) {
        try {
          descendantPid = Number(await readFile(pidPath, 'utf8'));
          break;
        } catch {
          await new Promise((resolvePromise) => setTimeout(resolvePromise, 10));
        }
      }
      const startedDescendantPid = descendantPid;
      assert.ok(startedDescendantPid, 'fake compiler did not start its descendant');
      for (let attempt = 0; attempt < 50 && updates.length === 0; attempt += 1) {
        await new Promise((resolvePromise) => setTimeout(resolvePromise, 10));
      }
      assert.equal(updates.length, 1, 'initial real log output should update once');
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 80));
      assert.equal(
        updates.length,
        1,
        'a silent running compiler must not emit synthetic activity updates',
      );
      controller.abort();
      await assert.rejects(execution, /TOOL\.ABORTED/u);
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 150));
      assert.throws(() => process.kill(startedDescendantPid, 0), /ESRCH/u);
    } finally {
      if (descendantPid) {
        try {
          process.kill(descendantPid, 'SIGKILL');
        } catch {
          // The expected path: the process group was already terminated.
        }
      }
      await fixture.cleanup();
    }
  },
);
