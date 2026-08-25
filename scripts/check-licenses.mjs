import { access, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const publicLicenseRoot = join(root, 'public', 'licenses');

const allowedProductionLicenses = new Set([
  '0BSD',
  'Apache-2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'BlueOak-1.0.0',
  'CC0-1.0',
  'ISC',
  'MIT',
  'OFL-1.1',
]);

const requiredFiles = [
  'amagine3d-notice.txt',
  'apache-2.0.txt',
  'build123d-notice.txt',
  'build123d.txt',
  'ibm-plex-sans.txt',
  'jetbrains-mono.txt',
  'lib3mf.txt',
  'npm-production-licenses.json',
  'opencascade-exception.txt',
  'opencascade-lgpl-2.1.txt',
  'pi.txt',
  'react.txt',
  'rtree.txt',
  'three.txt',
  'trimesh.txt',
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

const packageJson = await readJson(join(root, 'package.json'));
const packageLock = await readJson(join(root, 'package-lock.json'));
const inventory = await readJson(
  join(publicLicenseRoot, 'npm-production-licenses.json'),
);

assert(
  packageJson.license === 'Apache-2.0',
  'package.json must declare Apache-2.0',
);

const inventoryByName = new Map(
  inventory.packages.map((entry) => [entry.name, entry]),
);
for (const dependency of Object.keys(packageJson.dependencies ?? {})) {
  const entry = inventoryByName.get(dependency);
  assert(entry, `${dependency} is missing from the production license inventory`);
  const locked = packageLock.packages?.[`node_modules/${dependency}`]?.version;
  assert(
    !locked || entry.version === locked,
    `${dependency} license inventory version does not match package-lock.json`,
  );
}

const expectedProductionPackages = new Map();
for (const [path, metadata] of Object.entries(packageLock.packages ?? {})) {
  if (!path.includes('node_modules/') || metadata.dev) continue;
  const packageMetadata = metadata.link
    ? packageLock.packages?.[metadata.resolved]
    : metadata;
  const name = path.split('node_modules/').at(-1);
  const key = `${name}@${packageMetadata?.version}`;
  assert(
    packageMetadata?.license,
    `${key} has no license metadata in package-lock.json`,
  );
  expectedProductionPackages.set(key, {
    license: packageMetadata.license,
    name,
    version: packageMetadata.version,
  });
}

const actualProductionPackages = new Map(
  inventory.packages.map((entry) => [`${entry.name}@${entry.version}`, entry]),
);
assert(
  actualProductionPackages.size === expectedProductionPackages.size,
  'production license inventory package count does not match package-lock.json',
);
for (const [key, expected] of expectedProductionPackages) {
  const actual = actualProductionPackages.get(key);
  assert(actual, `${key} is missing from the production license inventory`);
  assert(
    actual.license === expected.license,
    `${key} license does not match package-lock.json`,
  );
}

for (const entry of inventory.packages) {
  assert(
    allowedProductionLicenses.has(entry.license),
    `${entry.name}@${entry.version} uses unreviewed license ${entry.license}`,
  );
}

for (const filename of requiredFiles) {
  await access(join(publicLicenseRoot, filename));
}

const notice = await readFile(join(root, 'NOTICE'), 'utf8');
const publicNotice = await readFile(
  join(publicLicenseRoot, 'amagine3d-notice.txt'),
  'utf8',
);
assert(notice === publicNotice, 'NOTICE and its public copy must remain identical');
assert(
  notice.includes('Copyright (c) 2022–2025 The build123d Contributors'),
  'NOTICE must retain the build123d attribution',
);

const gitignore = await readFile(join(root, '.gitignore'), 'utf8');
for (const ignoredPath of [
  '.amagine-state/',
  '.venv/',
  'dist/',
  'node_modules/',
  'workspace/*',
]) {
  assert(
    gitignore.split(/\r?\n/u).includes(ignoredPath),
    `${ignoredPath} must remain outside the source distribution`,
  );
}

console.log(
  `License compliance checks passed for ${inventory.packages.length} production npm packages.`,
);
