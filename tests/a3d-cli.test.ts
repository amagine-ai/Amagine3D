import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { test } from 'node:test';

const execFileAsync = promisify(execFile);
const A3D = new URL('../bin/a3d.mjs', import.meta.url);

test('symbol queries return usable helper signatures without the full catalog', async () => {
  const { stdout } = await execFileAsync(process.execPath, [A3D.pathname, 'capabilities', '--symbol', 'write_scene', '--symbol', 'export_assembly']);
  const result = JSON.parse(stdout);
  assert.equal(result.query.write_scene.available, true);
  assert.equal(result.query.write_scene.provider, 'authoring');
  assert.ok(result.query.write_scene.signature.includes('intent_path'));
  assert.equal(result.query.export_assembly.provider, 'cad_helpers');
  assert.equal(result.build123d, undefined);
  assert.equal(result.authoring, undefined);
});

test('lists compact public modeling guides without starting Python', async () => {
  const { stdout } = await execFileAsync(process.execPath, [A3D.pathname, 'guide']);

  assert.match(stdout, /strategy/u);
  assert.match(stdout, /pressable-control/u);
  assert.match(stdout, /multipart/u);
  assert.match(stdout, /color/u);
  assert.doesNotMatch(stdout, /workflow/u);
});

test('serves every advertised modeling guide', async () => {
  for (const topic of ['strategy', 'multipart', 'pressable-control', 'color']) {
    const { stdout } = await execFileAsync(process.execPath, [
      A3D.pathname,
      'guide',
      topic,
    ]);
    assert.ok(stdout.trim(), `${topic} guide is empty`);
  }
});

test('selects one persisted compile diagnostic without replaying the full result', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-a3d-diagnose-'));
  const result = join(root, 'part_compile-result.json');
  try {
    await writeFile(
      result,
      JSON.stringify({
        issues: [
          { code: 'QA.THIN_WALL', id: 'thin-wall', severity: 'error' },
          { code: 'QA.WARNING', id: 'overhang', severity: 'warning' },
        ],
        schema: 'evidence-cad-compile-result/v1',
      }),
    );

    const { stdout } = await execFileAsync(process.execPath, [
      A3D.pathname,
      'diagnose',
      result,
      '--id',
      'thin-wall',
    ], { cwd: root });
    const selected = JSON.parse(stdout);

    assert.equal(selected.schema, 'a3d-diagnostics/v1');
    assert.equal(selected.count, 1);
    assert.equal(selected.issues[0].code, 'QA.THIN_WALL');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('diagnose rejects sibling files and symlinks escaping the current workspace', async () => {
  const root = await mkdtemp(join(process.cwd(), '.a3d-boundary-test-'));
  const current = join(root, 'current');
  const sibling = join(root, 'sibling');
  const document = JSON.stringify({
    issues: [{ code: 'QA.EXAMPLE', id: 'example', severity: 'error' }],
    schema: 'evidence-cad-compile-result/v1',
  });
  try {
    await Promise.all([mkdir(current), mkdir(sibling)]);
    const outside = join(sibling, 'result.json');
    await writeFile(outside, document);
    await writeFile(join(current, '..result.json'), document);
    await symlink(outside, join(current, 'outside.json'));
    await symlink(sibling, join(current, 'outside-dir'), 'dir');

    for (const input of [outside, '../sibling/result.json', 'outside.json', 'outside-dir/result.json']) {
      await assert.rejects(
        execFileAsync(process.execPath, [A3D.pathname, 'diagnose', input], { cwd: current }),
        (error: Error & { code?: number; stderr?: string }) => {
          assert.equal(error.code, 2);
          assert.match(error.stderr ?? '', /only reads files inside the current session workspace/u);
          return true;
        },
      );
    }

    const { stdout } = await execFileAsync(process.execPath, [
      A3D.pathname, 'diagnose', '..result.json',
    ], { cwd: current });
    assert.equal(JSON.parse(stdout).count, 1);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('diagnose works when the sandbox denies metadata access to the sessions parent', {
  skip: process.platform !== 'darwin',
}, async () => {
  const root = await mkdtemp(join(process.cwd(), '.a3d-sandbox-test-'));
  const sessions = join(root, 'sessions');
  const current = join(sessions, 'current');
  const profile = join(root, 'sandbox.sb');
  try {
    await mkdir(current, { recursive: true });
    const canonicalSessions = await realpath(sessions);
    await writeFile(profile, [
      '(version 1)',
      '(allow default)',
      `(deny file-read-metadata (literal ${JSON.stringify(canonicalSessions)}))`,
    ].join('\n'));
    const result = join(current, 'result.json');
    await writeFile(result, JSON.stringify({
      issues: [{ code: 'QA.EXAMPLE', id: 'example', severity: 'error' }],
      schema: 'evidence-cad-compile-result/v1',
    }));
    await symlink('result.json', join(current, 'alias.json'));

    // Verify this reproduces the original failure, rather than silently
    // running a permissive sandbox on a different macOS configuration.
    await assert.rejects(execFileAsync('/usr/bin/sandbox-exec', [
      '-f', profile, process.execPath, '-e',
      "require('node:fs').realpathSync(process.cwd())",
    ], { cwd: current }), /EPERM/u);

    for (const input of ['result.json', result, 'alias.json']) {
      const { stdout } = await execFileAsync('/usr/bin/sandbox-exec', [
        '-f', profile, process.execPath, A3D.pathname, 'diagnose', input,
      ], { cwd: current });
      const selected = JSON.parse(stdout);
      assert.equal(selected.count, 1);
      assert.equal(selected.fullResult, await realpath(result));
    }
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
