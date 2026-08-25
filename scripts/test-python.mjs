#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const python =
  process.platform === 'win32'
    ? join(root, '.venv', 'Scripts', 'python.exe')
    : join(root, '.venv', 'bin', 'python');
const result = spawnSync(
  python,
  ['-m', 'unittest', 'discover', '-s', 'tests/python', '-p', 'test_*.py'],
  { cwd: root, stdio: 'inherit' },
);
if (result.error) {
  console.error(`Python tests could not start: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
