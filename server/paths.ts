import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export interface ServerPaths {
  bundledPomodoroRoot: string;
  distPath: string;
  projectRoot: string;
  sessionRoot: string;
  workspaceRoot: string;
}

export function serverPaths(): ServerPaths {
  const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  return {
    bundledPomodoroRoot: join(
      projectRoot,
      'bundled-projects',
      'amagine3d-pomodoro',
    ),
    distPath: join(projectRoot, 'dist'),
    projectRoot,
    sessionRoot: join(projectRoot, '.amagine-state', 'sessions'),
    workspaceRoot: join(projectRoot, 'workspace'),
  };
}
