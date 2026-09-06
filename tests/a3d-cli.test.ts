import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { test } from 'node:test';

const execFileAsync = promisify(execFile);
const A3D = new URL('../bin/a3d.mjs', import.meta.url);

test('lists compact public modeling guides without starting Python', async () => {
  const { stdout } = await execFileAsync(process.execPath, [A3D.pathname, 'guide']);

  assert.match(stdout, /workflow/u);
  assert.match(stdout, /pressable-control/u);
  assert.match(stdout, /multipart/u);
  assert.match(stdout, /color/u);
});

test('prints the targeted pressable-control guide', async () => {
  const { stdout } = await execFileAsync(process.execPath, [
    A3D.pathname,
    'guide',
    'pressable-control',
  ]);

  assert.match(stdout, /retained_slider/u);
  assert.match(stdout, /travel/u);
  assert.match(stdout, /clearance/u);
  assert.ok(stdout.length < 1_500);
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
