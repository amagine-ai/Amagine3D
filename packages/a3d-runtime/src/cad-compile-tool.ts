import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { constants, type BigIntStats } from 'node:fs';
import {
  access,
  lstat,
  mkdir,
  open,
  readFile,
  realpath,
  rename,
  stat,
  unlink,
  writeFile,
} from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';

import {
  defineTool,
  type AgentToolUpdateCallback,
  type InlineExtension,
} from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';

import { terminateProcessTree } from './python-json-process.ts';

export const CAD_COMPILE_TOOL_NAME = 'cad_compile';

const RESULT_SCHEMA = 'evidence-cad-compile-result/v1';
const INTENT_SESSION_SCHEMA = 'evidence-cad-intent-session/v1';
export const CAD_INTENT_STATE_DIRECTORY = '.intent-sessions';
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const LOG_NAME = 'cad_compile.log';
const RESULT_NAME = 'cad_compile-result.json';
const DEFAULT_LOG_POLL_INTERVAL_MS = 250;
const DEFAULT_TERMINATE_GRACE_MS = 500;
export const CAD_COMPILE_AGGREGATE_TIMEOUT_MS = 5_400_000;
// Python's bounded Windows cleanup can consume 12 seconds (taskkill + two
// waits); reserve additional time for compact-result and log publication.
export const CAD_COMPILE_DEADLINE_SETTLEMENT_GRACE_MS = 30_000;
export const CAD_COMPILE_HARD_TIMEOUT_MS =
  CAD_COMPILE_AGGREGATE_TIMEOUT_MS +
  CAD_COMPILE_DEADLINE_SETTLEMENT_GRACE_MS;
const MAX_CAPTURE_BYTES = 1_000_000;
const MAX_LOG_READ_BYTES = 64_000;
const MAX_LOG_UPDATE_CHARS = 4_000;

const cadCompileParameters = Type.Object({
  intent: Type.String({
    description:
      'Pre-existing immutable intent-contract JSON path, relative to the current session workspace. Author and validate it separately before the build source; the source may not create or modify it.',
    minLength: 1,
  }),
  marker: Type.String({
    description:
      'Generation-start marker path, relative to the current session workspace. It must already exist and predate the intent and source.',
    minLength: 1,
  }),
  scene: Type.String({
    description:
      'Semantic-scene JSON path, relative to the current session workspace. The source may generate this file.',
    minLength: 1,
  }),
  source: Type.String({
    description:
      'Agent-authored Python CAD build-source path, relative to the current session workspace. It may generate the scene but must not write the intent.',
    minLength: 1,
  }),
  output_dir: Type.String({
    description:
      'Output directory path, relative to the current session workspace. Use "." for the workspace root.',
    minLength: 1,
  }),
});

export interface CadCompileArtifactReference {
  path: string;
  sha256: string;
}

export interface CadCompileIssue {
  code?: string;
  message?: string;
  severity?: string;
  stage?: string;
  [key: string]: unknown;
}

export interface CadCompileResult {
  artifacts: Record<string, CadCompileArtifactReference>;
  issues: CadCompileIssue[];
  pass: boolean;
  runId: string;
  schema: typeof RESULT_SCHEMA;
  status: string;
  [key: string]: unknown;
}

interface CadCompileProgress {
  log: {
    bytesRead: number;
    path: string;
  };
  status: 'running';
}

type CadCompileToolDetails = CadCompileProgress | CadCompileResult;

export interface CadCompileToolTuning {
  /** Test-only latency tuning; production callers should use the default. */
  hardTimeoutMs?: number;
  /** Test-only latency tuning; production callers should use the default. */
  logPollIntervalMs?: number;
  /** Test-only latency tuning; production callers should use the default. */
  terminateGraceMs?: number;
}

export interface CadCompileToolOptions {
  intentScopeId: string;
  intentStatePath: string;
  projectRoot: string;
  tuning?: CadCompileToolTuning;
  workspaceRoot: string;
}

interface FrozenIntentRevision {
  path: string;
  sha256: string;
}

interface IntentSessionState {
  activeRevision: number;
  activeScopeId: string;
  revisions: FrozenIntentRevision[];
  schema: typeof INTENT_SESSION_SCHEMA;
}

interface IntentCandidate extends FrozenIntentRevision {
  revision: number;
  state: IntentSessionState | undefined;
}

interface CapturedProcessResult {
  code: number | null;
  signal: NodeJS.Signals | null;
  stderr: string;
  stdout: string;
}

export function isCadCompileResult(value: unknown): value is CadCompileResult {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false;
  }
  const result = value as Record<string, unknown>;
  return (
    result.schema === RESULT_SCHEMA &&
    typeof result.pass === 'boolean' &&
    typeof result.runId === 'string' &&
    UUID.test(result.runId) &&
    typeof result.status === 'string' &&
    Array.isArray(result.issues) &&
    typeof result.artifacts === 'object' &&
    result.artifacts !== null &&
    !Array.isArray(result.artifacts)
  );
}

/** Mark semantic compile failures as PI tool errors without discarding details. */
export function createCadCompileResultExtension(): InlineExtension {
  return {
    factory(pi) {
      pi.on('tool_result', (event) => {
        if (
          event.toolName === CAD_COMPILE_TOOL_NAME &&
          !event.isError &&
          isCadCompileResult(event.details) &&
          !event.details.pass
        ) {
          return {
            details: event.details,
            isError: true,
            usage: event.usage,
          };
        }
        return undefined;
      });
    },
    hidden: true,
    name: 'cad-compile-result-status',
  };
}

function isInside(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return (
    path === '' ||
    (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path))
  );
}

function infrastructureError(
  code: string,
  message: string,
  extra: Record<string, unknown> = {},
): Error {
  return new Error(
    JSON.stringify({
      code,
      message,
      schema: 'evidence-cad-compile-tool-error/v1',
      ...extra,
    }),
  );
}

export function cadIntentStatePath(
  sessionRoot: string,
  sessionId: string,
): string {
  if (!UUID.test(sessionId)) throw new Error('Invalid session id.');
  return join(sessionRoot, CAD_INTENT_STATE_DIRECTORY, `${sessionId}.json`);
}

function relativeIntentPath(workspaceRoot: string, intentPath: string): string {
  return relative(workspaceRoot, intentPath).split(sep).join('/');
}

function isFrozenIntentRevision(value: unknown): value is FrozenIntentRevision {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const revision = value as Partial<FrozenIntentRevision>;
  return (
    typeof revision.path === 'string' &&
    revision.path.length > 0 &&
    !isAbsolute(revision.path) &&
    !revision.path.split('/').includes('..') &&
    typeof revision.sha256 === 'string' &&
    /^[0-9a-f]{64}$/u.test(revision.sha256)
  );
}

function isIntentSessionState(value: unknown): value is IntentSessionState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const state = value as Partial<IntentSessionState>;
  return (
    state.schema === INTENT_SESSION_SCHEMA &&
    typeof state.activeScopeId === 'string' &&
    UUID.test(state.activeScopeId) &&
    Number.isInteger(state.activeRevision) &&
    Number(state.activeRevision) >= 0 &&
    Array.isArray(state.revisions) &&
    state.revisions.length > Number(state.activeRevision) &&
    state.revisions.every(isFrozenIntentRevision)
  );
}

async function readIntentSessionState(
  statePath: string,
): Promise<IntentSessionState | undefined> {
  let entry;
  try {
    entry = await lstat(statePath);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
    throw infrastructureError(
      'TOOL.INTENT_STATE_INVALID',
      `intent session state is unavailable: ${(error as Error).message}`,
    );
  }
  if (entry.isSymbolicLink() || !entry.isFile() || entry.nlink !== 1) {
    throw infrastructureError(
      'TOOL.INTENT_STATE_INVALID',
      'intent session state must be a single-linked regular file',
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(statePath, 'utf8'));
  } catch (error) {
    throw infrastructureError(
      'TOOL.INTENT_STATE_INVALID',
      `intent session state is not valid JSON: ${(error as Error).message}`,
    );
  }
  if (!isIntentSessionState(parsed)) {
    throw infrastructureError(
      'TOOL.INTENT_STATE_INVALID',
      `intent session state must use ${INTENT_SESSION_SCHEMA}`,
    );
  }
  return parsed;
}

async function stableFileHash(
  path: string,
  label: string,
  signal: AbortSignal | undefined,
): Promise<string> {
  const entry = await lstat(path, { bigint: true });
  if (!entry.isFile() || entry.nlink !== 1n) {
    throw infrastructureError(
      'TOOL.PATH_INVALID',
      `${label} must be a single-linked regular file`,
    );
  }
  return await hashStableArtifact(path, label, entry, signal);
}

function frozenIntentError(
  expected: FrozenIntentRevision,
  observed: FrozenIntentRevision,
): Error {
  return infrastructureError(
    'TOOL.INTENT_FROZEN',
    'the accepted intent is frozen for this user turn',
    {
      expected,
      observed,
      repairHint:
        'Restore the accepted intent exactly. A user-requested target change must be authored under a new intent filename in a later user turn.',
    },
  );
}

async function resolveIntentCandidate(
  workspaceRoot: string,
  intentPath: string,
  statePath: string,
  scopeId: string,
  signal: AbortSignal | undefined,
): Promise<IntentCandidate> {
  const observed = {
    path: relativeIntentPath(workspaceRoot, intentPath),
    sha256: await stableFileHash(intentPath, 'intent', signal),
  };
  const state = await readIntentSessionState(statePath);
  if (!state) return { ...observed, revision: 0, state };

  const active = state.revisions[state.activeRevision]!;
  if (state.activeScopeId === scopeId) {
    if (active.path !== observed.path || active.sha256 !== observed.sha256) {
      throw frozenIntentError(active, observed);
    }
    return { ...observed, revision: state.activeRevision, state };
  }

  const reusedRevision = state.revisions.findIndex(
    (revision) => revision.path === observed.path,
  );
  if (reusedRevision >= 0) {
    const reused = state.revisions[reusedRevision]!;
    if (reused.sha256 !== observed.sha256) {
      throw infrastructureError(
        'TOOL.INTENT_PATH_REUSED',
        'an earlier immutable intent filename was reused with different content',
        {
          expected: reused,
          observed,
          repairHint:
            'Keep prior intent files unchanged and write a new intent filename for a user-requested target revision.',
        },
      );
    }
    return { ...observed, revision: reusedRevision, state };
  }
  return { ...observed, revision: state.revisions.length, state };
}

function sameIntentState(
  left: IntentSessionState | undefined,
  right: IntentSessionState | undefined,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function writeIntentSessionState(
  statePath: string,
  state: IntentSessionState,
): Promise<void> {
  const parent = dirname(statePath);
  await mkdir(parent, { recursive: true });
  const temporary = `${statePath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, {
      flag: 'wx',
      mode: 0o600,
    });
    await rename(temporary, statePath);
  } catch (error) {
    throw infrastructureError(
      'TOOL.INTENT_STATE_WRITE_FAILED',
      `could not persist the immutable intent binding: ${(error as Error).message}`,
    );
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}

async function commitIntentCandidate(
  statePath: string,
  scopeId: string,
  candidate: IntentCandidate,
): Promise<void> {
  const current = await readIntentSessionState(statePath);
  if (!sameIntentState(current, candidate.state)) {
    throw infrastructureError(
      'TOOL.INTENT_STATE_CHANGED',
      'intent session state changed during compilation',
    );
  }
  const revisions = [...(current?.revisions ?? [])];
  if (candidate.revision === revisions.length) {
    revisions.push({ path: candidate.path, sha256: candidate.sha256 });
  } else {
    const existing = revisions[candidate.revision];
    if (
      !existing ||
      existing.path !== candidate.path ||
      existing.sha256 !== candidate.sha256
    ) {
      throw infrastructureError(
        'TOOL.INTENT_STATE_CHANGED',
        'intent revision changed during compilation',
      );
    }
  }
  await writeIntentSessionState(statePath, {
    activeRevision: candidate.revision,
    activeScopeId: scopeId,
    revisions,
    schema: INTENT_SESSION_SCHEMA,
  });
}

async function intentValidationPassed(
  result: CadCompileResult,
): Promise<boolean> {
  const reference = result.artifacts.intentValidation;
  if (!reference) return false;
  let bytes: Buffer;
  try {
    bytes = await readFile(reference.path);
  } catch (error) {
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      `intent validation artifact is unavailable: ${(error as Error).message}`,
    );
  }
  if (createHash('sha256').update(bytes).digest('hex') !== reference.sha256) {
    throw infrastructureError(
      'TOOL.RESULT_HASH_MISMATCH',
      'intent validation artifact changed before intent binding',
    );
  }
  let audit: { errors?: unknown; pass?: unknown; schema?: unknown };
  try {
    audit = JSON.parse(bytes.toString('utf8')) as typeof audit;
  } catch (error) {
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      `intent validation artifact is not valid JSON: ${(error as Error).message}`,
    );
  }
  if (
    audit.schema !== 'intent-validation/v5' ||
    typeof audit.pass !== 'boolean' ||
    !Array.isArray(audit.errors) ||
    audit.pass !== (audit.errors.length === 0)
  ) {
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      'intent validation artifact has an invalid contract',
    );
  }
  return audit.pass;
}

function assertRelativeParameter(label: string, value: string): void {
  if (!value.trim()) {
    throw infrastructureError('TOOL.PATH_INVALID', `${label} must not be empty`);
  }
  if (value.includes('\0') || isAbsolute(value)) {
    throw infrastructureError(
      'TOOL.PATH_INVALID',
      `${label} must be a relative path inside the session workspace`,
    );
  }
  if (value.split(/[\\/]+/u).includes('..')) {
    throw infrastructureError(
      'TOOL.PATH_TRAVERSAL',
      `${label} may not contain parent-directory traversal`,
    );
  }
}

async function assertPathEntriesAreSafe(
  workspaceRoot: string,
  candidate: string,
  label: string,
): Promise<void> {
  const path = relative(workspaceRoot, candidate);
  let cursor = workspaceRoot;
  for (const part of path.split(sep).filter(Boolean)) {
    cursor = resolve(cursor, part);
    try {
      const entry = await lstat(cursor);
      if (entry.isSymbolicLink()) {
        throw infrastructureError(
          'TOOL.SYMLINK_REJECTED',
          `${label} may not pass through a symbolic link`,
        );
      }
      if (entry.isFile() && entry.nlink > 1) {
        throw infrastructureError(
          'TOOL.HARDLINK_REJECTED',
          `${label} may not use a hard-linked file`,
        );
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
  }
}

async function truncateSafeCompileLog(path: string): Promise<void> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    try {
      const existing = await lstat(path);
      if (
        existing.isSymbolicLink() ||
        !existing.isFile() ||
        existing.nlink !== 1
      ) {
        throw infrastructureError(
          'TOOL.LOG_UNSAFE',
          'cad_compile log must be a single-linked regular file',
        );
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }

    handle = await open(
      path,
      constants.O_RDWR |
        constants.O_CREAT |
        (constants.O_NOFOLLOW ?? 0) |
        (constants.O_NONBLOCK ?? 0),
      0o600,
    );
    const opened = await handle.stat();
    const current = await lstat(path);
    if (
      !opened.isFile() ||
      opened.nlink !== 1 ||
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      opened.dev !== current.dev ||
      opened.ino !== current.ino
    ) {
      throw infrastructureError(
        'TOOL.LOG_UNSAFE',
        'cad_compile log stopped being a single-linked regular file',
      );
    }
    await handle.truncate(0);
    const truncated = await handle.stat();
    const published = await lstat(path);
    if (
      !truncated.isFile() ||
      truncated.nlink !== 1 ||
      truncated.size !== 0 ||
      published.isSymbolicLink() ||
      !published.isFile() ||
      published.nlink !== 1 ||
      truncated.dev !== published.dev ||
      truncated.ino !== published.ino
    ) {
      throw infrastructureError(
        'TOOL.LOG_UNSAFE',
        'cad_compile log changed while it was prepared',
      );
    }
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('{')) throw error;
    throw infrastructureError(
      'TOOL.LOG_UNSAFE',
      `cad_compile log could not be prepared safely: ${(error as Error).message}`,
    );
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function resolveWorkspaceParameter(
  workspaceRoot: string,
  label: string,
  value: string,
  options: {
    missingRepairHint?: string;
    mustExist: boolean;
    type?: 'directory' | 'file';
  },
): Promise<string> {
  assertRelativeParameter(label, value);
  const candidate = resolve(workspaceRoot, value);
  if (!isInside(workspaceRoot, candidate)) {
    throw infrastructureError(
      'TOOL.PATH_TRAVERSAL',
      `${label} must stay inside the session workspace`,
    );
  }
  await assertPathEntriesAreSafe(workspaceRoot, candidate, label);
  try {
    const entry = await lstat(candidate);
    if (options.type === 'file' && !entry.isFile()) {
      throw infrastructureError(
        'TOOL.PATH_INVALID',
        `${label} must be a regular file`,
      );
    }
    if (options.type === 'directory' && !entry.isDirectory()) {
      throw infrastructureError(
        'TOOL.PATH_INVALID',
        `${label} must be a directory`,
      );
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    if (options.mustExist) {
      throw infrastructureError(
        'TOOL.PATH_MISSING',
        `${label} does not exist inside the session workspace`,
        options.missingRepairHint
          ? { repairHint: options.missingRepairHint }
          : {},
      );
    }
  }
  return candidate;
}

async function canonicalWorkspaceRoot(workspaceRoot: string): Promise<string> {
  const root = resolve(workspaceRoot);
  let entry;
  try {
    entry = await lstat(root);
  } catch (error) {
    throw infrastructureError(
      'TOOL.WORKSPACE_INVALID',
      `session workspace is unavailable: ${(error as Error).message}`,
    );
  }
  if (!entry.isDirectory() || entry.isSymbolicLink()) {
    throw infrastructureError(
      'TOOL.WORKSPACE_INVALID',
      'session workspace must be a real directory, not a symbolic link',
    );
  }
  const canonical = await realpath(root);
  return canonical;
}

async function assertMarkerPredatesInputs(
  marker: string,
  inputs: Array<{ label: string; path: string }>,
): Promise<void> {
  const markerTime = (await stat(marker, { bigint: true })).mtimeNs;
  for (const input of inputs) {
    const inputTime = (await stat(input.path, { bigint: true })).mtimeNs;
    if (markerTime > inputTime) {
      throw infrastructureError(
        'TOOL.MARKER_TOO_NEW',
        `generation marker must be created before ${input.label}`,
      );
    }
  }
}

function appendCaptured(
  current: Buffer,
  chunk: Buffer,
): { buffer: Buffer; overflow: boolean } {
  const combined = Buffer.concat([current, chunk]);
  if (combined.byteLength <= MAX_CAPTURE_BYTES) {
    return { buffer: combined, overflow: false };
  }
  return {
    buffer: combined.subarray(combined.byteLength - MAX_CAPTURE_BYTES),
    overflow: true,
  };
}

class CompileLogTailer {
  private bytesRead = 0;
  private interval: NodeJS.Timeout | undefined;
  private polling: Promise<void> = Promise.resolve();

  constructor(
    private readonly logPath: string,
    private readonly displayPath: string,
    private readonly pollIntervalMs: number,
    private readonly onUpdate:
      | AgentToolUpdateCallback<CadCompileToolDetails>
      | undefined,
  ) {}

  start(): void {
    if (!this.onUpdate) return;
    this.interval = setInterval(() => this.queuePoll(), this.pollIntervalMs);
    this.interval.unref();
  }

  private queuePoll(): void {
    this.polling = this.polling.then(() => this.poll()).catch(() => undefined);
  }

  private async poll(): Promise<void> {
    if (!this.onUpdate) return;
    let size: number;
    try {
      const entry = await lstat(this.logPath);
      if (entry.isSymbolicLink() || !entry.isFile() || entry.nlink > 1) {
        throw infrastructureError(
          'TOOL.LOG_UNSAFE',
          'cad_compile log stopped being a safe regular file',
        );
      }
      size = entry.size;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
    if (size < this.bytesRead) this.bytesRead = 0;
    if (size === this.bytesRead) return;

    const unreadBytes = size - this.bytesRead;
    const bytesToRead = Math.min(unreadBytes, MAX_LOG_READ_BYTES);
    const position = size - bytesToRead;
    const buffer = Buffer.allocUnsafe(bytesToRead);
    const file = await open(
      this.logPath,
      process.platform === 'win32'
        ? 'r'
        : constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    try {
      const { bytesRead } = await file.read(buffer, 0, bytesToRead, position);
      if (bytesRead === 0) return;
      this.bytesRead = size;
      const chunk = buffer
        .subarray(0, bytesRead)
        .toString('utf8')
        .slice(-MAX_LOG_UPDATE_CHARS);
      this.onUpdate({
        content: [
          {
            type: 'text',
            text: `cad_compile log update (${this.displayPath}):\n${chunk}`,
          },
        ],
        details: {
          log: { bytesRead: this.bytesRead, path: this.displayPath },
          status: 'running',
        },
      });
    } finally {
      await file.close();
    }
  }

  async stop(): Promise<void> {
    if (this.interval) clearInterval(this.interval);
    await this.polling;
    await this.poll();
  }
}

async function runCompilerProcess(options: {
  argv: string[];
  cwd: string;
  hardTimeoutMs: number;
  logPath: string;
  logDisplayPath: string;
  onUpdate: AgentToolUpdateCallback<CadCompileToolDetails> | undefined;
  pollIntervalMs: number;
  pythonExecutable: string;
  signal: AbortSignal | undefined;
  terminateGraceMs: number;
}): Promise<CapturedProcessResult> {
  if (options.signal?.aborted) {
    throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
  }
  if (!Number.isFinite(options.hardTimeoutMs) || options.hardTimeoutMs <= 0) {
    throw infrastructureError(
      'TOOL.CONFIG_INVALID',
      'cad_compile hard timeout must be a positive number',
    );
  }

  const tailer = new CompileLogTailer(
    options.logPath,
    options.logDisplayPath,
    options.pollIntervalMs,
    options.onUpdate,
  );
  let stdout: Buffer = Buffer.alloc(0);
  let stderr: Buffer = Buffer.alloc(0);
  let outputOverflow = false;
  let childClosed = false;
  let terminationReason: 'aborted' | 'output-limit' | 'timeout' | undefined;
  let terminationPromise: Promise<void> | undefined;
  let watchdog: NodeJS.Timeout | undefined;

  const child = spawn(options.pythonExecutable, options.argv, {
    cwd: options.cwd,
    detached: process.platform !== 'win32',
    env: { ...process.env, PYTHONNOUSERSITE: '1' },
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  tailer.start();

  const terminate = (
    reason: 'aborted' | 'output-limit' | 'timeout',
  ): Promise<void> => {
    if (childClosed || terminationReason) {
      return terminationPromise ?? Promise.resolve();
    }
    terminationReason = reason;
    terminationPromise = terminateProcessTree(
      child,
      options.terminateGraceMs,
    );
    return terminationPromise;
  };
  const abort = () => void terminate('aborted');
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  watchdog = setTimeout(
    () => void terminate('timeout'),
    options.hardTimeoutMs,
  );

  child.stdout?.on('data', (chunk: Buffer) => {
    const captured = appendCaptured(stdout, chunk);
    stdout = captured.buffer;
    outputOverflow ||= captured.overflow;
    if (captured.overflow) void terminate('output-limit');
  });
  child.stderr?.on('data', (chunk: Buffer) => {
    const captured = appendCaptured(stderr, chunk);
    stderr = captured.buffer;
    outputOverflow ||= captured.overflow;
    if (captured.overflow) void terminate('output-limit');
  });

  try {
    const completed = await new Promise<{
      code: number | null;
      signal: NodeJS.Signals | null;
    }>((resolvePromise, rejectPromise) => {
      child.once('error', rejectPromise);
      child.once('close', (code, childSignal) => {
        childClosed = true;
        if (watchdog) clearTimeout(watchdog);
        options.signal?.removeEventListener('abort', abort);
        resolvePromise({ code, signal: childSignal });
      });
    });
    await terminationPromise;
    await tailer.stop();
    if (terminationReason === 'aborted') {
      throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
    }
    if (terminationReason === 'timeout') {
      throw infrastructureError(
        'TOOL.TIMEOUT',
        `cad_compile exceeded its ${options.hardTimeoutMs} ms hard timeout`,
      );
    }
    if (terminationReason === 'output-limit' || outputOverflow) {
      throw infrastructureError(
        'TOOL.OUTPUT_TOO_LARGE',
        'cad_compile produced too much direct process output',
      );
    }
    return {
      ...completed,
      stderr: stderr.toString('utf8'),
      stdout: stdout.toString('utf8'),
    };
  } catch (error) {
    if (!childClosed) {
      terminationPromise ??= terminateProcessTree(
        child,
        options.terminateGraceMs,
      );
      await terminationPromise;
    }
    await tailer.stop();
    if (error instanceof Error && error.message.startsWith('{')) throw error;
    throw infrastructureError(
      'TOOL.SPAWN_FAILED',
      `cad_compile could not run: ${(error as Error).message}`,
    );
  } finally {
    if (watchdog) clearTimeout(watchdog);
    options.signal?.removeEventListener('abort', abort);
  }
}

function parseCompileResult(
  stdout: string,
  processResult: Pick<CapturedProcessResult, 'code' | 'signal' | 'stderr'>,
): CadCompileResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      'cad_compile did not emit valid JSON',
      {
        exitCode: processResult.code,
        processSignal: processResult.signal,
        stderr: processResult.stderr.slice(-4_000),
      },
    );
  }
  if (!isCadCompileResult(parsed)) {
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      'cad_compile emitted an invalid evidence-cad-compile-result/v1 payload',
      { exitCode: processResult.code },
    );
  }
  return parsed as CadCompileResult;
}

async function validateReturnedArtifacts(
  workspaceRoot: string,
  result: CadCompileResult,
  signal: AbortSignal | undefined,
): Promise<void> {
  for (const [name, rawReference] of Object.entries(result.artifacts)) {
    if (signal?.aborted) {
      throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
    }
    if (
      typeof rawReference !== 'object' ||
      rawReference === null ||
      Array.isArray(rawReference)
    ) {
      throw infrastructureError(
        'TOOL.RESULT_INVALID',
        `cad_compile artifact ${name} is not a reference object`,
      );
    }
    const artifactPath = rawReference.path;
    if (typeof artifactPath !== 'string' || !isAbsolute(artifactPath)) {
      throw infrastructureError(
        'TOOL.RESULT_INVALID',
        `cad_compile artifact ${name} does not contain an absolute path`,
      );
    }
    const candidate = resolve(artifactPath);
    if (!isInside(workspaceRoot, candidate)) {
      throw infrastructureError(
        'TOOL.RESULT_OUTSIDE_WORKSPACE',
        `cad_compile artifact ${name} escaped the session workspace`,
      );
    }
    await assertPathEntriesAreSafe(workspaceRoot, candidate, `artifact ${name}`);
    const entry = await lstat(candidate, { bigint: true }).catch((error: NodeJS.ErrnoException) => {
      throw infrastructureError(
        'TOOL.RESULT_INVALID',
        `cad_compile artifact ${name} is unavailable: ${error.message}`,
      );
    });
    if (!entry.isFile() || entry.nlink !== 1n) {
      throw infrastructureError(
        'TOOL.RESULT_INVALID',
        `cad_compile artifact ${name} is not a single-linked regular file`,
      );
    }
    if (
      typeof rawReference.sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/u.test(rawReference.sha256)
    ) {
      throw infrastructureError(
        'TOOL.RESULT_INVALID',
        `cad_compile artifact ${name} does not contain a valid SHA-256`,
      );
    }
    const observedHash = await hashStableArtifact(candidate, name, entry, signal);
    if (observedHash !== rawReference.sha256) {
      throw infrastructureError(
        'TOOL.RESULT_HASH_MISMATCH',
        `cad_compile artifact ${name} changed before result validation`,
      );
    }
  }
}

function artifactSnapshotMatches(
  left: BigIntStats,
  right: BigIntStats,
): boolean {
  return (
    left.isFile() &&
    right.isFile() &&
    left.nlink === 1n &&
    right.nlink === 1n &&
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.size === right.size &&
    left.mtimeNs === right.mtimeNs &&
    left.ctimeNs === right.ctimeNs
  );
}

async function hashStableArtifact(
  path: string,
  name: string,
  initialPathEntry: BigIntStats,
  signal: AbortSignal | undefined,
): Promise<string> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      path,
      constants.O_RDONLY |
        (constants.O_NOFOLLOW ?? 0) |
        (constants.O_NONBLOCK ?? 0),
    );
    const before = await handle.stat({ bigint: true });
    if (!artifactSnapshotMatches(initialPathEntry, before)) {
      throw infrastructureError(
        'TOOL.RESULT_CHANGED',
        `cad_compile artifact ${name} changed before result validation`,
      );
    }
    if (signal?.aborted) {
      throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
    }

    const hash = createHash('sha256');
    const stream = handle.createReadStream({
      autoClose: false,
      signal,
      start: 0,
    });
    try {
      for await (const chunk of stream) hash.update(chunk as Buffer);
    } catch (error) {
      stream.destroy();
      if (signal?.aborted || (error as Error).name === 'AbortError') {
        throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
      }
      throw error;
    }

    if (signal?.aborted) {
      throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
    }
    const after = await handle.stat({ bigint: true });
    const finalPathEntry = await lstat(path, { bigint: true });
    if (
      !artifactSnapshotMatches(before, after) ||
      !artifactSnapshotMatches(after, finalPathEntry)
    ) {
      throw infrastructureError(
        'TOOL.RESULT_CHANGED',
        `cad_compile artifact ${name} changed during result validation`,
      );
    }
    return hash.digest('hex');
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('{')) throw error;
    if (signal?.aborted || (error as Error).name === 'AbortError') {
      throw infrastructureError('TOOL.ABORTED', 'cad_compile was cancelled');
    }
    throw infrastructureError(
      'TOOL.RESULT_INVALID',
      `cad_compile artifact ${name} could not be hashed: ${(error as Error).message}`,
    );
  } finally {
    await handle?.close();
  }
}

/**
 * Create the sequential, session-scoped CAD compiler tool used by PI.
 *
 * The compiler's `pass: false` result is intentionally returned instead of
 * thrown so the model retains its structured issues and artifact references.
 * PI's AgentToolResult does not expose `isError`; register the companion
 * createCadCompileResultExtension() to mark `pass: false` after execution while
 * preserving the result content and details.
 */
export function createCadCompileTool(
  options: CadCompileToolOptions,
) {
  if (!UUID.test(options.intentScopeId)) {
    throw new Error('intentScopeId must be a UUID.');
  }
  const resolvedProjectRoot = resolve(options.projectRoot);
  const resolvedIntentStatePath = resolve(options.intentStatePath);
  const tuning = options.tuning ?? {};
  const pythonExecutable =
    process.platform === 'win32'
      ? join(resolvedProjectRoot, '.venv', 'Scripts', 'python.exe')
      : join(resolvedProjectRoot, '.venv', 'bin', 'python');
  const compilerScript = join(
    resolvedProjectRoot,
    'skills',
    'text-a3d',
    'cad_compile.py',
  );

  return defineTool<typeof cadCompileParameters, CadCompileToolDetails>({
    name: CAD_COMPILE_TOOL_NAME,
    label: 'Compile CAD',
    description:
      'Compile one canonical text-a3d intent, semantic scene, and Python source; run all applicable CAD, manufacturing, package, and render audits; then return compact structured diagnostics and fresh artifact references.',
    promptSnippet:
      'Compile and audit the current text-a3d generation marker, intent, scene, and source',
    promptGuidelines: [
      'Use cad_compile instead of manually chaining text-a3d compiler and QA scripts.',
      'Before calling cad_compile, run a separate contract-only authoring step that creates and validates the immutable intent. Never put write_intent in the CAD build source or run the full build source manually to bootstrap intent; the build source may generate the scene inside cad_compile.',
      'The first valid intent used in this user turn is hash-bound by the runtime. Preserve that exact file and content throughout repair iterations. A later user-requested target change must use a new intent filename rather than rewriting an earlier contract.',
      'A pass result still requires reading the returned fresh preview before delivery.',
      'Before calling cad_compile again, review the full issue set, identify shared root causes, and try to address related findings in one coordinated change. Use judgment when an issue should be deferred and briefly explain that choice. Use repairDelta and repairState to avoid regressing checks that already passed; blockedBy marks checks that require valid upstream evidence, not extra failures to guess around.',
    ],
    parameters: cadCompileParameters,
    executionMode: 'sequential',
    async execute(_toolCallId, params, signal, onUpdate) {
      const root = await canonicalWorkspaceRoot(options.workspaceRoot);
      if (isInside(root, resolvedIntentStatePath)) {
        throw infrastructureError(
          'TOOL.CONFIG_INVALID',
          'intent session state must be stored outside the Agent-writable workspace',
        );
      }
      await access(pythonExecutable, constants.X_OK).catch(
        (error: NodeJS.ErrnoException) => {
          throw infrastructureError(
            'TOOL.PYTHON_UNAVAILABLE',
            `project Python is unavailable; run npm run python:setup: ${error.message}`,
          );
        },
      );
      await access(compilerScript, constants.R_OK).catch(
        (error: NodeJS.ErrnoException) => {
          throw infrastructureError(
            'TOOL.COMPILER_UNAVAILABLE',
            `cad_compile.py is unavailable: ${error.message}`,
          );
        },
      );

      const intent = await resolveWorkspaceParameter(root, 'intent', params.intent, {
        missingRepairHint:
          'Create and validate the immutable intent in a separate contract-only authoring step, then call cad_compile again. Do not run the CAD build source to create intent; it may create the scene during compilation.',
        mustExist: true,
        type: 'file',
      });
      const marker = await resolveWorkspaceParameter(root, 'marker', params.marker, {
        mustExist: true,
        type: 'file',
      });
      const scene = await resolveWorkspaceParameter(root, 'scene', params.scene, {
        mustExist: false,
      });
      const source = await resolveWorkspaceParameter(root, 'source', params.source, {
        mustExist: true,
        type: 'file',
      });
      await assertMarkerPredatesInputs(marker, [
        { label: 'intent', path: intent },
        { label: 'source', path: source },
      ]);
      const intentCandidate = await resolveIntentCandidate(
        root,
        intent,
        resolvedIntentStatePath,
        options.intentScopeId,
        signal,
      );
      const outputDir = await resolveWorkspaceParameter(
        root,
        'output_dir',
        params.output_dir,
        { mustExist: false },
      );
      await mkdir(outputDir, { recursive: true });
      await resolveWorkspaceParameter(root, 'output_dir', params.output_dir, {
        mustExist: true,
        type: 'directory',
      });

      const logPath = join(outputDir, LOG_NAME);
      const resultPath = join(outputDir, RESULT_NAME);
      for (const [label, path] of [
        ['compile log', logPath],
        ['compile result', resultPath],
      ] as const) {
        await assertPathEntriesAreSafe(root, path, label);
      }
      await truncateSafeCompileLog(logPath);

      const processResult = await runCompilerProcess({
        argv: [
          compilerScript,
          scene,
          '--marker',
          marker,
          '--intent',
          intent,
          '--source',
          source,
          '--workspace',
          root,
          '--output-dir',
          outputDir,
          '--result',
          resultPath,
          '--log',
          logPath,
        ],
        cwd: root,
        hardTimeoutMs: tuning.hardTimeoutMs ?? CAD_COMPILE_HARD_TIMEOUT_MS,
        logDisplayPath: relative(root, logPath) || LOG_NAME,
        logPath,
        onUpdate,
        pollIntervalMs:
          tuning.logPollIntervalMs ?? DEFAULT_LOG_POLL_INTERVAL_MS,
        pythonExecutable,
        signal,
        terminateGraceMs:
          tuning.terminateGraceMs ?? DEFAULT_TERMINATE_GRACE_MS,
      });
      const result = parseCompileResult(processResult.stdout, processResult);
      await validateReturnedArtifacts(root, result, signal);

      if (result.pass && processResult.code !== 0) {
        throw infrastructureError(
          'TOOL.EXIT_MISMATCH',
          'cad_compile reported pass=true but exited unsuccessfully',
          {
            exitCode: processResult.code,
            processSignal: processResult.signal,
          },
        );
      }
      if (!result.pass && processResult.code === 0) {
        throw infrastructureError(
          'TOOL.EXIT_MISMATCH',
          'cad_compile reported pass=false but exited successfully',
          { exitCode: processResult.code },
        );
      }
      if (await intentValidationPassed(result)) {
        await commitIntentCandidate(
          resolvedIntentStatePath,
          options.intentScopeId,
          intentCandidate,
        );
      }

      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        details: result,
      };
    },
  });
}
