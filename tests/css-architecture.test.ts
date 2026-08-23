import { strict as assert } from 'node:assert';
import { readdir, readFile } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { test } from 'node:test';

const root = resolve(import.meta.dirname, '..');
const sourceRoot = resolve(root, 'src');

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = resolve(directory, entry.name);
      return entry.isDirectory() ? sourceFiles(path) : Promise.resolve([path]);
    }),
  );
  return nested.flat();
}

test('CSS Modules stay colocated and independent', async () => {
  const files = await sourceFiles(sourceRoot);
  const modules = files.filter((file) => file.endsWith('.module.css'));
  const importedModules = new Set<string>();

  for (const module of modules) {
    const source = await readFile(module, 'utf8');
    assert.doesNotMatch(
      source,
      /@import\b/u,
      `${relative(root, module)} must not aggregate another stylesheet`,
    );
    assert.doesNotMatch(
      source,
      /!important\b/u,
      `${relative(root, module)} must not rely on !important`,
    );
  }

  for (const component of files.filter((file) => file.endsWith('.tsx'))) {
    const source = await readFile(component, 'utf8');
    for (const match of source.matchAll(/from\s+['"](\.\/[^'"]+\.module\.css)['"]/gu)) {
      const module = resolve(dirname(component), match[1]);
      assert.equal(
        dirname(module),
        dirname(component),
        `${relative(root, component)} imports a non-colocated CSS Module`,
      );
      importedModules.add(module);
    }
  }

  assert.deepEqual(
    [...modules].sort(),
    [...importedModules].sort(),
    'every CSS Module should be imported by a colocated component',
  );
});
