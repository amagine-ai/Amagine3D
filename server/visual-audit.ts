import { createHash } from 'node:crypto';
import {
  createReadStream,
  readFileSync,
  realpathSync,
  statSync,
} from 'node:fs';
import { readFile, realpath, stat } from 'node:fs/promises';
import {
  extname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from 'node:path';

import { CAD_COMPILE_TOOL_NAME } from '@amagine3d/a3d-runtime';

import { resolveArtifactPath, scanArtifacts } from './artifacts.ts';
import {
  BUILD_REPORT_SCHEMA,
  type UnifiedBuildReport,
  validateUnifiedBuildReport,
} from './build-report.ts';

const REFERENCE_REPORT_SCHEMA = 'evidence-reference-analysis/v1';
const RENDER_REPORT_SCHEMA = 'evidence-render/v2';
const MAX_PREVIEW_SNAPSHOT_BYTES = 64 * 1024 * 1024;
const SHA256 = /^[0-9a-f]{64}$/u;
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
  skillsRoot: string;
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

function insideRoot(root: string, path: string): boolean {
  const value = relative(root, path);
  return value === '' || (value !== '..' && !value.startsWith(`..${sep}`));
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
  return {
    artifacts: {
      ...(renderEvidence &&
      typeof renderEvidence === 'object' &&
      !Array.isArray(renderEvidence)
        ? { renderEvidence }
        : {}),
    },
    pass: record.pass,
    schema: record.schema,
    status: record.status,
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
            const metadata = statSync(canonical);
            if (
              metadata.isFile() &&
              metadata.size > 0 &&
              metadata.size <= MAX_PREVIEW_SNAPSHOT_BYTES &&
              insideRoot(this.workspaceRoot, canonical) &&
              extname(canonical).toLowerCase() === '.png'
            ) {
              const bytes = readFileSync(canonical);
              entry.readSnapshot = {
                modifiedAtMs: metadata.mtimeMs,
                path: canonical,
                sha256: createHash('sha256').update(bytes).digest('hex'),
                size: metadata.size,
              };
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

function tokenizeSimpleCommand(command: string): string[] | undefined {
  if (
    /[;&|<>`#\u0000-\u001f\u007f]/u.test(command) ||
    command.includes('$(')
  ) {
    return undefined;
  }
  const tokens: string[] = [];
  let token = '';
  let quote: '"' | "'" | undefined;
  let escaped = false;
  for (const character of command.trim()) {
    if (escaped) {
      token += character;
      escaped = false;
      continue;
    }
    if (character === '\\' && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = undefined;
      else token += character;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (/\s/u.test(character)) {
      if (token) tokens.push(token);
      token = '';
      continue;
    }
    token += character;
  }
  if (escaped || quote) return undefined;
  if (token) tokens.push(token);
  return tokens.length > 0 ? tokens : undefined;
}

interface PythonInvocation {
  arguments: string[];
  scriptPath: string;
}

interface TrustedVisualScripts {
  reference: string;
}

function scriptInvocation(call: ToolCallBlock): PythonInvocation | undefined {
  if (call.name !== 'bash') return undefined;
  const command = recordArguments(call.arguments).command;
  if (typeof command !== 'string') return undefined;
  const tokens = tokenizeSimpleCommand(command);
  if (!tokens || tokens.length < 2) return undefined;
  if (tokens[0] !== 'python') return undefined;
  const scriptPath = tokens[1];
  if (!scriptPath || !scriptPath.toLowerCase().endsWith('.py')) return undefined;
  return { arguments: tokens.slice(2), scriptPath };
}

function optionValue(tokens: readonly string[], name: string): string | undefined {
  const equalsPrefix = `${name}=`;
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === name) {
      const value = tokens[index + 1];
      return value && !value.startsWith('-') ? value : undefined;
    }
    if (token?.startsWith(equalsPrefix)) {
      const value = token.slice(equalsPrefix.length);
      return value || undefined;
    }
  }
  return undefined;
}

interface ReferenceInvocation {
  imagePath: string;
  reportPath: string;
}

function referenceInvocation(
  invocation: PythonInvocation | undefined,
  trusted: TrustedVisualScripts,
): ReferenceInvocation | undefined {
  if (!invocation || invocation.scriptPath !== trusted.reference) return undefined;
  const imagePath = invocation.arguments[0];
  const reportPath = optionValue(invocation.arguments, '--out');
  if (!imagePath || imagePath.startsWith('-') || !reportPath) return undefined;
  return { imagePath, reportPath };
}

async function canonicalInvocation(
  call: ToolCallBlock,
  workspaceRoot: string,
): Promise<PythonInvocation | undefined> {
  const invocation = scriptInvocation(call);
  if (!invocation) return undefined;
  try {
    const candidate = isAbsolute(invocation.scriptPath)
      ? invocation.scriptPath
      : resolve(workspaceRoot, invocation.scriptPath);
    return {
      ...invocation,
      scriptPath: await realpath(candidate),
    };
  } catch {
    return undefined;
  }
}

async function trustedVisualScripts(
  skillsRoot: string,
): Promise<TrustedVisualScripts | undefined> {
  try {
    const skillRoot = await realpath(join(skillsRoot, 'text-a3d'));
    const reference = await realpath(join(skillRoot, 'reference_analyze.py'));
    return { reference };
  } catch {
    return undefined;
  }
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
  schema?: unknown;
}

interface RawReferenceReport {
  image?: { sha256?: unknown };
  schema?: unknown;
  source?: FileReference;
}

const MAX_EVIDENCE_REPORT_BYTES = 2 * 1024 * 1024;

async function fileSha256(path: string): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    const hash = createHash('sha256');
    const stream = createReadStream(path);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.once('error', reject);
    stream.once('end', () => resolve(hash.digest('hex')));
  });
}

async function readJsonFile<T>(path: string): Promise<T | undefined> {
  try {
    const metadata = await stat(path);
    if (!metadata.isFile() || metadata.size > MAX_EVIDENCE_REPORT_BYTES) {
      return undefined;
    }
    return JSON.parse(await readFile(path, 'utf8')) as T;
  } catch {
    return undefined;
  }
}

async function latestPassingBuild(
  workspaceRoot: string,
  turnStartedAtMs: number,
): Promise<BuildEvidence | undefined> {
  const artifacts = await scanArtifacts(workspaceRoot);
  for (const artifact of artifacts) {
    if (
      artifact.kind !== 'report' ||
      !artifact.name.endsWith('_report.json') ||
      artifact.size > MAX_EVIDENCE_REPORT_BYTES
    ) {
      continue;
    }
    const reportPath = await resolveArtifactPath(workspaceRoot, artifact.path);
    if (!reportPath) continue;
    const candidate = await readJsonFile<UnifiedBuildReport>(reportPath);
    if (candidate?.schema !== BUILD_REPORT_SCHEMA) continue;
    const validated = await validateUnifiedBuildReport(
      workspaceRoot,
      reportPath,
      {
        maxBytes: MAX_EVIDENCE_REPORT_BYTES,
        minimumArtifactModifiedAtMs: turnStartedAtMs,
        minimumModifiedAtMs: turnStartedAtMs,
      },
    );
    if (!validated) return undefined;
    const report = validated.report;

    // This is the newest passing unified build. A broken display reference
    // fails closed instead of falling back to an older successful report.
    const display = report.artifacts?.['glb:display'];
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
  return undefined;
}

async function inspectRenderEvidence(
  reportArgument: string | undefined,
  readSnapshots: readonly VisualFileSnapshot[],
  options: VisualAuditOptions,
): Promise<EvidenceInspection> {
  const failed: EvidenceInspection = {
    buildBound: false,
    previewRead: false,
    renderReportValid: false,
  };
  if (!reportArgument) return failed;
  const reportPath = await resolveArtifactPath(
    options.workspaceRoot,
    reportArgument,
  );
  if (!reportPath) return failed;
  const reportMetadata = await stat(reportPath);
  if (reportMetadata.mtimeMs < options.turnStartedAtMs) return failed;
  const report = await readJsonFile<RawRenderReport>(reportPath);
  if (report?.schema !== RENDER_REPORT_SCHEMA) return failed;

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
  const previewMetadata = await stat(previewPath);
  if (
    previewMetadata.mtimeMs < options.turnStartedAtMs ||
    (await fileSha256(previewPath)) !== preview.sha256
  ) {
    return failed;
  }
  const previewRead = readSnapshots.some(
    (snapshot) =>
      snapshot.path === previewPath &&
      snapshot.sha256 === preview.sha256 &&
      snapshot.modifiedAtMs === previewMetadata.mtimeMs &&
      snapshot.size === previewMetadata.size,
  );
  const renderReportValid = true;

  const build = await latestPassingBuild(
    options.workspaceRoot,
    options.turnStartedAtMs,
  );
  if (!build || reportMetadata.mtimeMs < build.reportModifiedAtMs) {
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
  return {
    buildBound: true,
    buildReportModifiedAtMs: build.reportModifiedAtMs,
    previewRead,
    renderReportValid,
  };
}

async function inspectReferenceEvidence(
  calls: readonly ReferenceInvocation[],
  options: VisualAuditOptions,
  buildReportModifiedAtMs: number | undefined,
): Promise<boolean> {
  if (buildReportModifiedAtMs === undefined) return false;
  const expected = new Map<string, string>();
  for (const reference of options.referenceImages ?? []) {
    try {
      const path = await realpath(reference.path);
      if (
        !SHA256.test(reference.sha256) ||
        (await fileSha256(path)) !== reference.sha256
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
  for (const call of calls) {
    let sourcePath: string;
    try {
      sourcePath = await realpath(call.imagePath);
    } catch {
      continue;
    }
    const expectedHash = expected.get(sourcePath);
    if (!expectedHash) continue;
    const reportPath = await resolveArtifactPath(
      options.workspaceRoot,
      call.reportPath,
    );
    if (!reportPath) continue;
    const reportMetadata = await stat(reportPath);
    if (
      reportMetadata.mtimeMs < options.turnStartedAtMs ||
      reportMetadata.mtimeMs > buildReportModifiedAtMs
    ) {
      continue;
    }
    const report = await readJsonFile<RawReferenceReport>(reportPath);
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
      reportedSource = await realpath(report.source.path);
    } catch {
      continue;
    }
    if (reportedSource !== sourcePath) continue;
    observed.add(sourcePath);
  }
  return observed.size === expected.size;
}

export async function auditCadVisualValidation(
  entries: readonly VisualAuditEntry[],
  options: VisualAuditOptions,
): Promise<VisualAuditResult> {
  const trusted = await trustedVisualScripts(options.skillsRoot);
  if (!trusted) {
    return {
      buildBound: false,
      pass: false,
      previewRead: false,
      referenceAnalyzed: false,
      renderCalled: false,
      renderReportValid: false,
    };
  }
  let renderCalled = false;
  let successfulCadCompile = false;
  let activeRenderReport: string | undefined;
  let successfulReadSnapshots: VisualFileSnapshot[] = [];
  const pendingCadCompiles = new Set<string>();
  const pendingReferenceAnalyses = new Map<string, ReferenceInvocation>();
  const successfulReferenceAnalyses: ReferenceInvocation[] = [];
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
        const invocation = await canonicalInvocation(call, options.workspaceRoot);
        if (isCadMutation(call)) {
          renderCalled = false;
          successfulCadCompile = false;
          activeRenderReport = undefined;
          successfulReadSnapshots = [];
          pendingCadCompiles.clear();
          pendingPreviewReads.clear();
        }
        if (
          call.name === CAD_COMPILE_TOOL_NAME &&
          typeof call.id === 'string'
        ) {
          pendingCadCompiles.add(call.id);
        }
        const reference = referenceInvocation(invocation, trusted);
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
      if (
        message.isError !== true &&
        details?.schema === 'evidence-cad-compile-result/v1' &&
        details.pass === true &&
        details.status === 'awaiting-visual-review' &&
        typeof renderEvidence?.path === 'string'
      ) {
        renderCalled = true;
        successfulCadCompile = true;
        activeRenderReport = renderEvidence.path;
        successfulReadSnapshots = [];
        pendingPreviewReads.clear();
      }
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === 'bash' &&
      message.isError !== true &&
      typeof message.toolCallId === 'string' &&
      pendingReferenceAnalyses.has(message.toolCallId)
    ) {
      const reference = pendingReferenceAnalyses.get(message.toolCallId);
      if (reference) successfulReferenceAnalyses.push(reference);
      pendingReferenceAnalyses.delete(message.toolCallId);
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
        successfulReferenceAnalyses,
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
          'Uploaded reference images are present. Run the selected skill\'s reference_analyze.py once for every saved local image path before modeling, with an explicit --out reference-report.json. The server verifies a current-turn evidence-reference-analysis/v1 report, exact source path, and source SHA-256 for every upload; hand-copied pixel coordinates or unsupported visual assertions do not satisfy the reference contract.',
        ]
      : []),
    'This CAD turn has a mandatory visual gate. After any required reference analysis, call cad_compile to build, audit, package, and render the current marker, immutable intent, semantic scene, and generated source. A successful call returns evidence-cad-compile-result/v1 with pass=true, status=awaiting-visual-review, and artifacts.renderEvidence.path pointing to a current evidence-render/v2 report bound to the latest passing evidence-a3d-build/v1 report. Use the read tool on the exact preview PNG recorded by that report; a successful compile alone does not complete visual review. The server verifies current-turn mtimes, file hashes, the read-time preview snapshot, and the render-report mesh binding to the latest passing build; command text or a similarly named old image does not count. Compare the preview against the independent reference/design contract before answering. If the comparison fails, revise the source or scene without weakening the intent, call cad_compile again, and read the new preview.',
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
      'run the canonical reference_analyze.py --out <report.json> for every saved uploaded-image path, bind each evidence-reference-analysis/v1 source path and SHA-256, then rebuild the final CAD after those reports before rerendering and rereading',
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
      'render artifacts["glb:display"] from the workspace latest passing evidence-a3d-build/v1 report, preserving its exact path and SHA-256 in render-report meshes',
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
