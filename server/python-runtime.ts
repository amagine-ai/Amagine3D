import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { delimiter, join } from 'node:path';

import type { PythonHealth } from '../src/types.ts';

export function venvPython(projectRoot: string): string {
  return process.platform === 'win32'
    ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : join(projectRoot, '.venv', 'bin', 'python');
}

export function activateProjectPython(projectRoot: string): PythonHealth {
  const executable = venvPython(projectRoot);
  if (!existsSync(executable)) {
    return { executable: null, ready: false, version: null };
  }

  const environmentBin = process.platform === 'win32'
    ? join(projectRoot, '.venv', 'Scripts')
    : join(projectRoot, '.venv', 'bin');
  process.env.VIRTUAL_ENV = join(projectRoot, '.venv');
  process.env.PATH = `${environmentBin}${delimiter}${process.env.PATH ?? ''}`;

  const result = spawnSync(
    executable,
    [
      '-c',
      'import platform, build123d, lib3mf, numpy, PIL, rtree, trimesh; print(platform.python_version())',
    ],
    { encoding: 'utf8' },
  );
  if (result.status !== 0) {
    return { executable, ready: false, version: null };
  }
  return {
    executable,
    ready: true,
    version: result.stdout.trim() || null,
  };
}
