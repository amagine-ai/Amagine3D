import { createHash } from 'node:crypto';
import {
  copyFile,
  mkdir,
  readdir,
  readFile,
  rename,
  writeFile,
} from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const appRoot = resolve(import.meta.dirname, '..');
const repositoryRoot = resolve(appRoot, '..', '..');
const cachesBase = resolve(appRoot, 'public/cad-runtime');
const runtimeManifestPath = resolve(
  repositoryRoot,
  'packages/cad-execution-browser/runtime-assets.json',
);
const runtimeManifest = JSON.parse(await readFile(runtimeManifestPath, 'utf8'));
const pyodideRoot = resolve(
  repositoryRoot,
  'packages/cad-execution-browser/node_modules/pyodide',
);
const outputRoot = resolve(
  appRoot,
  'public/cad-runtime',
  runtimeManifest.cacheKey,
);
const pyodideOutput = resolve(outputRoot, 'pyodide');
const wheelOutput = resolve(outputRoot, 'wheels');
const pyodideCdn = `https://cdn.jsdelivr.net/pyodide/v${runtimeManifest.pyodide.version}/full`;
const runtimeBundlePath = resolve(outputRoot, runtimeManifest.bundle.fileName);

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function hasExpectedHash(path, expectedHash) {
  try {
    return sha256(await readFile(path)) === expectedHash;
  } catch {
    return false;
  }
}

async function copyVerified(source, destination, expectedHash) {
  if (await hasExpectedHash(destination, expectedHash)) return false;
  const bytes = await readFile(source);
  const actualHash = sha256(bytes);
  if (actualHash !== expectedHash) {
    throw new Error(
      `Local CAD runtime asset ${source} has SHA-256 ${actualHash}, expected ${expectedHash}.`,
    );
  }
  await mkdir(dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.tmp`;
  await copyFile(source, temporary);
  await rename(temporary, destination);
  return true;
}

async function downloadVerified(url, destination, expectedHash, relativePath) {
  if (await hasExpectedHash(destination, expectedHash)) return false;
  if (
    relativePath !== undefined &&
    (await copyVerifiedFromCaches(relativePath, destination, expectedHash))
  ) {
    return true;
  }
  const response = await globalThis.fetch(url, { redirect: 'follow' });
  if (!response.ok) {
    throw new Error(`Unable to download ${url}: HTTP ${response.status}.`);
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  const actualHash = sha256(bytes);
  if (actualHash !== expectedHash) {
    throw new Error(
      `Downloaded CAD runtime asset ${url} has SHA-256 ${actualHash}, expected ${expectedHash}.`,
    );
  }
  await mkdir(dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, bytes);
  await rename(temporary, destination);
  return true;
}

async function copyVerifiedFromCaches(relativePath, destination, expectedHash) {
  let siblingKeys;
  try {
    siblingKeys = (await readdir(cachesBase)).filter(
      (name) => name !== runtimeManifest.cacheKey,
    );
  } catch {
    siblingKeys = [];
  }
  for (const siblingKey of siblingKeys) {
    const candidate = resolve(cachesBase, siblingKey, relativePath);
    if (await hasExpectedHash(candidate, expectedHash)) {
      await mkdir(dirname(destination), { recursive: true });
      const temporary = `${destination}.${process.pid}.tmp`;
      await copyFile(candidate, temporary);
      await rename(temporary, destination);
      return true;
    }
  }
  return false;
}

function pyodidePackageClosure(lock) {
  const selected = new Set();
  const queue = [...runtimeManifest.pyodide.packageSeeds];
  while (queue.length > 0) {
    const packageName = queue
      .shift()
      .toLowerCase()
      .replaceAll(/[._-]+/gu, '-');
    if (selected.has(packageName)) continue;
    const packageRecord = lock.packages[packageName];
    if (packageRecord === undefined) {
      throw new Error(
        `Pyodide ${runtimeManifest.pyodide.version} does not contain required package ${packageName}.`,
      );
    }
    selected.add(packageName);
    queue.push(...packageRecord.depends);
  }
  return [...selected].sort();
}

async function runPool(items, task, concurrency = 6) {
  const queue = [...items];
  await Promise.all(
    Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
      while (queue.length > 0) {
        const item = queue.shift();
        if (item !== undefined) await task(item);
      }
    }),
  );
}

async function writeRuntimeBundle(entries) {
  const ordered = [...entries].sort((left, right) =>
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
  );
  const seen = new Set();
  const payloads = [];
  const index = [];
  let offset = 0;
  for (const entry of ordered) {
    if (seen.has(entry.path)) {
      throw new Error(`Duplicate CAD runtime bundle path: ${entry.path}`);
    }
    seen.add(entry.path);
    const bytes = await readFile(entry.source);
    index.push({ path: entry.path, offset, length: bytes.byteLength });
    payloads.push(bytes);
    offset += bytes.byteLength;
  }
  const header = Buffer.from(
    JSON.stringify({ schemaVersion: 1, entries: index }),
    'utf8',
  );
  const prefix = Buffer.alloc(12);
  prefix.write('AM3DRT01', 0, 'ascii');
  prefix.writeUInt32LE(header.byteLength, 8);
  const bundle = Buffer.concat([prefix, header, ...payloads]);
  const actualHash = sha256(bundle);
  let previousHash;
  try {
    previousHash = sha256(await readFile(runtimeBundlePath));
  } catch {
    previousHash = undefined;
  }
  if (previousHash !== undefined && previousHash !== actualHash) {
    throw new Error(
      `CAD runtime bundle changed under cacheKey ${runtimeManifest.cacheKey} (${previousHash} -> ${actualHash}). Bump cacheKey in runtime-assets.json so immutable clients do not reuse a stale bundle.`,
    );
  }
  const temporary = `${runtimeBundlePath}.${process.pid}.tmp`;
  await writeFile(temporary, bundle);
  await rename(temporary, runtimeBundlePath);
  if (actualHash !== runtimeManifest.bundle.sha256) {
    throw new Error(
      `CAD runtime bundle SHA-256 is ${actualHash}, expected ${runtimeManifest.bundle.sha256}.`,
    );
  }
  return actualHash;
}

await mkdir(pyodideOutput, { recursive: true });
await mkdir(wheelOutput, { recursive: true });

let changed = 0;
for (const asset of runtimeManifest.pyodide.coreAssets) {
  if (
    await copyVerified(
      resolve(pyodideRoot, asset.fileName),
      resolve(pyodideOutput, asset.fileName),
      asset.sha256,
    )
  ) {
    changed += 1;
  }
}

for (const asset of runtimeManifest.profileAssets) {
  if (
    await copyVerified(
      resolve(repositoryRoot, asset.source),
      resolve(outputRoot, asset.path),
      asset.sha256,
    )
  ) {
    changed += 1;
  }
}

const pyodideLock = JSON.parse(
  await readFile(resolve(pyodideRoot, 'pyodide-lock.json'), 'utf8'),
);
const pyodidePackages = pyodidePackageClosure(pyodideLock);
await runPool(pyodidePackages, async (packageName) => {
  const packageRecord = pyodideLock.packages[packageName];
  const fileName = packageRecord.file_name;
  if (
    await downloadVerified(
      `${pyodideCdn}/${fileName}`,
      resolve(pyodideOutput, fileName),
      packageRecord.sha256,
      `pyodide/${fileName}`,
    )
  ) {
    changed += 1;
  }
});

await runPool(runtimeManifest.pythonWheels, async (wheel) => {
  if (
    await downloadVerified(
      wheel.url,
      resolve(wheelOutput, wheel.fileName),
      wheel.sha256,
      `wheels/${wheel.fileName}`,
    )
  ) {
    changed += 1;
  }
});

const bundleEntries = [
  ...runtimeManifest.pyodide.coreAssets
    .filter((asset) => asset.serveStatic !== true)
    .map((asset) => ({
      path: `pyodide/${asset.fileName}`,
      source: resolve(pyodideOutput, asset.fileName),
    })),
  ...pyodidePackages.map((packageName) => ({
    path: `pyodide/${pyodideLock.packages[packageName].file_name}`,
    source: resolve(pyodideOutput, pyodideLock.packages[packageName].file_name),
  })),
  ...runtimeManifest.pythonWheels.map((wheel) => ({
    path: `wheels/${wheel.fileName}`,
    source: resolve(wheelOutput, wheel.fileName),
  })),
  ...runtimeManifest.profileAssets.map((asset) => ({
    path: asset.path,
    source: resolve(outputRoot, asset.path),
  })),
];
await writeRuntimeBundle(bundleEntries);

const completion = {
  schemaVersion: 1,
  cacheKey: runtimeManifest.cacheKey,
  pyodideVersion: runtimeManifest.pyodide.version,
  pyodidePackages,
  profileAssets: runtimeManifest.profileAssets.map((asset) => asset.path),
  pythonWheels: runtimeManifest.pythonWheels.map((wheel) => wheel.fileName),
  runtimeBundle: {
    fileName: runtimeManifest.bundle.fileName,
    sha256: runtimeManifest.bundle.sha256,
    entryCount: bundleEntries.length,
  },
};
await writeFile(
  resolve(outputRoot, 'complete.json'),
  `${JSON.stringify(completion, null, 2)}\n`,
);

console.log(
  `CAD runtime ${runtimeManifest.cacheKey} ready: ${String(pyodidePackages.length)} Pyodide packages, ${String(runtimeManifest.pythonWheels.length)} pinned wheels, ${String(changed)} asset(s) updated.`,
);
