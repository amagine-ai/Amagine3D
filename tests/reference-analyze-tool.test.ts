import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  symlink,
  truncate,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';

import {
  createReferenceAnalyzeTool,
  REFERENCE_ANALYZE_RESULT_SCHEMA,
  REFERENCE_ANALYZE_TOOL_NAME,
  type ReferenceAnalyzeResult,
} from '@amagine3d/a3d-runtime';

const fakePythonSource = `#!/usr/bin/env node
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const behavior = fs.readFileSync(process.argv[2], 'utf8');
const image = path.resolve(process.argv[3]);
const argv = process.argv.slice(4);
const outIndex = argv.indexOf('--out');
if (outIndex < 0 || outIndex + 1 >= argv.length) throw new Error('missing --out');
const report = argv[outIndex + 1];
if (behavior.includes('hang-and-ignore-sigterm')) {
  fs.writeFileSync(path.join(process.cwd(), 'analyzer.pid'), String(process.pid));
  process.on('SIGTERM', () => {});
  setInterval(() => {}, 1000);
} else if (behavior.includes('make-cleanup-fail')) {
  fs.chmodSync(path.dirname(path.dirname(report)), 0o500);
  process.stderr.write('forced analyzer failure');
  process.exit(2);
} else if (behavior.includes('oversized-report')) {
  fs.writeFileSync(report, 'x');
  fs.truncateSync(report, 4 * 1024 * 1024 + 1);
} else if (behavior.includes('symlink-report')) {
  fs.symlinkSync(image, report);
} else {
  const sha256 = crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex');
  const analysis = {
    foreground: { bbox_px: [0, 0, 2, 2] },
    image: { height_px: 2, sha256, width_px: 2 },
    mode: 'general-image',
    schema: 'evidence-reference-analysis/v1',
    source: { path: image, sha256 },
  };
  fs.writeFileSync(report, JSON.stringify(analysis, null, 2) + '\\n');
  process.stdout.write(JSON.stringify(analysis));
  if (behavior.includes('mutate-image')) fs.appendFileSync(image, 'changed');
}
`;

interface Fixture {
  cleanup(): Promise<void>;
  expectedSha256: string;
  imagePath: string;
  outside: string;
  projectRoot: string;
  script: string;
  uploadRoot: string;
  workspaceRoot: string;
}

function sha256(data: Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

async function createFixture(): Promise<Fixture> {
  const temporary = await mkdtemp(join(tmpdir(), 'amagine-reference-tool-'));
  const canonicalTemporary = await realpath(temporary);
  const projectRoot = join(canonicalTemporary, 'project');
  const workspaceRoot = join(canonicalTemporary, 'workspace');
  const uploadRoot = join(canonicalTemporary, 'uploads', 'session-id');
  const outside = join(canonicalTemporary, 'outside');
  const executable =
    process.platform === 'win32'
      ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
      : join(projectRoot, '.venv', 'bin', 'python');
  const script = join(projectRoot, 'skills', 'text-a3d', 'reference_analyze.py');
  await Promise.all([
    mkdir(join(executable, '..'), { recursive: true }),
    mkdir(join(script, '..'), { recursive: true }),
    mkdir(workspaceRoot, { recursive: true }),
    mkdir(uploadRoot, { recursive: true }),
    mkdir(outside, { recursive: true }),
  ]);
  await writeFile(executable, fakePythonSource);
  if (process.platform !== 'win32') await chmod(executable, 0o755);
  await writeFile(script, '# reference analyzer\n');
  const imageBytes = Buffer.from('stable-reference-image');
  const imagePath = join(uploadRoot, 'reference.png');
  await writeFile(imagePath, imageBytes);
  return {
    async cleanup() {
      await rm(canonicalTemporary, { force: true, recursive: true });
    },
    expectedSha256: sha256(imageBytes),
    imagePath,
    outside,
    projectRoot,
    script,
    uploadRoot,
    workspaceRoot,
  };
}

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
  throw new Error('reference analyzer did not publish its pid');
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ESRCH';
  }
}

function executeTool(fixture: Fixture, report = 'evidence/reference.json') {
  const tool = createReferenceAnalyzeTool(
    fixture.projectRoot,
    fixture.workspaceRoot,
    fixture.uploadRoot,
  );
  return tool.execute(
    'reference-call',
    {
      expected_sha256: fixture.expectedSha256,
      image: fixture.imagePath,
      report,
    },
    undefined,
    undefined,
    {} as never,
  );
}

test(
  'reference_analyze atomically publishes exact hash-bound evidence, including identical reruns',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      const tool = createReferenceAnalyzeTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
        fixture.uploadRoot,
      );
      assert.equal(tool.name, REFERENCE_ANALYZE_TOOL_NAME);
      assert.equal(tool.executionMode, 'parallel');
      assert.match(
        (tool.promptGuidelines ?? []).join('\n'),
        /directly; do not invoke reference_analyze\.py through bash/u,
      );

      const first = await executeTool(fixture);
      const second = await executeTool(fixture);
      const details = second.details as ReferenceAnalyzeResult;
      assert.equal(details.schema, REFERENCE_ANALYZE_RESULT_SCHEMA);
      assert.equal(details.pass, true);
      assert.equal(details.source.path, fixture.imagePath);
      assert.equal(details.source.sha256, fixture.expectedSha256);
      assert.equal(
        details.artifacts.report.sha256,
        (first.details as ReferenceAnalyzeResult).artifacts.report.sha256,
      );
      const published = await readFile(details.artifacts.report.path);
      assert.equal(sha256(published), details.artifacts.report.sha256);
      assert.deepEqual(JSON.parse(published.toString('utf8')), details.analysis);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze rejects stale hashes, path traversal, and symbolic-link report parents',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      const tool = createReferenceAnalyzeTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
        fixture.uploadRoot,
      );
      await assert.rejects(
        tool.execute(
          'reference-call',
          {
            expected_sha256: '0'.repeat(64),
            image: fixture.imagePath,
            report: 'reference.json',
          },
          undefined,
          undefined,
          {} as never,
        ),
        /does not match/u,
      );
      await assert.rejects(executeTool(fixture, '../outside.json'), /relative path/u);

      const linkedParent = join(fixture.workspaceRoot, 'linked');
      await symlink(fixture.outside, linkedParent, 'dir');
      await assert.rejects(
        executeTool(fixture, 'linked/reference.json'),
        /regular directories/u,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze rejects images larger than its 64 MiB snapshot bound',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      await truncate(fixture.imagePath, 64 * 1024 * 1024 + 1);
      await assert.rejects(executeTool(fixture), /no larger than 64 MiB/u);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze rejects oversized and symbolic-link analyzer reports',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      await writeFile(fixture.script, 'oversized-report\n');
      await assert.rejects(executeTool(fixture), /no larger than 4 MiB/u);

      await writeFile(fixture.script, 'symlink-report\n');
      await assert.rejects(executeTool(fixture), /ELOOP/u);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze never publishes a report over its source image',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      const imageBytes = Buffer.from('workspace-local-reference');
      const imagePath = join(fixture.workspaceRoot, 'same.json');
      await writeFile(imagePath, imageBytes);
      const tool = createReferenceAnalyzeTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
        fixture.uploadRoot,
      );
      await assert.rejects(
        tool.execute(
          'same-path-call',
          {
            expected_sha256: sha256(imageBytes),
            image: 'same.json',
            report: 'same.json',
          },
          undefined,
          undefined,
          {} as never,
        ),
        /must not overwrite the reference image/u,
      );
      assert.deepEqual(await readFile(imagePath), imageBytes);
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze rejects an image changed by the analyzer before publication',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      await writeFile(fixture.script, 'mutate-image\n');
      await assert.rejects(executeTool(fixture), /changed during analysis/u);
      await assert.rejects(
        readFile(join(fixture.workspaceRoot, 'evidence', 'reference.json')),
        (error: unknown) =>
          (error as NodeJS.ErrnoException).code === 'ENOENT',
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze aborts only after its child exits and then removes staging',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    const controller = new AbortController();
    let pid: number | undefined;
    try {
      await writeFile(fixture.script, 'hang-and-ignore-sigterm\n');
      const tool = createReferenceAnalyzeTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
        fixture.uploadRoot,
      );
      const execution = tool.execute(
        'reference-abort-call',
        {
          expected_sha256: fixture.expectedSha256,
          image: fixture.imagePath,
          report: 'evidence/reference.json',
        },
        controller.signal,
        undefined,
        {} as never,
      );
      pid = await waitForPid(join(fixture.workspaceRoot, 'analyzer.pid'));
      controller.abort();
      await assert.rejects(execution, /was cancelled/u);
      assert.equal(processExists(pid), false);
      assert.equal(
        (await readdir(join(fixture.workspaceRoot, 'evidence'))).some((name) =>
          name.startsWith('.reference-analyze-'),
        ),
        false,
      );
    } finally {
      controller.abort();
      if (pid && processExists(pid)) process.kill(pid, 'SIGKILL');
      await fixture.cleanup();
    }
  },
);

test(
  'reference_analyze releases its active report lock even if staging cleanup fails',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    const reportDirectory = join(fixture.workspaceRoot, 'cleanup');
    try {
      await writeFile(fixture.script, 'make-cleanup-fail\n');
      const tool = createReferenceAnalyzeTool(
        fixture.projectRoot,
        fixture.workspaceRoot,
        fixture.uploadRoot,
      );
      const execute = () =>
        tool.execute(
          'reference-cleanup-call',
          {
            expected_sha256: fixture.expectedSha256,
            image: fixture.imagePath,
            report: 'cleanup/reference.json',
          },
          undefined,
          undefined,
          {} as never,
        );
      await assert.rejects(execute());
      assert.equal(
        (await readdir(reportDirectory)).some((name) =>
          name.startsWith('.reference-analyze-'),
        ),
        true,
      );

      await chmod(reportDirectory, 0o700);
      await writeFile(fixture.script, '# reference analyzer\n');
      const recovered = await execute();
      assert.equal((recovered.details as ReferenceAnalyzeResult).pass, true);
    } finally {
      await chmod(reportDirectory, 0o700).catch(() => undefined);
      await fixture.cleanup();
    }
  },
);
