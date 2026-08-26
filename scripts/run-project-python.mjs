#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
const executable = process.platform === 'win32'
  ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : join(projectRoot, '.venv', 'bin', 'python');

if (!existsSync(executable)) {
  console.error('Project Python is not ready. Run npm run python:setup first.');
  process.exit(1);
}

const result = spawnSync(executable, process.argv.slice(2), {
  cwd: projectRoot,
  stdio: 'inherit',
});
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
