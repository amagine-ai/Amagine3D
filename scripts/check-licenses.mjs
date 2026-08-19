import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { readProductionLicenseInventory } from './lib/npm-licenses.mjs';
import { createCheck, listFiles, repositoryRoot } from './lib/repository.mjs';

const check = createCheck('LICENSES');
const requiredFiles = [
  'LICENSE',
  'NOTICE',
  'third_party/NOTICES.md',
  'third_party/README.md',
  'third_party/npm-production-licenses.json',
  'third_party/npm-production-notices.txt',
];

for (const path of requiredFiles) {
  if (!existsSync(resolve(repositoryRoot, path))) {
    check.fail(`Required license file is missing: ${path}`);
  }
}

const mirroredFiles = [
  ...listFiles('third_party/licenses').map((path) => ({
    source: `third_party/licenses/${path}`,
    publicCopy: `apps/web/public/licenses/${path}`,
  })),
  {
    source: 'third_party/npm-production-licenses.json',
    publicCopy: 'apps/web/public/licenses/npm-production-licenses.json',
  },
  {
    source: 'third_party/npm-production-notices.txt',
    publicCopy: 'apps/web/public/licenses/npm-production-notices.txt',
  },
  {
    source: 'NOTICE',
    publicCopy: 'apps/web/public/licenses/amagine3d-notice.txt',
  },
  {
    source: 'LICENSE',
    publicCopy: 'apps/web/public/licenses/apache-2.0.txt',
  },
];

for (const { source, publicCopy } of mirroredFiles) {
  const sourcePath = resolve(repositoryRoot, source);
  const publicPath = resolve(repositoryRoot, publicCopy);
  if (!existsSync(sourcePath)) continue;
  if (!existsSync(publicPath)) {
    check.fail(`Public application license copy is missing: ${publicCopy}`);
  } else if (!readFileSync(sourcePath).equals(readFileSync(publicPath))) {
    check.fail(`Public application license copy differs: ${publicCopy}`);
  }
}

const reviewedLicenseExpressions = new Set([
  '0BSD',
  '(AFL-2.1 OR BSD-3-Clause)',
  'Apache-2.0',
  'BSD-3-Clause',
  'CC-BY-4.0',
  'ISC',
  'LGPL-3.0-or-later',
  'MIT',
  'MPL-2.0',
  'OFL-1.1',
]);
const prohibitedLicensePattern = /(?:^|\W)(?:AGPL|GPL)(?:\W|$)/iu;

try {
  const { groupedReport, packages } = readProductionLicenseInventory();
  const licenseExpressions = Object.keys(groupedReport);
  for (const license of licenseExpressions) {
    if (/unknown|unlicensed|undefined/iu.test(license)) {
      check.fail(`Unknown production dependency license: ${license}`);
    } else if (prohibitedLicensePattern.test(license)) {
      check.fail(`Prohibited production dependency license: ${license}`);
    } else if (!reviewedLicenseExpressions.has(license)) {
      check.fail(
        `Unreviewed production dependency license: ${license}. Review it before adding it to the allowlist.`,
      );
    }
  }
  console.log(
    `Production dependency policy: ${packages.length} packages under ${licenseExpressions.length} reviewed license expressions.`,
  );
} catch (error) {
  check.fail(
    `Unable to inspect production dependency licenses: ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
}

check.finish(
  'License check passed: repository copies match and dependency licenses are reviewed.',
);
