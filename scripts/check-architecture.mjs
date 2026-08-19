import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

import {
  createCheck,
  listFiles,
  readJson,
  repositoryRoot,
} from './lib/repository.mjs';

const check = createCheck('ARCHITECTURE');

for (const path of ['docs/threat-model.md']) {
  if (!existsSync(resolve(repositoryRoot, path))) {
    check.fail(`Required project document is missing: ${path}`);
  }
}

const runtimeAssets = readJson(
  'packages/cad-execution-browser/runtime-assets.json',
);
if (
  !/^[A-Za-z0-9._-]+$/u.test(runtimeAssets.bundle?.fileName ?? '') ||
  !/^[a-f0-9]{64}$/u.test(runtimeAssets.bundle?.sha256 ?? '')
) {
  check.fail('CAD runtime bundle name or pinned SHA-256 is invalid.');
}

for (const asset of runtimeAssets.profileAssets ?? []) {
  if (asset.source.startsWith('third_party/')) {
    check.fail(
      `CAD runtime profile asset must be project-owned: ${asset.source}`,
    );
  }
}

const prohibitedBrowserRuntimes = new Set([
  'electron',
  'playwright',
  'playwright-core',
  '@playwright/test',
  'puppeteer',
  'puppeteer-core',
  'chrome-remote-interface',
  'cef',
  'tauri',
  '@tauri-apps/api',
]);
const productionManifests = [
  'package.json',
  'apps/web/package.json',
  ...readdirSync(resolve(repositoryRoot, 'packages')).map(
    (directory) => `packages/${directory}/package.json`,
  ),
];
for (const manifestPath of productionManifests) {
  if (!existsSync(resolve(repositoryRoot, manifestPath))) continue;
  const packageJson = readJson(manifestPath);
  for (const dependency of Object.keys(packageJson.dependencies ?? {})) {
    if (prohibitedBrowserRuntimes.has(dependency)) {
      check.fail(
        `Production manifest ${manifestPath} embeds prohibited browser runtime ${dependency}.`,
      );
    }
  }
}

const allowedInternalDependencies = {
  '@amagine3d/cad-protocol': [],
  '@amagine3d/cad-agent': ['@amagine3d/cad-protocol'],
  '@amagine3d/web-research': ['@amagine3d/cad-protocol'],
  '@amagine3d/cad-execution-browser': ['@amagine3d/cad-protocol'],
  '@amagine3d/cad-storage-opfs': ['@amagine3d/cad-protocol'],
  '@amagine3d/cad-viewer': ['@amagine3d/cad-protocol'],
};

for (const packageDirectory of readdirSync(
  resolve(repositoryRoot, 'packages'),
)) {
  const packageRoot = resolve(repositoryRoot, 'packages', packageDirectory);
  if (!statSync(packageRoot).isDirectory()) continue;

  const packageJson = readJson(`packages/${packageDirectory}/package.json`);
  const dependencies = Object.keys(packageJson.dependencies ?? {});
  const internalDependencies = dependencies.filter((name) =>
    name.startsWith('@amagine3d/'),
  );
  const allowed = allowedInternalDependencies[packageJson.name];
  if (!allowed) {
    check.fail(`No dependency-boundary policy exists for ${packageJson.name}.`);
    continue;
  }
  for (const dependency of internalDependencies) {
    if (!allowed.includes(dependency)) {
      check.fail(`${packageJson.name} cannot depend on ${dependency}.`);
    }
  }
  if (dependencies.includes('react') || dependencies.includes('react-dom')) {
    check.fail(`${packageJson.name} must remain independent of React.`);
  }

  for (const sourcePath of listFiles(`packages/${packageDirectory}/src`).filter(
    (path) => /\.tsx?$/u.test(path),
  )) {
    const source = readFileSync(join(packageRoot, 'src', sourcePath), 'utf8');
    const deepImports = source.match(
      /from ['"]@amagine3d\/[^'"]+\/[^'"]+['"]/gu,
    );
    if (deepImports) {
      check.fail(
        `${packageJson.name} contains a workspace deep import: ${deepImports[0]}`,
      );
    }
  }
}

check.finish('Architecture check passed: package boundaries are enforced.');
