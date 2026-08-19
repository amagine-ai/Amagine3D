import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { basename, resolve } from 'node:path';

import {
  publicPackageRecord,
  readProductionLicenseInventory,
} from './lib/npm-licenses.mjs';
import { repositoryRoot } from './lib/repository.mjs';

const { packages } = readProductionLicenseInventory();
const publicPackages = packages.map(publicPackageRecord);
const report = {
  schemaVersion: 2,
  source:
    'pnpm list --prod plus installed package manifests for the release target',
  packages: publicPackages,
};
const reportText = `${JSON.stringify(report, null, 2)}\n`;
const noticeFilePattern = /^(?:licen[cs]e|copying|notice)(?:[._-].*)?$/iu;

function findNoticeFiles(packagePath) {
  if (!existsSync(packagePath)) return [];
  return readdirSync(packagePath)
    .filter((name) => noticeFilePattern.test(name))
    .map((name) => resolve(packagePath, name))
    .filter((path) => statSync(path).isFile())
    .sort();
}

function normalizeNoticeText(contents) {
  return contents
    .replaceAll('\r\n', '\n')
    .replaceAll('\r', '\n')
    .split('\n')
    .map((line) => line.trimEnd())
    .join('\n')
    .trim();
}

const noticeSections = [
  'Amagine3D — Production npm Dependency Notices',
  '================================================',
  '',
  'Generated from the installed production dependency graph.',
  'Regenerate this file for each release target with `pnpm licenses:generate`.',
  '',
];

for (const packageRecord of packages) {
  noticeSections.push(
    `${packageRecord.name}@${packageRecord.version}`,
    `License expression: ${packageRecord.license}`,
  );
  if (packageRecord.homepage) {
    noticeSections.push(`Homepage: ${packageRecord.homepage}`);
  }

  const noticeFiles = [
    ...new Set(
      packageRecord.paths.flatMap((packagePath) =>
        findNoticeFiles(packagePath),
      ),
    ),
  ];
  if (noticeFiles.length === 0) {
    noticeSections.push('License text: not found in the installed package.');
  } else {
    for (const noticePath of noticeFiles) {
      noticeSections.push(
        '',
        `--- ${basename(noticePath)} ---`,
        normalizeNoticeText(readFileSync(noticePath, 'utf8')),
      );
    }
  }
  noticeSections.push(
    '',
    '------------------------------------------------',
    '',
  );
}

const noticesText = `${noticeSections.join('\n').trimEnd()}\n`;
const outputs = [
  ['third_party/npm-production-licenses.json', reportText],
  ['apps/web/public/licenses/npm-production-licenses.json', reportText],
  ['third_party/npm-production-notices.txt', noticesText],
  ['apps/web/public/licenses/npm-production-notices.txt', noticesText],
  [
    'apps/web/public/licenses/amagine3d-notice.txt',
    readFileSync(resolve(repositoryRoot, 'NOTICE'), 'utf8'),
  ],
  [
    'apps/web/public/licenses/apache-2.0.txt',
    readFileSync(resolve(repositoryRoot, 'LICENSE'), 'utf8'),
  ],
];

for (const [relativePath, contents] of outputs) {
  writeFileSync(resolve(repositoryRoot, relativePath), contents);
}

console.log(
  `Wrote ${publicPackages.length} production dependency records and synchronized application copies.`,
);
