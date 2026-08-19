import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

export const repositoryRoot = resolve(import.meta.dirname, '../..');

export function readJson(relativePath) {
  return JSON.parse(
    readFileSync(resolve(repositoryRoot, relativePath), 'utf8'),
  );
}

export function listFiles(relativeRoot) {
  const absoluteRoot = resolve(repositoryRoot, relativeRoot);
  if (!existsSync(absoluteRoot)) return [];

  const result = [];
  function walk(currentDirectory, prefix) {
    for (const entry of readdirSync(currentDirectory)) {
      const absolutePath = join(currentDirectory, entry);
      const relativePath = join(prefix, entry);
      if (statSync(absolutePath).isDirectory()) {
        walk(absolutePath, relativePath);
      } else {
        result.push(relativePath);
      }
    }
  }

  walk(absoluteRoot, '');
  return result.sort();
}

export function createCheck(label) {
  const failures = [];
  return {
    fail(message) {
      failures.push(message);
    },
    finish(successMessage) {
      if (failures.length === 0) {
        console.log(successMessage);
        return;
      }
      for (const failure of failures) console.error(`${label}: ${failure}`);
      process.exitCode = 1;
    },
  };
}
