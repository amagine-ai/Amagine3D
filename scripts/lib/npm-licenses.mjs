import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { repositoryRoot } from './repository.mjs';

export function readProductionLicenseInventory() {
  let rawDependencyTree;
  try {
    rawDependencyTree = execFileSync(
      process.env.npm_execpath ?? 'pnpm',
      ['list', '--json', '--prod', '--depth', 'Infinity'],
      {
        cwd: repositoryRoot,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    );
  } catch (error) {
    const detail =
      error && typeof error === 'object' && 'stderr' in error
        ? String(error.stderr).trim()
        : '';
    throw new Error(
      `pnpm could not enumerate the production dependency graph${detail ? `: ${detail}` : '.'}`,
      { cause: error },
    );
  }
  const dependencyTree = JSON.parse(rawDependencyTree);
  const packageMap = new Map();

  function visitDependencies(dependencies) {
    for (const dependency of Object.values(dependencies ?? {})) {
      if (dependency.path?.includes('/node_modules/')) {
        const manifestPath = resolve(dependency.path, 'package.json');
        if (!existsSync(manifestPath)) {
          visitDependencies(dependency.dependencies);
          visitDependencies(dependency.optionalDependencies);
          continue;
        }
        const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
        const license = normalizeLicense(manifest);
        const key = `${manifest.name}@${manifest.version}`;
        const existing = packageMap.get(key);
        if (existing) {
          if (!existing.paths.includes(dependency.path)) {
            existing.paths.push(dependency.path);
          }
        } else {
          packageMap.set(key, {
            name: manifest.name,
            version: manifest.version,
            license,
            ...(normalizeHomepage(manifest) === undefined
              ? {}
              : { homepage: normalizeHomepage(manifest) }),
            paths: [dependency.path],
          });
        }
      }
      visitDependencies(dependency.dependencies);
      visitDependencies(dependency.optionalDependencies);
    }
  }

  for (const workspace of dependencyTree) {
    visitDependencies(workspace.dependencies);
    visitDependencies(workspace.optionalDependencies);
  }

  const packages = [...packageMap.values()].sort((left, right) =>
    `${left.name}@${left.version}`.localeCompare(
      `${right.name}@${right.version}`,
    ),
  );
  const groupedReport = Object.groupBy(packages, ({ license }) => license);

  return { groupedReport, packages };
}

function normalizeLicense(manifest) {
  if (typeof manifest.license === 'string') return manifest.license;
  if (typeof manifest.license?.type === 'string') return manifest.license.type;
  if (Array.isArray(manifest.licenses)) {
    const expressions = manifest.licenses
      .map((license) => (typeof license === 'string' ? license : license?.type))
      .filter(Boolean);
    if (expressions.length > 0) return expressions.join(' OR ');
  }
  return 'UNKNOWN';
}

function normalizeHomepage(manifest) {
  if (typeof manifest.homepage === 'string') return manifest.homepage;
  const repository =
    typeof manifest.repository === 'string'
      ? manifest.repository
      : manifest.repository?.url;
  if (typeof repository !== 'string') return undefined;
  return repository
    .replace(/^git\+/u, '')
    .replace(/^git:\/\/github\.com\//u, 'https://github.com/')
    .replace(/\.git$/u, '');
}

export function publicPackageRecord(packageRecord) {
  return {
    name: packageRecord.name,
    version: packageRecord.version,
    license: packageRecord.license,
    ...(packageRecord.homepage === undefined
      ? {}
      : { homepage: packageRecord.homepage }),
  };
}
