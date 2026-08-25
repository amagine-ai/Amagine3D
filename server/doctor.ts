import 'dotenv/config';

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PiRuntime } from '@amagine3d/a3d-runtime';
import { activateProjectPython } from './python-runtime.ts';

const serverDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(serverDirectory, '..');
const nodeVersion = process.versions.node;
const [nodeMajor, nodeMinor] = nodeVersion.split('.').map(Number);
const nodeReady = nodeMajor > 20 || (nodeMajor === 20 && nodeMinor >= 19);
const python = activateProjectPython(projectRoot);

const checks: Array<{ detail: string; name: string; ready: boolean }> = [
  {
    detail: `Node ${nodeVersion} (requires >= 20.19)`,
    name: 'Node',
    ready: nodeReady,
  },
  {
    detail: python.ready
      ? `Python ${python.version} from .venv`
      : 'not ready; run npm run python:setup',
    name: 'Python CAD runtime',
    ready: python.ready,
  },
  {
    detail: process.env.LLM_API_KEY?.trim() ? 'configured' : 'not configured',
    name: 'LLM_API_KEY',
    ready: Boolean(process.env.LLM_API_KEY?.trim()),
  },
];

try {
  const runtime = await PiRuntime.create(projectRoot);
  checks.push({
    detail: `${runtime.modelName}; ${runtime.skills.length} skills`,
    name: 'Amagine3D Agent runtime',
    ready: runtime.runtimeReady && runtime.skillDiagnostics.length === 0,
  });
  for (const diagnostic of runtime.skillDiagnostics) {
    console.warn(`Skill warning: ${diagnostic}`);
  }
} catch (error) {
  checks.push({
    detail: error instanceof Error ? error.message : String(error),
    name: 'Amagine3D Agent runtime',
    ready: false,
  });
}

for (const check of checks) {
  console.log(`${check.ready ? '✓' : '✗'} ${check.name}: ${check.detail}`);
}

if (checks.some((check) => !check.ready)) process.exitCode = 1;
