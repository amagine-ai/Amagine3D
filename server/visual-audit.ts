import { createHash } from 'node:crypto';
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
} from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import {
  extname,
  isAbsolute,
  relative,
  resolve,
} from 'node:path';

import {
  CAD_COMPILE_TOOL_NAME,
  REFERENCE_ANALYZE_RESULT_SCHEMA,
  REFERENCE_ANALYZE_TOOL_NAME,
} from '@amagine3d/a3d-runtime';

import { resolveArtifactPath } from './artifacts.ts';
import {
  BUILD_REPORT_SCHEMA,
  type UnifiedBuildReport,
  validateUnifiedBuildReport,
} from './build-report.ts';
import { isContainedRelativePath } from './path-safety.ts';

const REFERENCE_REPORT_SCHEMA = 'evidence-reference-analysis/v1';
const RENDER_REPORT_SCHEMA = 'evidence-render/v2';
const MAX_PREVIEW_SNAPSHOT_BYTES = 64 * 1024 * 1024;
const SHA256 = /^[0-9a-f]{64}$/u;
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
interface ToolCallBlock {
  arguments?: unknown;
  id?: unknown;
  name?: unknown;
  type?: unknown;
}

export interface VisualAuditResult {
  buildBound: boolean;
  pass: boolean;
  previewRead: boolean;
  referenceAnalyzed: boolean;
  renderCalled: boolean;
  renderReportValid: boolean;
}

export interface VisualAuditOptions {
  referenceImages?: readonly { path: string; sha256: string }[];
  requireReferenceAnalysis?: boolean;
  turnStartedAtMs: number;
  workspaceRoot: string;
}

export interface VisualRepairOptions {
  attempt: number;
  maxAttempts: number;
  requireReferenceAnalysis?: boolean;
}

export interface VisualFileSnapshot {
  modifiedAtMs: number;
  path: string;
  sha256: string;
  size: number;
}

export interface VisualAuditEntry {
  message: unknown;
  readSnapshot?: VisualFileSnapshot;
}

interface EvidenceFileSnapshot extends VisualFileSnapshot {
  bytes: Buffer;
}

function unchangedFile(
  before: {
    ctimeMs: number;
    dev: number;
    ino: number;
    mtimeMs: number;
    size: number;
  },
  after: {
    ctimeMs: number;
    dev: number;
    ino: number;
    mtimeMs: number;
    size: number;
  },
): boolean {
  return (
    before.dev === after.dev &&
    before.ino === after.ino &&
    before.size === after.size &&
    before.mtimeMs === after.mtimeMs &&
    before.ctimeMs === after.ctimeMs
  );
}

function readFileSnapshotSync(
  path: string,
  maxBytes: number,
): EvidenceFileSnapshot | undefined {
  let descriptor: number | undefined;
  try {
    descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = fstatSync(descriptor);
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      before.size <= 0 ||
      before.size > maxBytes
    ) {
      return undefined;
    }
    const bytes = readFileSync(descriptor);
    const after = fstatSync(descriptor);
    const current = lstatSync(path);
    if (
      bytes.length !== before.size ||
      !unchangedFile(before, after) ||
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      !unchangedFile(before, current)
    ) {
      return undefined;
    }
    return {
      bytes,
      modifiedAtMs: before.mtimeMs,
      path,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      size: before.size,
    };
  } catch {
    return undefined;
  } finally {
    if (descriptor !== undefined) {
      try {
        closeSync(descriptor);
      } catch {
        // Closing does not alter the already captured immutable byte snapshot.
      }
    }
  }
}

async function readFileSnapshot(
  path: string,
  maxBytes: number,
): Promise<EvidenceFileSnapshot | undefined> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      before.size <= 0 ||
      before.size > maxBytes
    ) {
      return undefined;
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    const current = await lstat(path);
    if (
      bytes.length !== before.size ||
      !unchangedFile(before, after) ||
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      !unchangedFile(before, current)
    ) {
      return undefined;
    }
    return {
      bytes,
      modifiedAtMs: before.mtimeMs,
      path,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      size: before.size,
    };
  } catch {
    return undefined;
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function readDigestSnapshot(
  path: string,
): Promise<VisualFileSnapshot | undefined> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      !Number.isSafeInteger(before.size)
    ) {
      return undefined;
    }
    const hash = createHash('sha256');
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let position = 0;
    while (position < before.size) {
      const { bytesRead } = await handle.read(
        buffer,
        0,
        Math.min(buffer.length, before.size - position),
        position,
      );
      if (bytesRead <= 0) return undefined;
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
    const after = await handle.stat();
    const current = await lstat(path);
    if (
      !unchangedFile(before, after) ||
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      !unchangedFile(before, current)
    ) {
      return undefined;
    }
    return {
      modifiedAtMs: before.mtimeMs,
      path,
      sha256: hash.digest('hex'),
      size: before.size,
    };
  } catch {
    return undefined;
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

function parseSnapshotJson<T>(
  snapshot: EvidenceFileSnapshot | undefined,
): T | undefined {
  if (!snapshot) return undefined;
  try {
    return JSON.parse(snapshot.bytes.toString('utf8')) as T;
  } catch {
    return undefined;
  }
}

function insideRoot(root: string, path: string): boolean {
  return isContainedRelativePath(relative(root, path));
}

function projectedAuditMessage(rawMessage: unknown): unknown {
  if (!rawMessage || typeof rawMessage !== 'object') return rawMessage;
  const message = rawMessage as {
    content?: unknown;
    details?: unknown;
    isError?: unknown;
    role?: unknown;
    toolCallId?: unknown;
    toolName?: unknown;
  };
  const projected: Record<string, unknown> = {
    isError: message.isError,
    role: message.role,
    toolCallId: message.toolCallId,
    toolName: message.toolName,
  };
  if (message.toolName === CAD_COMPILE_TOOL_NAME) {
    projected.details = projectedCadCompileDetails(message.details);
  }
  if (message.toolName === REFERENCE_ANALYZE_TOOL_NAME) {
    projected.details = projectedReferenceAnalyzeDetails(message.details);
  }
  if (Array.isArray(message.content)) {
    projected.content = message.content.flatMap((rawBlock) => {
      if (!rawBlock || typeof rawBlock !== 'object') return [];
      const block = rawBlock as ToolCallBlock & { type?: unknown };
      if (block.type === 'toolCall') {
        return [
          {
            arguments: block.arguments,
            id: block.id,
            name: block.name,
            type: 'toolCall',
          },
        ];
      }
      return block.type === 'image' ? [{ type: 'image' }] : [];
    });
  }
  return projected;
}

function projectedCadCompileDetails(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const artifacts =
    record.artifacts &&
    typeof record.artifacts === 'object' &&
    !Array.isArray(record.artifacts)
      ? (record.artifacts as Record<string, unknown>)
      : {};
  const renderEvidence = artifacts.renderEvidence;
  const buildReport = artifacts.buildReport;
  return {
    artifacts: {
      ...(buildReport &&
      typeof buildReport === 'object' &&
      !Array.isArray(buildReport)
        ? { buildReport }
        : {}),
      ...(renderEvidence &&
      typeof renderEvidence === 'object' &&
      !Array.isArray(renderEvidence)
        ? { renderEvidence }
        : {}),
    },
    pass: record.pass,
    runId: record.runId,
    schema: record.schema,
    status: record.status,
  };
}

function projectedReferenceAnalyzeDetails(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const artifacts =
    record.artifacts &&
    typeof record.artifacts === 'object' &&
    !Array.isArray(record.artifacts)
      ? (record.artifacts as Record<string, unknown>)
      : {};
  const report =
    artifacts.report &&
    typeof artifacts.report === 'object' &&
    !Array.isArray(artifacts.report)
      ? (artifacts.report as Record<string, unknown>)
      : {};
  const source =
    record.source &&
    typeof record.source === 'object' &&
    !Array.isArray(record.source)
      ? (record.source as Record<string, unknown>)
      : {};
  return {
    artifacts: {
      report: { path: report.path, sha256: report.sha256 },
    },
    pass: record.pass,
    schema: record.schema,
    source: { path: source.path, sha256: source.sha256 },
  };
}

/**
 * Capture read evidence at message_end time. The later audit must compare this
 * snapshot with the final preview, so replacing a PNG after the model read it
 * cannot relabel unseen bytes as reviewed evidence.
 */
export class CadVisualAuditTrail {
  readonly entries: VisualAuditEntry[] = [];

  private readonly pendingReadPaths = new Map<string, string>();
  private readonly workspaceRoot: string;

  constructor(workspaceRoot: string) {
    this.workspaceRoot = resolve(workspaceRoot);
  }

  record(rawMessage: unknown): void {
    const message = projectedAuditMessage(rawMessage);
    const entry: VisualAuditEntry = { message };
    if (message && typeof message === 'object') {
      const record = message as {
        content?: unknown;
        isError?: unknown;
        role?: unknown;
        toolCallId?: unknown;
        toolName?: unknown;
      };
      if (record.role === 'assistant' && Array.isArray(record.content)) {
        for (const rawBlock of record.content) {
          if (!rawBlock || typeof rawBlock !== 'object') continue;
          const call = rawBlock as ToolCallBlock;
          const path = previewReadPath(call);
          if (path && typeof call.id === 'string') {
            this.pendingReadPaths.set(call.id, path);
          }
        }
      } else if (
        record.role === 'toolResult' &&
        record.toolName === 'read' &&
        record.isError !== true &&
        typeof record.toolCallId === 'string' &&
        Array.isArray(record.content) &&
        record.content.some(
          (block) =>
            block &&
            typeof block === 'object' &&
            (block as { type?: unknown }).type === 'image',
        )
      ) {
        const requestedPath = this.pendingReadPaths.get(record.toolCallId);
        if (requestedPath) {
          try {
            const candidate = isAbsolute(requestedPath)
              ? requestedPath
              : resolve(this.workspaceRoot, requestedPath);
            const canonical = realpathSync(candidate);
            if (
              insideRoot(this.workspaceRoot, canonical) &&
              extname(canonical).toLowerCase() === '.png'
            ) {
              const snapshot = readFileSnapshotSync(
                canonical,
                MAX_PREVIEW_SNAPSHOT_BYTES,
              );
              if (snapshot) {
                const { bytes: _bytes, ...readSnapshot } = snapshot;
                entry.readSnapshot = readSnapshot;
              }
            }
          } catch {
            // Missing or escaped files deliberately produce no usable snapshot.
          }
        }
        this.pendingReadPaths.delete(record.toolCallId);
      }
    }
    this.entries.push(entry);
  }
}

function recordArguments(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {};
}

interface ReferenceAnalyzeCall {
  expectedSha256: string;
  imagePath: string;
  reportPath: string;
}

interface ReferenceAnalyzeEvidence extends ReferenceAnalyzeCall {
  artifactReportPath: string;
  artifactReportSha256: string;
  sourcePath: string;
  sourceSha256: string;
}

function referenceAnalyzeCall(
  call: ToolCallBlock,
): ReferenceAnalyzeCall | undefined {
  if (call.name !== REFERENCE_ANALYZE_TOOL_NAME) return undefined;
  const args = recordArguments(call.arguments);
  const imagePath = args.image;
  const reportPath = args.report;
  const expectedSha256 = args.expected_sha256;
  if (
    typeof imagePath !== 'string' ||
    !imagePath ||
    typeof reportPath !== 'string' ||
    !reportPath ||
    isAbsolute(reportPath) ||
    reportPath.split(/[\\/]+/u).includes('..') ||
    extname(reportPath).toLowerCase() !== '.json' ||
    typeof expectedSha256 !== 'string' ||
    !SHA256.test(expectedSha256)
  ) {
    return undefined;
  }
  return { expectedSha256, imagePath, reportPath };
}

function referenceAnalyzeEvidence(
  call: ReferenceAnalyzeCall,
  details: unknown,
): ReferenceAnalyzeEvidence | undefined {
  if (!details || typeof details !== 'object' || Array.isArray(details)) {
    return undefined;
  }
  const record = details as Record<string, unknown>;
  const artifacts =
    record.artifacts &&
    typeof record.artifacts === 'object' &&
    !Array.isArray(record.artifacts)
      ? (record.artifacts as Record<string, unknown>)
      : undefined;
  const report =
    artifacts?.report &&
    typeof artifacts.report === 'object' &&
    !Array.isArray(artifacts.report)
      ? (artifacts.report as Record<string, unknown>)
      : undefined;
  const source =
    record.source &&
    typeof record.source === 'object' &&
    !Array.isArray(record.source)
      ? (record.source as Record<string, unknown>)
      : undefined;
  if (
    record.schema !== REFERENCE_ANALYZE_RESULT_SCHEMA ||
    record.pass !== true ||
    typeof report?.path !== 'string' ||
    typeof report.sha256 !== 'string' ||
    !SHA256.test(report.sha256) ||
    typeof source?.path !== 'string' ||
    typeof source.sha256 !== 'string' ||
    !SHA256.test(source.sha256)
  ) {
    return undefined;
  }
  return {
    ...call,
    artifactReportPath: report.path,
    artifactReportSha256: report.sha256,
    sourcePath: source.path,
    sourceSha256: source.sha256,
  };
}

function isCadMutation(
  call: ToolCallBlock,
): boolean {
  if (call.name === CAD_COMPILE_TOOL_NAME) return true;
  if (call.name === 'edit' || call.name === 'write') return true;
  return call.name === 'bash';
}

function previewReadPath(call: ToolCallBlock): string | undefined {
  if (call.name !== 'read') return undefined;
  const path = recordArguments(call.arguments).path;
  return typeof path === 'string' && extname(path).toLowerCase() === '.png'
    ? path
    : undefined;
}

interface BuildEvidence {
  displayPath: string;
  displaySha256: string;
  reportModifiedAtMs: number;
}

interface EvidenceInspection {
  buildBound: boolean;
  buildReportModifiedAtMs?: number;
  previewRead: boolean;
  renderReportValid: boolean;
}

interface FileReference {
  path?: unknown;
  sha256?: unknown;
}

interface RawRenderReport {
  meshes?: FileReference[];
  preview?: FileReference;
  runId?: unknown;
  schema?: unknown;
}

interface RawReferenceReport {
  image?: { sha256?: unknown };
  schema?: unknown;
  source?: FileReference;
}

const MAX_EVIDENCE_REPORT_BYTES = 2 * 1024 * 1024;

async function passingBuild(
  workspaceRoot: string,
  reportReference: FileReference,
  runId: string,
  turnStartedAtMs: number,
): Promise<BuildEvidence | undefined> {
  if (
    typeof reportReference.path !== 'string' ||
    typeof reportReference.sha256 !== 'string' ||
    !SHA256.test(reportReference.sha256)
  ) {
    return undefined;
  }
  const reportPath = await resolveArtifactPath(
    workspaceRoot,
    reportReference.path,
  );
  if (!reportPath) return undefined;
  const reportSnapshot = await readFileSnapshot(
    reportPath,
    MAX_EVIDENCE_REPORT_BYTES,
  );
  if (reportSnapshot?.sha256 !== reportReference.sha256) return undefined;
  const candidate = parseSnapshotJson<UnifiedBuildReport>(reportSnapshot);
  if (candidate?.schema !== BUILD_REPORT_SCHEMA) return undefined;
  const validated = await validateUnifiedBuildReport(
    workspaceRoot,
    reportPath,
    {
      maxBytes: MAX_EVIDENCE_REPORT_BYTES,
      minimumArtifactModifiedAtMs: turnStartedAtMs,
      minimumModifiedAtMs: turnStartedAtMs,
      expectedReportSha256: reportReference.sha256,
    },
  );
  if (!validated || validated.report.runId !== runId) return undefined;
  const display = validated.report.artifacts?.['glb:display'];
  if (
    typeof display?.path !== 'string' ||
    typeof display.sha256 !== 'string' ||
    !SHA256.test(display.sha256)
  ) {
    return undefined;
  }
  const displayPath = validated.artifactPaths['glb:display'];
  if (!displayPath) return undefined;
  return {
    displayPath,
    displaySha256: display.sha256,
    reportModifiedAtMs: validated.reportModifiedAtMs,
  };
}

async function inspectRenderEvidence(
  reportReference: FileReference | undefined,
  buildReportReference: FileReference | undefined,
  runId: string | undefined,
  readSnapshots: readonly VisualFileSnapshot[],
  options: VisualAuditOptions,
): Promise<EvidenceInspection> {
  const failed: EvidenceInspection = {
    buildBound: false,
    previewRead: false,
    renderReportValid: false,
  };
  if (
    typeof reportReference?.path !== 'string' ||
    typeof reportReference.sha256 !== 'string' ||
    !SHA256.test(reportReference.sha256) ||
    !buildReportReference ||
    typeof runId !== 'string' ||
    !UUID.test(runId)
  ) {
    return failed;
  }
  const reportPath = await resolveArtifactPath(
    options.workspaceRoot,
    reportReference.path,
  );
  if (!reportPath) return failed;
  const reportSnapshot = await readFileSnapshot(
    reportPath,
    MAX_EVIDENCE_REPORT_BYTES,
  );
  if (
    !reportSnapshot ||
    reportSnapshot.modifiedAtMs < options.turnStartedAtMs ||
    reportSnapshot.sha256 !== reportReference.sha256
  ) {
    return failed;
  }
  const report = parseSnapshotJson<RawRenderReport>(reportSnapshot);
  if (
    report?.schema !== RENDER_REPORT_SCHEMA ||
    report.runId !== runId
  ) {
    return failed;
  }

  const preview = report.preview;
  if (
    typeof preview?.path !== 'string' ||
    typeof preview.sha256 !== 'string' ||
    !SHA256.test(preview.sha256) ||
    extname(preview.path).toLowerCase() !== '.png'
  ) {
    return failed;
  }
  const previewPath = await resolveArtifactPath(
    options.workspaceRoot,
    preview.path,
  );
  if (!previewPath) return failed;
  const previewSnapshot = await readFileSnapshot(
    previewPath,
    MAX_PREVIEW_SNAPSHOT_BYTES,
  );
  if (
    !previewSnapshot ||
    previewSnapshot.modifiedAtMs < options.turnStartedAtMs ||
    previewSnapshot.sha256 !== preview.sha256
  ) {
    return failed;
  }
  const previewRead = readSnapshots.some(
    (snapshot) =>
      snapshot.path === previewPath &&
      snapshot.sha256 === preview.sha256 &&
      snapshot.modifiedAtMs === previewSnapshot.modifiedAtMs &&
      snapshot.size === previewSnapshot.size,
  );
  const renderReportValid = true;

  const build = await passingBuild(
    options.workspaceRoot,
    buildReportReference,
    runId,
    options.turnStartedAtMs,
  );
  if (!build || reportSnapshot.modifiedAtMs < build.reportModifiedAtMs) {
    return { buildBound: false, previewRead, renderReportValid };
  }
  if (!Array.isArray(report.meshes) || report.meshes.length === 0) {
    return { buildBound: false, previewRead, renderReportValid };
  }
  for (const mesh of report.meshes) {
    if (
      typeof mesh?.path !== 'string' ||
      typeof mesh.sha256 !== 'string' ||
      mesh.sha256 !== build.displaySha256
    ) {
      return { buildBound: false, previewRead, renderReportValid };
    }
    const meshPath = await resolveArtifactPath(options.workspaceRoot, mesh.path);
    if (meshPath !== build.displayPath) {
      return { buildBound: false, previewRead, renderReportValid };
    }
  }
  const displaySnapshot = await readDigestSnapshot(build.displayPath);
  if (displaySnapshot?.sha256 !== build.displaySha256) {
    return { buildBound: false, previewRead, renderReportValid };
  }
  return {
    buildBound: true,
    buildReportModifiedAtMs: build.reportModifiedAtMs,
    previewRead,
    renderReportValid,
  };
}

async function inspectReferenceEvidence(
  evidenceItems: readonly ReferenceAnalyzeEvidence[],
  options: VisualAuditOptions,
  buildReportModifiedAtMs: number | undefined,
): Promise<boolean> {
  if (buildReportModifiedAtMs === undefined) return false;
  const expected = new Map<string, string>();
  for (const reference of options.referenceImages ?? []) {
    try {
      const path = await realpath(
        isAbsolute(reference.path)
          ? reference.path
          : resolve(options.workspaceRoot, reference.path),
      );
      const snapshot = await readFileSnapshot(
        path,
        MAX_PREVIEW_SNAPSHOT_BYTES,
      );
      if (
        !SHA256.test(reference.sha256) ||
        snapshot?.sha256 !== reference.sha256
      ) {
        return false;
      }
      expected.set(path, reference.sha256);
    } catch {
      return false;
    }
  }
  if (expected.size === 0) return false;

  const observed = new Set<string>();
  for (const evidence of evidenceItems) {
    let callSourcePath: string;
    let resultSourcePath: string;
    try {
      callSourcePath = await realpath(
        isAbsolute(evidence.imagePath)
          ? evidence.imagePath
          : resolve(options.workspaceRoot, evidence.imagePath),
      );
      resultSourcePath = await realpath(
        isAbsolute(evidence.sourcePath)
          ? evidence.sourcePath
          : resolve(options.workspaceRoot, evidence.sourcePath),
      );
    } catch {
      continue;
    }
    const expectedHash = expected.get(callSourcePath);
    const sourceSnapshot = await readFileSnapshot(
      resultSourcePath,
      MAX_PREVIEW_SNAPSHOT_BYTES,
    );
    if (
      !expectedHash ||
      resultSourcePath !== callSourcePath ||
      evidence.expectedSha256 !== expectedHash ||
      evidence.sourceSha256 !== expectedHash ||
      sourceSnapshot?.sha256 !== expectedHash
    ) {
      continue;
    }
    const requestedReportPath = await resolveArtifactPath(
      options.workspaceRoot,
      evidence.reportPath,
    );
    const artifactReportPath = await resolveArtifactPath(
      options.workspaceRoot,
      evidence.artifactReportPath,
    );
    if (!requestedReportPath || artifactReportPath !== requestedReportPath) {
      continue;
    }
    const reportSnapshot = await readFileSnapshot(
      artifactReportPath,
      MAX_EVIDENCE_REPORT_BYTES,
    );
    if (
      !reportSnapshot ||
      reportSnapshot.modifiedAtMs < options.turnStartedAtMs ||
      reportSnapshot.modifiedAtMs > buildReportModifiedAtMs ||
      reportSnapshot.sha256 !== evidence.artifactReportSha256
    ) {
      continue;
    }
    const report = parseSnapshotJson<RawReferenceReport>(reportSnapshot);
    if (
      report?.schema !== REFERENCE_REPORT_SCHEMA ||
      typeof report.source?.path !== 'string' ||
      typeof report.source.sha256 !== 'string' ||
      report.source.sha256 !== expectedHash ||
      report.image?.sha256 !== expectedHash
    ) {
      continue;
    }
    let reportedSource: string;
    try {
      reportedSource = await realpath(
        isAbsolute(report.source.path)
          ? report.source.path
          : resolve(options.workspaceRoot, report.source.path),
      );
    } catch {
      continue;
    }
    if (reportedSource !== callSourcePath) continue;
    observed.add(callSourcePath);
  }
  return observed.size === expected.size;
}

export async function auditCadVisualValidation(
  entries: readonly VisualAuditEntry[],
  options: VisualAuditOptions,
): Promise<VisualAuditResult> {
  let renderCalled = false;
  let successfulCadCompile = false;
  let activeBuildReport: FileReference | undefined;
  let activeCompileRunId: string | undefined;
  let activeRenderReport: FileReference | undefined;
  let activeReferenceAnalyses: ReferenceAnalyzeEvidence[] = [];
  let successfulReadSnapshots: VisualFileSnapshot[] = [];
  const pendingCadCompiles = new Map<string, ReferenceAnalyzeEvidence[]>();
  const pendingReferenceAnalyses = new Map<string, ReferenceAnalyzeCall>();
  const successfulReferenceAnalyses: ReferenceAnalyzeEvidence[] = [];
  const pendingPreviewReads = new Map<string, string>();
  for (const entry of entries) {
    const rawMessage = entry.message;
    if (!rawMessage || typeof rawMessage !== 'object') continue;
    const message = rawMessage as {
      content?: unknown;
      details?: unknown;
      isError?: unknown;
      role?: unknown;
      toolCallId?: unknown;
      toolName?: unknown;
    };
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      for (const rawBlock of message.content) {
        if (!rawBlock || typeof rawBlock !== 'object') continue;
        const call = rawBlock as ToolCallBlock;
        if (call.type !== 'toolCall') continue;
        if (isCadMutation(call)) {
          renderCalled = false;
          successfulCadCompile = false;
          activeBuildReport = undefined;
          activeCompileRunId = undefined;
          activeRenderReport = undefined;
          activeReferenceAnalyses = [];
          successfulReadSnapshots = [];
          pendingCadCompiles.clear();
          pendingPreviewReads.clear();
        }
        if (
          call.name === CAD_COMPILE_TOOL_NAME &&
          typeof call.id === 'string'
        ) {
          pendingCadCompiles.set(call.id, [
            ...successfulReferenceAnalyses,
          ]);
        }
        const reference = referenceAnalyzeCall(call);
        if (reference && typeof call.id === 'string') {
          pendingReferenceAnalyses.set(call.id, reference);
        }
        const readPath = previewReadPath(call);
        if (renderCalled && readPath && typeof call.id === 'string') {
          pendingPreviewReads.set(call.id, readPath);
        }
      }
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === CAD_COMPILE_TOOL_NAME &&
      typeof message.toolCallId === 'string' &&
      pendingCadCompiles.has(message.toolCallId)
    ) {
      const referenceAnalyses =
        pendingCadCompiles.get(message.toolCallId) ?? [];
      pendingCadCompiles.delete(message.toolCallId);
      const details =
        message.details &&
        typeof message.details === 'object' &&
        !Array.isArray(message.details)
          ? (message.details as Record<string, unknown>)
          : undefined;
      const artifacts =
        details?.artifacts &&
        typeof details.artifacts === 'object' &&
        !Array.isArray(details.artifacts)
          ? (details.artifacts as Record<string, unknown>)
          : undefined;
      const renderEvidence =
        artifacts?.renderEvidence &&
        typeof artifacts.renderEvidence === 'object' &&
        !Array.isArray(artifacts.renderEvidence)
          ? (artifacts.renderEvidence as Record<string, unknown>)
          : undefined;
      const buildReport =
        artifacts?.buildReport &&
        typeof artifacts.buildReport === 'object' &&
        !Array.isArray(artifacts.buildReport)
          ? (artifacts.buildReport as Record<string, unknown>)
          : undefined;
      if (
        message.isError !== true &&
        details?.schema === 'evidence-cad-compile-result/v1' &&
        details.pass === true &&
        details.status === 'awaiting-visual-review' &&
        typeof details.runId === 'string' &&
        UUID.test(details.runId) &&
        typeof buildReport?.path === 'string' &&
        typeof buildReport.sha256 === 'string' &&
        SHA256.test(buildReport.sha256) &&
        typeof renderEvidence?.path === 'string' &&
        typeof renderEvidence.sha256 === 'string' &&
        SHA256.test(renderEvidence.sha256)
      ) {
        renderCalled = true;
        successfulCadCompile = true;
        activeCompileRunId = details.runId as string;
        activeBuildReport = {
          path: buildReport.path,
          sha256: buildReport.sha256,
        };
        activeRenderReport = {
          path: renderEvidence.path,
          sha256: renderEvidence.sha256,
        };
        activeReferenceAnalyses = referenceAnalyses;
        successfulReadSnapshots = [];
        pendingPreviewReads.clear();
      }
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === REFERENCE_ANALYZE_TOOL_NAME &&
      typeof message.toolCallId === 'string' &&
      pendingReferenceAnalyses.has(message.toolCallId)
    ) {
      const reference = pendingReferenceAnalyses.get(message.toolCallId);
      pendingReferenceAnalyses.delete(message.toolCallId);
      if (reference && message.isError !== true) {
        const evidence = referenceAnalyzeEvidence(reference, message.details);
        if (evidence) successfulReferenceAnalyses.push(evidence);
      }
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === 'read' &&
      message.isError !== true &&
      typeof message.toolCallId === 'string' &&
      pendingPreviewReads.has(message.toolCallId) &&
      Array.isArray(message.content) &&
      message.content.some(
        (block) =>
          block &&
          typeof block === 'object' &&
          (block as { type?: unknown }).type === 'image',
      )
    ) {
      const path = pendingPreviewReads.get(message.toolCallId);
      if (path && entry.readSnapshot) {
        const canonical = await resolveArtifactPath(options.workspaceRoot, path);
        if (canonical === entry.readSnapshot.path) {
          successfulReadSnapshots.push(entry.readSnapshot);
        }
      }
      pendingPreviewReads.delete(message.toolCallId);
    }
  }
  const evidence = renderCalled
      ? await inspectRenderEvidence(
        activeRenderReport,
        activeBuildReport,
        activeCompileRunId,
        successfulReadSnapshots,
        options,
      )
    : {
        buildBound: false,
        previewRead: false,
        renderReportValid: false,
      };
  const referenceAnalyzed = options.requireReferenceAnalysis
    ? await inspectReferenceEvidence(
        activeReferenceAnalyses,
        options,
        evidence.buildReportModifiedAtMs,
      )
    : false;
  return {
    buildBound: evidence.buildBound,
    pass:
      successfulCadCompile &&
      renderCalled &&
      evidence.previewRead &&
      evidence.renderReportValid &&
      evidence.buildBound &&
      (!options.requireReferenceAnalysis || referenceAnalyzed),
    previewRead: evidence.previewRead,
    referenceAnalyzed,
    renderCalled,
    renderReportValid: evidence.renderReportValid,
  };
}

export function visualValidationInstruction(
  required: boolean,
  requireReferenceAnalysis = false,
): string {
  if (!required) return '';
  return [
    '<visual_validation_required>',
    ...(requireReferenceAnalysis
      ? [
          'Uploaded reference images are present. Call the structured reference_analyze tool once for every saved local image path before modeling, passing the exact saved path, supplied SHA-256, and a workspace-relative JSON report path. The server binds each successful tool result to its call ID and verifies the current-turn report artifact SHA-256, exact source path, source SHA-256, and completion before the cad_compile build it informed; shell commands, hand-copied pixel coordinates, and unsupported visual assertions do not satisfy the reference contract.',
        ]
      : []),
    'This CAD turn has a mandatory visual gate. After any required reference analysis, call cad_compile to build, audit, package, and render the current marker, immutable intent, semantic scene, and generated source. Before compiling again, review the full issue set, identify shared root causes, and try to address related findings in one coordinated change; use judgment when an issue should be deferred and briefly explain that choice. Use repeated or regressed diagnostics to reconsider the construction strategy while preserving the immutable intent, without treating elapsed time or attempt count as a quality limit. A successful call returns evidence-cad-compile-result/v1 with pass=true, status=awaiting-visual-review, and exact artifacts.buildReport and artifacts.renderEvidence references. Use the read tool on the exact preview PNG recorded by that render report; a successful compile alone does not complete visual review. The server verifies current-turn mtimes, file hashes, the read-time preview snapshot, and the render-report mesh binding to the exact evidence-a3d-build/v1 report returned by the successful cad_compile call; command text, workspace scans, or a similarly named old image do not count. Compare the preview against the independent reference/design contract before answering. If the comparison fails, revise the source or scene without weakening the intent, call cad_compile again, and read the new preview.',
    '</visual_validation_required>',
  ].join('\n');
}

export function visualValidationRepairInstruction(
  audit: VisualAuditResult,
  options: VisualRepairOptions,
): string {
  const missing: string[] = [];
  if (options.requireReferenceAnalysis && !audit.referenceAnalyzed) {
    missing.push(
      'call the structured reference_analyze tool for every saved uploaded-image path with its supplied SHA-256 and a workspace-relative report path, then rebuild the final CAD after those hash-bound reports before rerendering and rereading',
    );
  }
  if (!audit.renderCalled) {
    missing.push(
      'call cad_compile for the current marker, intent, scene, and source so it produces a fresh audited preview',
    );
  }
  if (!audit.renderReportValid) {
    missing.push(
      'produce a current-turn evidence-render/v2 JSON with --report whose preview PNG path and SHA-256 match the file on disk',
    );
  }
  if (!audit.buildBound) {
    missing.push(
      'render artifacts["glb:display"] from the exact evidence-a3d-build/v1 artifacts.buildReport returned by the successful cad_compile call, preserving its path and SHA-256 in render-report meshes',
    );
  }
  if (!audit.previewRead) {
    missing.push(
      'use the read tool on that fresh preview PNG and visually compare it with the independent contract/reference',
    );
  }

  return [
    '<visual_validation_repair>',
    `Automatic visual-validation repair attempt ${options.attempt}/${options.maxAttempts}.`,
    `The previous attempt ended without sufficient evidence. Missing: ${missing.join('; ')}.`,
    'Continue in this same session and complete the missing tool work now. Do not ask the user to retry and do not merely describe commands.',
    'Validation must apply to the latest artifact: if you edit or regenerate CAD after reading a preview, render and read a new preview again.',
    'Follow the active skill through QA, freshness, visual comparison, and any required repair. Only provide a final success response when the evidence passes; otherwise report the concrete remaining failure.',
    '</visual_validation_repair>',
  ].join('\n');
}
