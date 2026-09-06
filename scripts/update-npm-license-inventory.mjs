import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const lock = JSON.parse(await readFile(join(root, 'package-lock.json'), 'utf8'));
const packages = new Map();

for (const [path, metadata] of Object.entries(lock.packages ?? {})) {
  if (!path.includes('node_modules/') || metadata.dev) continue;
  const packageMetadata = metadata.link
    ? lock.packages?.[metadata.resolved]
    : metadata;
  const name = path.split('node_modules/').at(-1);
  if (!name || !packageMetadata?.version || !packageMetadata.license) continue;
  packages.set(`${name}@${packageMetadata.version}`, {
    license: packageMetadata.license,
    name,
    version: packageMetadata.version,
  });
}

const inventory = {
  packages: [...packages.values()].sort((left, right) =>
    left.name.localeCompare(right.name) || left.version.localeCompare(right.version),
  ),
};
await writeFile(
  join(root, 'public', 'licenses', 'npm-production-licenses.json'),
  `${JSON.stringify(inventory, null, 2)}\n`,
);
console.log(`Updated npm license inventory with ${inventory.packages.length} packages.`);
