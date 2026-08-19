import { resolve } from 'node:path';

import { build } from 'esbuild';

const appRoot = resolve(import.meta.dirname, '..');
const repositoryRoot = resolve(appRoot, '..', '..');

await build({
  entryPoints: [
    resolve(repositoryRoot, 'packages/cad-execution-browser/src/cad-worker.ts'),
  ],
  outfile: resolve(appRoot, 'public/cad-worker.mjs'),
  bundle: true,
  conditions: ['browser', 'worker', 'import'],
  define: {
    'process.env.NODE_ENV': JSON.stringify(
      process.env.NODE_ENV ?? 'development',
    ),
  },
  external: ['node:*', 'ws'],
  format: 'esm',
  legalComments: 'none',
  logLevel: 'info',
  mainFields: ['browser', 'module', 'main'],
  minify: process.env.NODE_ENV === 'production',
  platform: 'browser',
  sourcemap: false,
  target: ['es2024'],
});
