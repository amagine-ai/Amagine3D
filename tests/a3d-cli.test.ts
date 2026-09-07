import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
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
