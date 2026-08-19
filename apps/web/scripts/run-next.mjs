import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseEnv } from 'node:util';

const DEFAULT_WEB_PORT = '6160';
const command = process.argv[2];
const appRoot = resolve(import.meta.dirname, '..');

if (command !== 'dev' && command !== 'start') {
  throw new Error('Expected the Next.js command to be either dev or start.');
}

function readConfiguredPort() {
  if (process.env.PORT !== undefined && process.env.PORT.trim() !== '') {
    return process.env.PORT.trim();
  }

  const nodeEnv = command === 'dev' ? 'development' : 'production';
  const envFiles = [
    `.env.${nodeEnv}.local`,
    '.env.local',
    `.env.${nodeEnv}`,
    '.env',
  ];

  for (const envFile of envFiles) {
    const envPath = resolve(appRoot, envFile);
    if (!existsSync(envPath)) continue;

    const port = parseEnv(readFileSync(envPath, 'utf8')).PORT;
    if (port !== undefined && port.trim() !== '') return port.trim();
  }

  return DEFAULT_WEB_PORT;
}

const port = readConfiguredPort();
const nextCli = resolve(appRoot, 'node_modules/next/dist/bin/next');
const child = spawn(
  process.execPath,
  [nextCli, command, '--hostname', '127.0.0.1', '--port', port],
  {
    cwd: appRoot,
    env: { ...process.env, PORT: port },
    stdio: 'inherit',
  },
);

child.on('error', (error) => {
  console.error(error);
  process.exitCode = 1;
});

child.on('exit', (code, signal) => {
  if (signal !== null) {
    process.kill(process.pid, signal);
    return;
  }

  process.exitCode = code ?? 1;
});
