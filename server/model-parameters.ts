import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises';
import { basename, dirname, join, resolve, sep } from 'node:path';

import type {
  ArtifactSummary,
  ModelParameter,
  ParameterModel,
} from '../src/types.ts';
import { scanArtifacts } from './artifacts.ts';
import { discoverModelBuilds, type ModelBuild } from './model-builds.ts';
import type { ParameterBuildRequest } from './trpc/schemas.ts';

const PARAMETER_SOURCE_SCRIPT = resolve(
  import.meta.dirname,
  'parameter_source.py',
);
const MAX_PROCESS_OUTPUT_BYTES = 2 * 1024 * 1024;
const PARAMETER_BUILD_TIMEOUT_MS = 180_000;

interface ParameterSourceResponse {
  error?: string;
  ok: boolean;
  parameters?: ModelParameter[];
  source?: string;
}

export class ParameterBuildError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ParameterBuildError';
  }
}

function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

function safeTopLevelPath(path: string, label: string): string {
  if (path !== basename(path) || dirname(path) !== '.') {
    throw new ParameterBuildError(
      `${label} must be a top-level session artifact.`,
      400,
    );
  }
  return path;
}

async function runJsonProcess(
  executable: string,
  args: string[],
  input: unknown,
  options: {
    cwd?: string;
    env?: NodeJS.ProcessEnv;
    timeoutMs?: number;
  } = {},
): Promise<string> {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(executable, args, {
      cwd: options.cwd,
      env: options.env ?? process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let outputBytes = 0;
    let settled = false;
    const finish = (error?: Error, output?: string) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) rejectPromise(error);
      else resolvePromise(output ?? '');
    };
    const append = (target: 'stderr' | 'stdout', chunk: Buffer) => {
      outputBytes += chunk.byteLength;
      if (outputBytes > MAX_PROCESS_OUTPUT_BYTES) {
        child.kill('SIGKILL');
        finish(new Error('CAD parameter process produced too much output.'));
        return;
      }
      if (target === 'stdout') stdout += chunk.toString('utf8');
      else stderr += chunk.toString('utf8');
    };
    child.stdout.on('data', (chunk: Buffer) => append('stdout', chunk));
    child.stderr.on('data', (chunk: Buffer) => append('stderr', chunk));
    child.once('error', (error) => finish(error));
    child.once('close', (code, signal) => {
      if (code === 0) finish(undefined, stdout);
      else {
        const detail = (stderr || stdout).trim().slice(-4_000);
        finish(
          new Error(
            detail ||
              `CAD parameter process exited with ${signal ?? String(code)}.`,
          ),
        );
      }
    });
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(new Error('CAD parameter build timed out.'));
    }, options.timeoutMs ?? 30_000);
    child.stdin.end(JSON.stringify(input));
  });
}

async function parameterSource(
  pythonExecutable: string,
  input: Record<string, unknown>,
): Promise<ParameterSourceResponse> {
  let parsed: ParameterSourceResponse;
  try {
    parsed = JSON.parse(
      await runJsonProcess(pythonExecutable, [PARAMETER_SOURCE_SCRIPT], input),
    ) as ParameterSourceResponse;
  } catch (error) {
    throw new ParameterBuildError(
      error instanceof Error ? error.message : 'Unable to inspect CAD source.',
      422,
    );
  }
  if (!parsed.ok) {
    throw new ParameterBuildError(
      parsed.error || 'Unable to inspect CAD source.',
      422,
    );
  }
  return parsed;
}

async function inspectSource(
  pythonExecutable: string,
  source: string,
): Promise<ModelParameter[]> {
  const response = await parameterSource(pythonExecutable, {
    operation: 'inspect',
    source,
  });
  if (!Array.isArray(response.parameters)) {
    throw new ParameterBuildError(
      'CAD parameter inspector returned no parameter list.',
      422,
    );
  }
  return response.parameters;
}

async function rewriteSource(
  pythonExecutable: string,
  source: string,
  values: Record<string, number>,
): Promise<string> {
  const response = await parameterSource(pythonExecutable, {
    operation: 'rewrite',
    source,
    values,
  });
  if (typeof response.source !== 'string') {
    throw new ParameterBuildError(
      'CAD parameter rewriter returned no source.',
      422,
    );
  }
  return response.source;
}

export async function parameterModelsForWorkspace(
  workspaceRoot: string,
  pythonExecutable: string,
  artifacts?: readonly ArtifactSummary[],
): Promise<ParameterModel[]> {
  const availableArtifacts = artifacts ?? (await scanArtifacts(workspaceRoot));
  const builds = await discoverModelBuilds(workspaceRoot, availableArtifacts);
  return Promise.all(
    builds.map(async (build) => {
      const source = await readFile(resolve(workspaceRoot, build.sourcePath), 'utf8');
      try {
        return {
          ...build,
          parameters: await inspectSource(pythonExecutable, source),
          sourceHash: sha256(source),
        };
      } catch (error) {
        return {
          ...build,
          parameterError:
            error instanceof Error
              ? error.message
              : 'Unable to inspect model parameters.',
          parameters: [],
          sourceHash: sha256(source),
        };
      }
    }),
  );
}

async function requireCandidateFiles(
  outDir: string,
  build: ModelBuild,
): Promise<string[]> {
  const paths = [...new Set([...build.artifactPaths, build.reportPath])];
  for (const path of paths) {
    safeTopLevelPath(path, 'Generated artifact');
    try {
      const metadata = await stat(join(outDir, path));
      if (!metadata.isFile()) throw new Error('not a file');
    } catch {
      throw new ParameterBuildError(
        `Parameter build did not regenerate ${path}.`,
        422,
      );
    }
  }
  if (!paths.includes(build.primaryPreviewPath)) {
    throw new ParameterBuildError(
      'Parameter build report does not own the selected print root.',
      409,
    );
  }
  return paths;
}

function rebaseReportPaths(
  value: unknown,
  outDir: string,
  workspaceRoot: string,
): unknown {
  if (typeof value === 'string') {
    const prefix = `${outDir}${sep}`;
    return value.startsWith(prefix)
      ? join(workspaceRoot, value.slice(prefix.length))
      : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => rebaseReportPaths(item, outDir, workspaceRoot));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        rebaseReportPaths(item, outDir, workspaceRoot),
      ]),
    );
  }
  return value;
}

async function prepareCandidateReport(
  reportPath: string,
  outDir: string,
  workspaceRoot: string,
  sourcePath: string,
  sourceHash: string,
  values: Record<string, number>,
): Promise<void> {
  const raw = JSON.parse(await readFile(reportPath, 'utf8')) as Record<
    string,
    unknown
  >;
  const report = rebaseReportPaths(raw, outDir, workspaceRoot) as Record<
    string,
    unknown
  >;
  report.source = {
    path: join(workspaceRoot, sourcePath),
    sha256: sourceHash,
  };
  if (report.parameters && typeof report.parameters === 'object') {
    const parameters = report.parameters as Record<
      string,
      Record<string, unknown>
    >;
    for (const [id, parameterValue] of Object.entries(values)) {
      if (parameters[id]) {
        parameters[id].default = parameterValue;
        parameters[id].value = parameterValue;
      }
    }
  }
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

async function promoteFiles(
  files: Array<{ from: string; to: string }>,
  backupDir: string,
): Promise<void> {
  await mkdir(backupDir, { recursive: true });
  const backups: Array<{ from: string; to: string }> = [];
  const installed: string[] = [];
  try {
    for (const [index, file] of files.entries()) {
      try {
        await stat(file.to);
      } catch {
        continue;
      }
      const backup = join(backupDir, `${String(index)}-${basename(file.to)}`);
      await rename(file.to, backup);
      backups.push({ from: backup, to: file.to });
    }
    for (const file of files) {
      await rename(file.from, file.to);
      installed.push(file.to);
    }
  } catch (error) {
    await Promise.all(installed.map((path) => rm(path, { force: true })));
    for (const backup of [...backups].reverse()) {
      await rename(backup.from, backup.to).catch(() => undefined);
    }
    throw error;
  }
}

async function executeParameterBuild(
  pythonExecutable: string,
  sourcePath: string,
  workspaceRoot: string,
  outDir: string,
  values: Record<string, number>,
): Promise<void> {
  try {
    await runJsonProcess(
      pythonExecutable,
      [sourcePath],
      {},
      {
        cwd: workspaceRoot,
        env: {
          ...process.env,
          AMAGINE3D_OUTPUT_DIR: outDir,
          AMAGINE3D_PARAMETER_OVERRIDES: JSON.stringify(values),
          MPLBACKEND: 'Agg',
          PYTHONDONTWRITEBYTECODE: '1',
        },
        timeoutMs: PARAMETER_BUILD_TIMEOUT_MS,
      },
    );
  } catch (error) {
    throw new ParameterBuildError(
      error instanceof Error ? error.message : 'Parameter build failed.',
      422,
    );
  }
}

export async function rebuildModelWithParameters(options: {
  pythonExecutable: string;
  request: ParameterBuildRequest;
  workspaceRoot: string;
}): Promise<void> {
  const { pythonExecutable, request, workspaceRoot } = options;
  safeTopLevelPath(request.sourcePath, 'Model source');
  safeTopLevelPath(request.primaryPreviewPath, 'Top-level print root');
  const artifacts = await scanArtifacts(workspaceRoot);
  const build = (await discoverModelBuilds(workspaceRoot, artifacts)).find(
    (candidate) =>
      candidate.sourcePath === request.sourcePath &&
      candidate.primaryPreviewPath === request.primaryPreviewPath,
  );
  if (!build) {
    throw new ParameterBuildError(
      'The selected artifact is not a recognized top-level print root.',
      409,
    );
  }
  build.artifactPaths.forEach((path) => safeTopLevelPath(path, 'Model artifact'));
  safeTopLevelPath(build.reportPath, 'Build report');
  const sourcePath = join(workspaceRoot, request.sourcePath);
  const source = await readFile(sourcePath, 'utf8');
  if (sha256(source) !== request.sourceHash) {
    throw new ParameterBuildError(
      'Model source changed; reload parameters before rebuilding.',
      409,
    );
  }
  const rewrittenSource = await rewriteSource(
    pythonExecutable,
    source,
    request.values,
  );
  const rewrittenSourceHash = sha256(rewrittenSource);
  const buildsRoot = join(
    workspaceRoot,
    '.amagine-state',
    'parameter-builds',
  );
  await mkdir(buildsRoot, { recursive: true });
  const jobRoot = await mkdtemp(join(buildsRoot, 'job-'));
  const outDir = join(jobRoot, 'out');
  const backupDir = join(jobRoot, 'backup');
  const stagedSourcePath = join(jobRoot, basename(request.sourcePath));
  await mkdir(outDir, { recursive: true });
  try {
    await executeParameterBuild(
      pythonExecutable,
      sourcePath,
      workspaceRoot,
      outDir,
      request.values,
    );
    const candidatePaths = await requireCandidateFiles(outDir, build);
    const candidateReportPath = join(outDir, build.reportPath);
    await prepareCandidateReport(
      candidateReportPath,
      outDir,
      workspaceRoot,
      request.sourcePath,
      rewrittenSourceHash,
      request.values,
    );
    await writeFile(stagedSourcePath, rewrittenSource, 'utf8');
    await promoteFiles(
      [
        ...candidatePaths.map((path) => ({
          from: join(outDir, path),
          to: join(workspaceRoot, path),
        })),
        { from: stagedSourcePath, to: sourcePath },
      ],
      backupDir,
    );
  } finally {
    await rm(jobRoot, { force: true, recursive: true });
  }
}
