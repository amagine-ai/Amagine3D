import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { test } from 'node:test';

const execFileAsync = promisify(execFile);
const A3D = new URL('../bin/a3d.mjs', import.meta.url);

async function withDiagnostics(
  issues: unknown[],
  run: (query: (...args: string[]) => Promise<{ stdout: string; result: any }>, file: string) => Promise<void>,
) {
  const root = await mkdtemp(join(process.cwd(), '.a3d-diagnostic-budget-'));
  const file = join(root, 'result.json');
  try {
    await writeFile(file, JSON.stringify({ schema: 'evidence-cad-compile-result/v1', issues }));
    await run(async (...args) => {
      const { stdout } = await execFileAsync(process.execPath, [A3D.pathname, 'diagnose', file, ...args], {
        cwd: root, maxBuffer: 4 * 1024 * 1024,
      });
      return { stdout, result: JSON.parse(stdout) };
    }, file);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
}

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

test('one query returns installed primitive signatures and constructor overloads', async () => {
  const { stdout } = await execFileAsync(process.execPath, [
    A3D.pathname, 'capabilities', '--symbol', 'Cylinder', '--symbol', 'extrude', '--symbol', 'Pos',
  ]);
  const { query } = JSON.parse(stdout);
  assert.ok(query.Cylinder.parameters.includes('rotation'));
  assert.ok(!query.Cylinder.parameters.includes('axis'));
  assert.match(query.extrude.signature, /amount: float/u);
  assert.ok(query.Pos.overloadSignatures.some((signature: string) => signature.includes('X: float')));
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
  const completeCause = `${'frame details\n'.repeat(100)}TypeError: unexpected keyword argument 'axis'`;
  try {
    await writeFile(
      result,
      JSON.stringify({
        issues: [
          { code: 'QA.THIN_WALL', id: 'thin-wall', severity: 'error', message: completeCause },
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
    assert.ok(selected.issues[0].message.length <= 700);
    assert.ok(selected.issues[0].message.endsWith("TypeError: unexpected keyword argument 'axis'"));
    assert.equal(selected.projectionTruncated, true);
    assert.equal(selected.hasMore, false);
    const { stdout: full } = await execFileAsync(process.execPath, [
      A3D.pathname, 'diagnose', result, '--id', 'thin-wall', '--full',
    ], { cwd: root });
    assert.equal(JSON.parse(full).issues[0].message, completeCause);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('diagnose bounds serialized nested evidence even for a selected id and escaping', async () => {
  const cause = "TypeError: unexpected keyword argument 'axis'";
  const issue = {
    id: 'axis-error', code: 'SOURCE.EXECUTION_FAILED', severity: 'error', stage: 'source',
    part: 'housing', featureId: 'housing/window',
    blockedBy: 'missing-owner', source: { file: 'part_build.py', line: 69 },
    message: `${'\u0000"\\\n😀'.repeat(8000)}${cause}`,
    actual: {
      overlapMm3: 1.25,
      details: Array.from({ length: 1000 }, (_, i) => ({ id: `probe-${i}`, path: '\u0001\\'.repeat(1000), depthMm: 0.4 })),
    },
    expected: { overlapMm3: 0.01 },
  };
  await withDiagnostics([issue], async (query, file) => {
    for (const args of [[], ['--id', issue.id]]) {
      const { stdout, result } = await query(...args);
      assert.ok(stdout.length <= 12_000, `serialized length ${stdout.length}`);
      assert.equal(result.total, 1);
      assert.equal(result.count, 1);
      assert.equal(result.truncated, true);
      assert.equal(result.projectionTruncated, true);
      assert.equal(result.fullResult, await realpath(file));
      assert.equal(result.issues[0].id, issue.id);
      assert.equal(result.issues[0].featureId, issue.featureId);
      assert.equal(result.issues[0].blockedBy, issue.blockedBy);
      assert.deepEqual(result.issues[0].source, issue.source);
      assert.ok(result.issues[0].message.endsWith(cause));
      assert.equal(result.issues[0].actual.overlapMm3, 1.25);
      assert.equal(result.issues[0].expected.overlapMm3, 0.01);
    }
  });
});

test('diagnostic pages preserve all matching identities with stable next offsets', async () => {
  const issues = Array.from({ length: 23 }, (_, i) => ({
    id: `issue-${i}`, code: 'QA.EXAMPLE', severity: 'error', message: `Failed check ${i}`,
  }));
  await withDiagnostics([...issues, { id: 'warning', code: 'QA.WARNING', severity: 'warning' }], async (query) => {
    let offset = 0;
    const ids: string[] = [];
    do {
      const { stdout, result } = await query('--offset', String(offset));
      assert.ok(stdout.length <= 12_000);
      assert.equal(result.total, issues.length);
      assert.ok(result.count > 0 && result.count <= 5);
      assert.equal(result.projectionTruncated, false);
      ids.push(...result.issues.map((issue: { id: string }) => issue.id));
      if (!result.hasMore) {
        assert.equal(result.nextOffset, null);
        break;
      }
      assert.equal(result.nextOffset, offset + result.count);
      offset = result.nextOffset;
    } while (offset < issues.length);
    assert.deepEqual(ids, issues.map((issue) => issue.id));
    const small = (await query('--offset', '5', '--limit', '2')).result;
    assert.equal(small.count, 2);
    assert.equal(small.nextOffset, 7);
    const capped = (await query('--limit', '100')).result;
    assert.equal(capped.count, 5);
    const empty = (await query('--offset', '100')).result;
    assert.equal(empty.count, 0);
    assert.equal(empty.hasMore, false);
    assert.equal(empty.nextOffset, null);
    const warning = (await query('--severity', 'warning')).result;
    assert.deepEqual(warning.issues.map((issue: { id: string }) => issue.id), ['warning']);
  });
});

test('budget-driven issue pagination makes progress when five detailed errors cannot fit', async () => {
  const issues = Array.from({ length: 9 }, (_, i) => ({
    id: `large-${i}`, code: 'QA.EXAMPLE', severity: 'error',
    message: `${'\u0000'.repeat(800)}last cause ${i}`,
    actual: { overlapMm3: i, details: '"\\'.repeat(2000) },
  }));
  await withDiagnostics(issues, async (query) => {
    let offset = 0;
    const ids: string[] = [];
    while (offset < issues.length) {
      const { stdout, result } = await query('--offset', String(offset));
      assert.ok(stdout.length <= 12_000);
      assert.ok(result.count > 0 && result.count <= 5);
      ids.push(...result.issues.map((issue: { id: string }) => issue.id));
      offset += result.count;
      assert.equal(result.nextOffset, offset < issues.length ? offset : null);
    }
    assert.deepEqual(ids, issues.map((issue) => issue.id));
  });
});

test('field chunks obey the escaped JSON budget and recover the exact original value', async () => {
  const actual = { label: '\u0000"\\\n😀'.repeat(2500), overlapMm3: 0.25, nested: [1, null, false] };
  await withDiagnostics([{ id: 'detail', code: 'QA.EXAMPLE', severity: 'error', actual }], async (query) => {
    const first = await query('--id', 'detail', '--field', 'actual');
    assert.ok(first.stdout.length <= 12_000);
    assert.ok(first.result.count > 0 && first.result.count <= 2000);
    assert.equal(first.result.encoding, 'json');
    assert.equal(first.result.offsetUnit, 'utf16-code-units');
    let collected = first.result.data;
    let offset = first.result.nextOffset;
    while (offset !== null) {
      const { stdout, result } = await query('--id', 'detail', '--field', 'actual', '--offset', String(offset), '--limit', '20000');
      assert.ok(stdout.length <= 12_000);
      assert.ok(result.count > 0 && result.count <= 20000);
      assert.equal(result.offset, offset);
      assert.equal(result.count, result.data.length);
      collected += result.data;
      offset = result.nextOffset;
    }
    assert.equal(collected, JSON.stringify(actual));
    assert.deepEqual(JSON.parse(collected), actual);
    const beyond = (await query('--id', 'detail', '--field', 'actual', '--offset', String(collected.length + 1))).result;
    assert.equal(beyond.count, 0);
    assert.equal(beyond.hasMore, false);
    assert.equal(beyond.data, '');
    const full = await query('--id', 'detail', '--field', 'actual', '--full');
    assert.ok(full.stdout.length > 12_000);
    assert.equal(full.result.truncated, false);
    assert.equal(full.result.total, JSON.stringify(actual).length);
    assert.equal(full.result.count, full.result.total);
    assert.equal(full.result.nextOffset, null);
    assert.deepEqual(full.result.value, actual);
  });
});

test('full issue output is an explicit unbounded selection and pagination options are validated', async () => {
  const issue = { id: 'chosen', severity: 'error', code: 'QA.EXAMPLE', message: 'full evidence '.repeat(2000), actual: { count: 9 } };
  await withDiagnostics([issue, { id: 'other', severity: 'error', code: 'QA.OTHER' }], async (query, file) => {
    const full = await query('--id', 'chosen', '--full');
    assert.ok(full.stdout.length > 12_000);
    assert.equal(full.result.count, 1);
    assert.equal(full.result.truncated, false);
    assert.deepEqual(full.result.issues, [issue]);
    const invalid = [
      ['--limit', '0'], ['--limit', '-1'], ['--offset', '-1'], ['--offset', '1.2'],
      ['--limit', '9007199254740992'], ['--offset', '1e3'], ['--offset'],
      ['--full', '--offset', '0'], ['--limit', '5', '--full'],
      ['--field', 'actual'], ['--id', 'chosen', '--field', 'missing'],
      ['--id', 'absent', '--field', 'actual'], ['--id', 'chosen', '--field', '__proto__'],
      ['--id', 'chosen', '--id', 'other'], ['--full', '--full'], ['--wat'], ['--severity', 'fatal'],
    ];
    for (const args of invalid) {
      await assert.rejects(query(...args), (error: Error & { code?: number }) => {
        assert.equal(error.code, 2, args.join(' '));
        return true;
      });
    }
    await writeFile(file, JSON.stringify({ schema: 'unrelated', issues: [issue] }));
    await assert.rejects(query(), /not an evidence-cad-compile-result/u);
  });
  await withDiagnostics([issue, issue], async (query) => {
    await assert.rejects(query('--id', issue.id, '--field', 'actual'), /exactly one/u);
  });
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
