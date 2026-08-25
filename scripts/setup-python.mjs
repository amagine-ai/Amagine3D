#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
const environmentRoot = join(projectRoot, '.venv');
const requirementsPath = join(projectRoot, 'requirements.txt');
const markerPath = join(environmentRoot, '.amagine-setup');
const quiet = process.argv.includes('--quiet');

function log(message) {
  if (!quiet) console.log(message);
}

function fail(message) {
  console.error(`\nPython setup failed: ${message}\n`);
  process.exit(1);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
  });
  return result;
}

function pythonCandidates() {
  return process.platform === 'win32'
    ? [
        { args: ['-3.13'], command: 'py' },
        { args: ['-3.12'], command: 'py' },
        { args: ['-3.11'], command: 'py' },
        { args: ['-3.10'], command: 'py' },
        { args: [], command: 'python' },
      ]
    : [
        { args: [], command: 'python3.13' },
        { args: [], command: 'python3.12' },
        { args: [], command: 'python3.11' },
        { args: [], command: 'python3.10' },
        { args: [], command: 'python3' },
        { args: [], command: 'python' },
      ];
}

function findPython() {
  const probe =
    'import json,sys; print(json.dumps({"path":sys.executable,"version":list(sys.version_info[:3])}))';
  for (const candidate of pythonCandidates()) {
    const result = run(candidate.command, [...candidate.args, '-c', probe], {
      capture: true,
    });
    if (result.status !== 0) continue;
    try {
      const metadata = JSON.parse(result.stdout.trim());
      const [major, minor] = metadata.version;
      if (major === 3 && minor >= 10 && minor <= 13) {
        return { ...candidate, metadata };
      }
    } catch {
      // Try the next candidate when an unrelated executable owns this name.
    }
  }
  return undefined;
}

function environmentPython() {
  return process.platform === 'win32'
    ? join(environmentRoot, 'Scripts', 'python.exe')
    : join(environmentRoot, 'bin', 'python');
}

if (!existsSync(requirementsPath)) {
  fail(`missing ${requirementsPath}`);
}

const hostPython = findPython();
if (!hostPython) {
  fail(
    'Python 3.10 through 3.13 was not found. Install Python from https://python.org and run npm run python:setup again. Conda is not required.',
  );
}

const requirements = readFileSync(requirementsPath);
const fingerprint = createHash('sha256')
  .update(requirements)
  .update(JSON.stringify(hostPython.metadata.version.slice(0, 2)))
  .digest('hex');

const currentMarker = existsSync(markerPath)
  ? readFileSync(markerPath, 'utf8').trim()
  : '';
const venvPython = environmentPython();

if (currentMarker === fingerprint && existsSync(venvPython)) {
  const check = run(
    venvPython,
    ['-c', 'import build123d, lib3mf, numpy, PIL, rtree, trimesh'],
    { capture: true },
  );
  if (check.status === 0) {
    log(`Python runtime ready: ${venvPython}`);
    process.exit(0);
  }
}

if (existsSync(environmentRoot) && !existsSync(venvPython)) {
  log('Removing an incomplete local Python environment...');
  rmSync(environmentRoot, { force: true, recursive: true });
}

if (!existsSync(venvPython)) {
  log(`Creating .venv with Python ${hostPython.metadata.version.join('.')}...`);
  const created = run(hostPython.command, [
    ...hostPython.args,
    '-m',
    'venv',
    environmentRoot,
  ]);
  if (created.status !== 0) {
    fail('could not create .venv. Ensure the standard-library venv module is installed.');
  }
}

log('Installing the pinned CAD runtime into .venv...');
const installed = run(venvPython, [
  '-m',
  'pip',
  'install',
  '--disable-pip-version-check',
  '--requirement',
  requirementsPath,
]);
if (installed.status !== 0) {
  fail('pip could not install the CAD wheels for this platform. See the output above.');
}

const verified = run(
  venvPython,
  ['-c', 'import build123d, lib3mf, numpy, PIL, rtree, trimesh'],
  { capture: true },
);
if (verified.status !== 0) {
  fail(verified.stderr.trim() || 'the installed CAD runtime could not be imported.');
}

mkdirSync(environmentRoot, { recursive: true });
writeFileSync(markerPath, `${fingerprint}\n`, 'utf8');
log(`Python runtime ready: ${venvPython}`);
