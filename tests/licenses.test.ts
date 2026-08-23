import { strict as assert } from 'node:assert';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test } from 'node:test';

import { curatedLicenses } from '../src/lib/licenses.ts';

const root = resolve(import.meta.dirname, '..');

test('README badges and acknowledgments match current runtime dependencies', async () => {
  const [packageJson, readme, chineseReadme] = await Promise.all([
    readFile(resolve(root, 'package.json'), 'utf8').then(JSON.parse) as Promise<{
      devDependencies: Record<string, string>;
      engines: { node: string };
      license: string;
    }>,
    readFile(resolve(root, 'README.md'), 'utf8'),
    readFile(resolve(root, 'docs/README.zh-CN.md'), 'utf8'),
  ]);
  assert.equal(packageJson.engines.node, '>=20.19.0');
  assert.equal(packageJson.devDependencies.vite, '7.3.6');
  assert.equal(packageJson.license, 'Apache-2.0');
  for (const document of [readme, chineseReadme]) {
    assert.match(document, /Node\.js-20\.19%2B/u);
    assert.match(document, /Vite-7\.3\.6/u);
    const acknowledgments = document.split(/## (?:Core Dependencies|核心依赖)/u)[1] ?? '';
    assert.doesNotMatch(acknowledgments, /Pyodide|OCP\.wasm|Vercel AI SDK/u);
  }
});

test('license page inventory covers direct production dependencies', async () => {
  const [packageJson, inventory] = await Promise.all([
    readFile(resolve(root, 'package.json'), 'utf8').then(JSON.parse) as Promise<{
      dependencies: Record<string, string>;
    }>,
    readFile(
      resolve(root, 'public/licenses/npm-production-licenses.json'),
      'utf8',
    ).then(JSON.parse) as Promise<{
      packages: Array<{ license: string; name: string; version: string }>;
    }>,
  ]);
  const names = new Set(inventory.packages.map(({ name }) => name));
  for (const dependency of Object.keys(packageJson.dependencies)) {
    assert.ok(names.has(dependency), `${dependency} is missing from license inventory`);
  }
  assert.deepEqual(
    curatedLicenses
      .filter(({ name }) => ['OCP.wasm', 'Pyodide', 'Vercel AI SDK'].includes(name))
      .map(({ name }) => name),
    [],
  );
  assert.ok(curatedLicenses.some(({ name }) => name === 'PI coding agent'));
});

test('curated runtime components expose their checked-in license files', async () => {
  for (const component of curatedLicenses) {
    assert.ok(component.files.length > 0, `${component.name} has no license file`);
    for (const file of component.files) {
      assert.match(file.href, /^\/licenses\/[a-z0-9.-]+$/u);
      await readFile(resolve(root, 'public', file.href.slice(1)), 'utf8');
    }
  }

  const [notice, publicNotice, thirdPartyNotices] = await Promise.all([
    readFile(resolve(root, 'NOTICE'), 'utf8'),
    readFile(resolve(root, 'public/licenses/amagine3d-notice.txt'), 'utf8'),
    readFile(resolve(root, 'docs/THIRD_PARTY_NOTICES.md'), 'utf8'),
  ]);
  assert.equal(publicNotice, notice);
  assert.match(notice, /build123d Contributors/u);
  assert.match(thirdPartyNotices, /Source-distribution boundary/u);
});
