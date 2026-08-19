import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { createCheck, listFiles, repositoryRoot } from './lib/repository.mjs';

const check = createCheck('BROWSER SECURITY');

for (const sourcePath of listFiles('apps/web/src').filter((path) =>
  /\.[cm]?[jt]sx?$/u.test(path),
)) {
  const source = readFileSync(
    resolve(repositoryRoot, 'apps/web/src', sourcePath),
    'utf8',
  );
  if (
    /^['"]use client['"];?/u.test(source.trimStart()) &&
    /\bprocess\.env\b/u.test(source)
  ) {
    check.fail(
      `Client module reads server environment variables: ${sourcePath}`,
    );
  }
  if (/NEXT_PUBLIC_[A-Z0-9_]*(?:KEY|SECRET|TOKEN)/u.test(source)) {
    check.fail(
      `Browser-readable secret-like environment name found: ${sourcePath}`,
    );
  }
}

check.finish('Browser security check passed.');
