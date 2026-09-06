import { createHash } from 'node:crypto';
import { constants, type Stats } from 'node:fs';
import {
  access,
  lstat,
  mkdir,
  mkdtemp,
  open,
  realpath,
  rename,
  rm,
} from 'node:fs/promises';
import {
  basename,
  dirname,
  extname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from 'node:path';

import { defineTool } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';

import { runPythonJsonProcess } from './python-json-process.ts';

export const REFERENCE_ANALYZE_TOOL_NAME = 'reference_analyze';
export const REFERENCE_ANALYZE_RESULT_SCHEMA =
  'evidence-reference-analysis-tool-result/v1';
const ANALYSIS_SCHEMA = 'evidence-reference-analysis/v1';
const SHA256 = /^[0-9a-f]{64}$/u;
const MAX_IMAGE_BYTES = 64 * 1024 * 1024;
const MAX_REPORT_BYTES = 4 * 1024 * 1024;

const parameters = Type.Object({
  image: Type.String({
    description:
      'Exact saved reference-image path inside this session workspace or upload directory. Relative paths resolve inside the workspace.',
    minLength: 1,
  }),
  report: Type.String({
    description: 'JSON report path relative to the current session workspace.',
    minLength: 1,
  }),
  expected_sha256: Type.String({
    description: 'SHA-256 supplied with the saved reference image.',
    pattern: '^[0-9a-f]{64}$',
  }),
});

export interface ReferenceAnalyzeResult {
  analysis: Record<string, unknown>;
  artifacts: { report: { path: string; sha256: string } };
  pass: true;
  schema: typeof REFERENCE_ANALYZE_RESULT_SCHEMA;
  source: { path: string; sha256: string };
}

function inside(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return (
    path === '' ||
    (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path))
  );
}

function digest(data: Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

interface StableFileSnapshot {
  bytes: Buffer;
  changedAtMs: number;
  device: number;
  inode: number;
  modifiedAtMs: number;
  sha256: string;
  size: number;
}

function sameFileState(
  left: Stats,
  right: Stats,
): boolean {
  return (
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.size === right.size &&
    left.mtimeMs === right.mtimeMs &&
    left.ctimeMs === right.ctimeMs
  );
}

async function stableFileSnapshot(
  path: string,
  maxBytes: number,
  label: string,
): Promise<StableFileSnapshot> {
  const handle = await open(
    path,
    process.platform === 'win32'
      ? 'r'
      : constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  try {
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      !Number.isSafeInteger(before.size) ||
      before.size <= 0 ||
      before.size > maxBytes
    ) {
      throw new Error(
        `${label} must be a non-empty, single-linked regular file no larger than ${Math.floor(maxBytes / (1024 * 1024))} MiB.`,
      );
    }
    const bytes = Buffer.allocUnsafe(before.size);
    let offset = 0;
    while (offset < bytes.length) {
      const result = await handle.read(
        bytes,
        offset,
        bytes.length - offset,
        offset,
      );
      if (result.bytesRead === 0) break;
      offset += result.bytesRead;
    }
    const overflowProbe = Buffer.allocUnsafe(1);
    const overflow = await handle.read(overflowProbe, 0, 1, before.size);
    const after = await handle.stat();
    if (
      offset !== before.size ||
      overflow.bytesRead !== 0 ||
      !sameFileState(before, after)
    ) {
      throw new Error(`${label} changed while it was being read.`);
    }
    return {
      bytes,
      changedAtMs: after.ctimeMs,
      device: after.dev,
      inode: after.ino,
      modifiedAtMs: after.mtimeMs,
      sha256: digest(bytes),
      size: after.size,
    };
  } finally {
    await handle.close();
  }
}

async function stableImageSnapshot(path: string): Promise<StableFileSnapshot> {
  return await stableFileSnapshot(path, MAX_IMAGE_BYTES, 'reference image');
}

interface ReportSnapshot extends StableFileSnapshot {
  value: unknown;
}

async function stableReportSnapshot(path: string): Promise<ReportSnapshot> {
  const snapshot = await stableFileSnapshot(
    path,
    MAX_REPORT_BYTES,
    'reference analyzer report',
  );
  let value: unknown;
  try {
    value = JSON.parse(snapshot.bytes.toString('utf8'));
  } catch {
    throw new Error('reference analyzer report must contain valid JSON.');
  }
  return { ...snapshot, value };
}

function sameImageSnapshot(
  left: StableFileSnapshot,
  right: StableFileSnapshot,
): boolean {
  return (
    left.device === right.device &&
    left.inode === right.inode &&
    left.size === right.size &&
    left.modifiedAtMs === right.modifiedAtMs &&
    left.changedAtMs === right.changedAtMs &&
    left.sha256 === right.sha256
  );
}

async function assertNormalPath(
  root: string,
  candidate: string,
  label: string,
): Promise<void> {
  const path = relative(root, candidate);
  let cursor = root;
  for (const part of path.split(sep).filter(Boolean)) {
    cursor = resolve(cursor, part);
    const entry = await lstat(cursor);
    if (entry.isSymbolicLink()) {
      throw new Error(`${label} may not pass through a symbolic link.`);
    }
    if (entry.isFile() && entry.nlink > 1) {
      throw new Error(`${label} may not use a hard-linked file.`);
    }
  }
}

async function ensureSafeDirectory(root: string, candidate: string): Promise<void> {
  const path = relative(root, candidate);
  let cursor = root;
  for (const part of path.split(sep).filter(Boolean)) {
    cursor = resolve(cursor, part);
    try {
      const entry = await lstat(cursor);
      if (entry.isSymbolicLink() || !entry.isDirectory()) {
        throw new Error('report parent must contain only regular directories.');
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      try {
        await mkdir(cursor);
      } catch (mkdirError) {
        if ((mkdirError as NodeJS.ErrnoException).code !== 'EEXIST') {
          throw mkdirError;
        }
      }
      const created = await lstat(cursor);
      if (created.isSymbolicLink() || !created.isDirectory()) {
        throw new Error('report parent must contain only regular directories.');
      }
    }
  }
}

async function resolveImage(
  workspaceRoot: string,
  uploadRoot: string,
  value: string,
): Promise<string> {
  if (value.includes('\0')) throw new Error('image path contains a null byte.');
  const candidate = resolve(
    isAbsolute(value) ? value : resolve(workspaceRoot, value),
  );
  const canonical = await realpath(candidate);
  const roots = [
    {
      canonical: await realpath(workspaceRoot),
      configured: resolve(workspaceRoot),
    },
  ];
  try {
    roots.push({
      canonical: await realpath(uploadRoot),
      configured: resolve(uploadRoot),
    });
  } catch {
    // A session without uploads still accepts workspace-local reference files.
  }
  const owner = roots.find((root) => inside(root.canonical, canonical));
  if (!owner) {
    throw new Error(
      'image must stay inside this session workspace or upload directory.',
    );
  }
  const inspectionRoot = inside(owner.configured, candidate)
    ? owner.configured
    : owner.canonical;
  await assertNormalPath(inspectionRoot, candidate, 'image');
  return canonical;
}

async function resolveReport(
  workspaceRoot: string,
  value: string,
): Promise<string> {
  if (
    !value.trim() ||
    value.includes('\0') ||
    isAbsolute(value) ||
    value.split(/[\\/]+/u).includes('..')
  ) {
    throw new Error('report must be a relative path inside the session workspace.');
  }
  if (extname(value).toLowerCase() !== '.json') {
    throw new Error('report must use a .json filename.');
  }
  const root = await realpath(workspaceRoot);
  const candidate = resolve(root, value);
  if (!inside(root, candidate)) {
    throw new Error('report escaped the session workspace.');
  }
  await ensureSafeDirectory(root, dirname(candidate));
  const parent = await realpath(dirname(candidate));
  if (!inside(root, parent)) {
    throw new Error('report parent escaped the session workspace.');
  }
  try {
    await assertNormalPath(root, candidate, 'report');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  return candidate;
}

async function pathsIdentifySameFile(
  left: string,
  right: string,
): Promise<boolean> {
  if (left === right) return true;
  try {
    return (await realpath(left)) === (await realpath(right));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}

async function replaceFile(source: string, destination: string): Promise<void> {
  await rename(source, destination);
}

function validateAnalysis(
  value: unknown,
  imagePath: string,
  imageSha256: string,
): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('reference analyzer report must contain an object.');
  }
  const report = value as Record<string, unknown>;
  const source = report.source as Record<string, unknown> | undefined;
  const image = report.image as Record<string, unknown> | undefined;
  if (
    report.schema !== ANALYSIS_SCHEMA ||
    source?.path !== imagePath ||
    source.sha256 !== imageSha256 ||
    image?.sha256 !== imageSha256
  ) {
    throw new Error(
      'reference analyzer report is not bound to the exact source image.',
    );
  }
  return report;
}

export function createReferenceAnalyzeTool(
  projectRoot: string,
  workspaceRoot: string,
  uploadRoot: string,
) {
  const activeReports = new Set<string>();
  const project = resolve(projectRoot);
  const python =
    process.platform === 'win32'
      ? join(project, '.venv', 'Scripts', 'python.exe')
      : join(project, '.venv', 'bin', 'python');
  const script = join(project, 'skills', 'text-a3d', 'reference_analyze.py');

  return defineTool({
    name: REFERENCE_ANALYZE_TOOL_NAME,
    label: 'Analyze CAD Reference',
    description:
      'Analyze one exact saved reference image through the deterministic text-a3d analyzer and atomically publish a hash-bound evidence report. Use once per uploaded reference before the build it informs.',
    promptSnippet: 'Analyze an uploaded CAD reference into current, hash-bound evidence',
    promptGuidelines: [
      'Call reference_analyze directly; do not invoke reference_analyze.py through bash.',
      'Pass the exact saved image path and SHA-256 supplied by the session.',
      'Analyze every uploaded reference before the cad_compile build that uses it.',
    ],
    parameters,
    executionMode: 'parallel',
    async execute(_toolCallId, params, signal) {
      if (!SHA256.test(params.expected_sha256)) {
        throw new Error('expected_sha256 must be a lowercase SHA-256 digest.');
      }
      await Promise.all([
        access(python, constants.X_OK),
        access(script, constants.R_OK),
      ]);
      const imagePath = await resolveImage(
        workspaceRoot,
        uploadRoot,
        params.image,
      );
      const initialImage = await stableImageSnapshot(imagePath);
      const imageSha256 = initialImage.sha256;
      if (imageSha256 !== params.expected_sha256) {
        throw new Error('reference image SHA-256 does not match expected_sha256.');
      }
      const reportPath = await resolveReport(workspaceRoot, params.report);
      if (await pathsIdentifySameFile(reportPath, imagePath)) {
        throw new Error('report path must not overwrite the reference image.');
      }
      if (activeReports.has(reportPath)) {
        throw new Error('reference report is already being generated.');
      }
      activeReports.add(reportPath);
      let temporaryDirectory: string | undefined;
      try {
        temporaryDirectory = await mkdtemp(
          join(dirname(reportPath), '.reference-analyze-'),
        );
        const temporaryReport = join(temporaryDirectory, basename(reportPath));
        const processResult = await runPythonJsonProcess(
          python,
          [script, imagePath, '--out', temporaryReport],
          {
            cwd: workspaceRoot,
            maxBufferBytes: MAX_REPORT_BYTES,
            signal,
            timeoutMs: 30_000,
          },
        );
        if (processResult.exitCode !== 0) {
          throw new Error(
            `reference analysis failed: ${processResult.stderr || processResult.stdout}`,
          );
        }
        const temporarySnapshot = await stableReportSnapshot(temporaryReport);
        const analysis = validateAnalysis(
          temporarySnapshot.value,
          imagePath,
          imageSha256,
        );
        const finalImage = await stableImageSnapshot(imagePath);
        if (!sameImageSnapshot(initialImage, finalImage)) {
          throw new Error('reference image changed during analysis.');
        }
        const reportSha256 = temporarySnapshot.sha256;
        await replaceFile(temporaryReport, reportPath);
        const publishedSnapshot = await stableReportSnapshot(reportPath);
        validateAnalysis(publishedSnapshot.value, imagePath, imageSha256);
        if (publishedSnapshot.sha256 !== reportSha256) {
          throw new Error('reference report changed during publication.');
        }
        const result: ReferenceAnalyzeResult = {
          analysis,
          artifacts: {
            report: { path: reportPath, sha256: reportSha256 },
          },
          pass: true,
          schema: REFERENCE_ANALYZE_RESULT_SCHEMA,
          source: { path: imagePath, sha256: imageSha256 },
        };
        return {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          details: result,
        };
      } finally {
        try {
          if (temporaryDirectory) {
            await rm(temporaryDirectory, { force: true, recursive: true });
          }
        } finally {
          activeReports.delete(reportPath);
        }
      }
    },
  });
}
