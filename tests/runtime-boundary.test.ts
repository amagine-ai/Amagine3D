import { strict as assert } from 'node:assert';
import { readFile, readdir } from 'node:fs/promises';
import {
  dirname,
  extname,
  isAbsolute,
  join,
  relative,
  resolve,
} from 'node:path';
import { test } from 'node:test';

const ROOT = resolve(import.meta.dirname, '..');
const RUNTIME_SOURCE = join(ROOT, 'packages', 'a3d-runtime', 'src');
const SOURCE_EXTENSIONS = new Set(['.mjs', '.ts', '.tsx']);

async function sourceFiles(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const path = join(root, entry.name);
      if (entry.isDirectory()) return sourceFiles(path);
      return SOURCE_EXTENSIONS.has(extname(entry.name)) ? [path] : [];
    }),
  );
  return nested.flat();
}

test('only the runtime package imports the Codex SDK', async () => {
  const applicationFiles = (
    await Promise.all(
      ['server', 'src', 'tests', 'scripts'].map((directory) =>
        sourceFiles(join(ROOT, directory)),
      ),
    )
  ).flat();
  for (const path of applicationFiles) {
    const source = await readFile(path, 'utf8');
    assert.doesNotMatch(
      source,
      /(?:from\s+|import\s*)['"]@openai\/codex(?:-sdk)?['"]/u,
      `${relative(ROOT, path)} must use @amagine3d/a3d-runtime`,
    );
  }
});

test('runtime source does not import application internals', async () => {
  for (const path of await sourceFiles(RUNTIME_SOURCE)) {
    const source = await readFile(path, 'utf8');
    for (const match of source.matchAll(/from\s+['"]([^'"]+)['"]/gu)) {
      const specifier = match[1]!;
      if (!specifier.startsWith('.')) continue;
      const target = resolve(dirname(path), specifier);
      const relativeTarget = relative(RUNTIME_SOURCE, target);
      assert.equal(
        relativeTarget.startsWith('..') || isAbsolute(relativeTarget),
        false,
        `${relative(ROOT, path)} imports outside the runtime package: ${specifier}`,
      );
    }
  }
});
