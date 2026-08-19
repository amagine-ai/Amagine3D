'use client';

import {
  BuildSuperseded,
  LatestBuildQueue,
  applyProportionalAdjustment,
  changeParameter,
  cadAgentToolErrorState,
  couplingForParameter,
  createCadTools,
  MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS,
  redoParameterChange,
  resetParameters,
  shouldContinueCadAgent,
  undoParameterChange,
  type CadTools,
} from '@amagine3d/cad-agent';
import {
  researchStreamEventSchema,
  SCHEMA_VERSION,
  type CadWorkflowPreference,
  type ColorRegionPlan,
  type ParameterSet,
  type ParameterValue,
  type ResearchPacket,
  type VisualReviewInput,
  type ModelProfile,
  type ModelProfileSettings,
} from '@amagine3d/cad-protocol';
import {
  freezeModelProfile,
  toFileUIParts,
  validateImageInputs,
  type ImageInput,
} from '@amagine3d/cad-agent';
import {
  CAD_RUNTIME_MANIFEST,
  createBrowserCadExecutor,
  type BrowserCadExecutor,
  type CadExecutorEvent,
} from '@amagine3d/cad-execution-browser';
import {
  OpfsProjectRepository,
  listOpfsWorkspaceFiles,
  readOpfsWorkspaceFile,
  removeOpfsWorkspaceFiles,
  type OpfsWorkspaceFile,
} from '@amagine3d/cad-storage-opfs';
import type { ViewerModel } from '@amagine3d/cad-viewer';
import { useChat } from '@ai-sdk/react';
import {
  DefaultChatTransport,
  getToolName,
  isToolUIPart,
  validateUIMessages,
  type InferUITools,
  type UIMessage,
} from 'ai';
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type Ref,
} from 'react';

import {
  CadAgentProjectController,
  type CadProjectRecord,
  loadCadAgentProject,
  listCadAgentProjects,
  type CadWorkspaceArtifact,
  type CadWorkspaceSnapshot,
  type SourceWritebackPreview,
  type VisualReviewDecision,
} from '../lib/cad-agent-project-controller';
import { planCadChatSubmission } from '../lib/cad-chat-continuation';
import { externalHttpUrl, safeDownloadFileName } from '../lib/browser-security';
import {
  BUNDLED_POMODORO_RUN_ID,
  bundledPomodoroPreviewFileId,
  bundledPomodoroProjectName,
  compareProjectsWithPomodoroLast,
  ensureBundledPomodoroProject,
  isBundledPomodoroProject,
  loadBundledPomodoroViewerModel,
  selectInitialProjectId,
} from '../lib/bundled-pomodoro-project';
import { useI18n, type Language } from '../lib/i18n';
import { CadViewerShell } from './cad-viewer-shell';
import styles from './cad-workbench.module.css';

type ResearchStage = {
  status: 'running' | 'completed' | 'failed' | 'skipped';
  packet?: ResearchPacket;
  attempt?: number;
  total?: number;
};

type CadUiMessageMetadata = {
  runId?: string;
  research?: ResearchStage;
};

type CadUiMessage = UIMessage<
  CadUiMessageMetadata,
  never,
  InferUITools<CadTools>
>;
type CadUiMessagePart = CadUiMessage['parts'][number];

type PendingConversationTurn = {
  id: string;
  text: string;
  attachments: Array<{ fileName: string; url: string }>;
  research: ResearchStage;
};

type ConversationTurn = {
  id: string;
  user?: CadUiMessage;
  assistantMessages: CadUiMessage[];
};

function groupConversationTurns(messages: CadUiMessage[]): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (const message of messages) {
    if (message.role === 'user') {
      turns.push({ id: message.id, user: message, assistantMessages: [] });
      continue;
    }
    if (message.role !== 'assistant') continue;
    const current = turns.at(-1);
    if (current === undefined || current.user === undefined) {
      turns.push({
        id: `assistant-${message.id}`,
        assistantMessages: [message],
      });
    } else {
      current.assistantMessages.push(message);
    }
  }
  return turns;
}

type RunContext = {
  runId: string;
  workflowKind: 'single-color' | 'multi-color';
  phase:
    | 'briefing'
    | 'coding'
    | 'building'
    | 'qa'
    | 'visual_review_waiting'
    | 'visual_review'
    | 'ready_to_finish'
    | 'completed'
    | 'failed'
    | 'cancelled';
  userRequest: string;
  research?: ResearchPacket;
  visualReviewConsent: 'approved' | 'declined';
  modelProfileId?: string;
  modificationContext?: {
    source: string;
    designBrief: unknown;
    parameterSchema: unknown;
    latestQa?: unknown;
  };
};

type PendingVisualReview = {
  input: VisualReviewInput;
  resolve: (decision: VisualReviewDecision) => void;
};

type ActivityFailure = {
  phase: RunContext['phase'];
  reason: string;
  tool: string | undefined;
};

type RuntimeActivityEvent = {
  id: number;
  occurredAt: number;
  event: CadExecutorEvent;
};

type WorkspaceFile = {
  id: string;
  label: string;
  path: string;
  category: 'source' | 'data' | 'model' | 'report';
  text?: string;
  artifact?: CadWorkspaceArtifact;
};

type ResizeSide = 'left' | 'right';

type ActiveResize = {
  side: ResizeSide;
  pointerId: number;
  startX: number;
  startWidth: number;
};

type ActiveLogResize = {
  pointerId: number;
  startY: number;
  startHeight: number;
};

type RunOptionsPanel = 'workflow' | 'research' | 'review' | 'model';
type AddModelDialogDraft = {
  modelId: string;
  displayName: string;
  imageInput: boolean;
};
type ToolbarIconName =
  'new-run' | 'workflow' | 'research' | 'review' | 'send' | 'stop';

type ReasoningMarkdownBlock =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'lead'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'unordered-list'; items: string[] }
  | { kind: 'ordered-list'; items: string[] }
  | { kind: 'code'; language: string; text: string };

const LEFT_PANEL_MIN = 280;
const LEFT_PANEL_MAX = 520;
const RIGHT_PANEL_MIN = 288;
const RIGHT_PANEL_MAX = 480;
const COLLAPSED_PANEL_WIDTH = 52;
const RESIZER_TOTAL_WIDTH = 16;
const CENTER_PANEL_MIN = 320;
const LOG_PANEL_MIN = 128;
const LOG_PANEL_MAX = 420;
const LOG_PANEL_COLLAPSED_HEIGHT = 52;
const PREVIEW_PANEL_MIN = 240;

function ToolbarIcon({ name }: { name: ToolbarIconName }) {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      {name === 'new-run' ? (
        <path d="M12 5v14M5 12h14" />
      ) : name === 'workflow' ? (
        <>
          <path d="M4 7h5M15 7h5M4 17h9M18 17h2" />
          <circle cx="12" cy="7" r="2.5" />
          <circle cx="15.5" cy="17" r="2.5" />
        </>
      ) : name === 'research' ? (
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16" />
        </>
      ) : name === 'review' ? (
        <>
          <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
          <circle cx="12" cy="12" r="2.5" />
        </>
      ) : name === 'send' ? (
        <path d="M12 19V5M6 11l6-6 6 6" />
      ) : (
        <rect height="10" rx="1.5" width="10" x="7" y="7" />
      )}
    </svg>
  );
}

function GearIcon() {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </svg>
  );
}

function WrenchIcon() {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      <path d="M14.6 6.2a4.5 4.5 0 0 0-5.8 5.7L3.5 17.2a1.8 1.8 0 0 0 2.6 2.6l5.3-5.3a4.5 4.5 0 0 0 5.7-5.8l-2.6 2.6-2.8-.7-.7-2.8 2.6-2.6Z" />
    </svg>
  );
}

function LoadingSpinner() {
  return <span aria-hidden="true" className={styles.loadingSpinner} />;
}

function ToolStatusMark({ state }: { state: 'error' | 'success' }) {
  return (
    <span
      aria-label={
        state === 'success' ? 'Tool completed' : 'Tool did not complete'
      }
      className={styles.toolStatusMark}
      data-state={state}
      role="img"
    >
      <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="6.5" />
        {state === 'success' ? (
          <path d="m4.8 8.1 2 2 4.4-4.5" />
        ) : (
          <path d="m5.5 5.5 5 5m0-5-5 5" />
        )}
      </svg>
    </span>
  );
}

function formatRunDuration(
  durationMs: number | undefined,
  labels: { notRecorded: string; seconds: string; minutes: string },
): string {
  if (durationMs === undefined) return labels.notRecorded;
  if (durationMs < 60_000)
    return `${(durationMs / 1_000).toFixed(1)} ${labels.seconds}`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.floor((durationMs % 60_000) / 1_000);
  return `${String(minutes)} ${labels.minutes} ${String(seconds).padStart(2, '0')} ${labels.seconds}`;
}

function formatExecutionTime(value: string, language: Language): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime()))
    return language === 'zh' ? '时间未知' : 'Unknown time';
  return new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en-US', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function executionDisplayTitle(
  value: string,
  fallback: string,
  maxUnits = 34,
): string {
  let units = 0;
  let result = '';
  for (const character of value.trim()) {
    const weight = (character.codePointAt(0) ?? 0) > 0xff ? 2 : 1;
    if (units + weight > maxUnits) break;
    result += character;
    units += weight;
  }
  return result || fallback;
}

function parseReasoningMarkdown(markdown: string): ReasoningMarkdownBlock[] {
  const lines = markdown
    .replaceAll('\r\n', '\n')
    .replaceAll('\r', '\n')
    .split('\n');
  const blocks: ReasoningMarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? '';

    if (line.trim().length === 0) {
      index += 1;
      continue;
    }

    const codeFence = /^```([^\s`]*)\s*$/.exec(line.trim());
    if (codeFence !== null) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && lines[index]?.trim() !== '```') {
        codeLines.push(lines[index] ?? '');
        index += 1;
      }
      if (lines[index]?.trim() === '```') index += 1;
      blocks.push({
        kind: 'code',
        language: codeFence[1] ?? '',
        text: codeLines.join('\n'),
      });
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line.trim());
    if (heading !== null) {
      blocks.push({
        kind: 'heading',
        level: heading[1]?.length ?? 2,
        text: heading[2] ?? '',
      });
      index += 1;
      continue;
    }

    const boldLead =
      /^\*\*(.+?)\*\*([。.!！?？]?)$/.exec(line.trim()) ??
      /^__(.+?)__([。.!！?？]?)$/.exec(line.trim());
    if (boldLead !== null) {
      blocks.push({
        kind: 'lead',
        text: `${boldLead[1] ?? ''}${boldLead[2] ?? ''}`,
      });
      index += 1;
      continue;
    }

    if (/^[-*+]\s+/.test(line.trim())) {
      const items: string[] = [];
      while (
        index < lines.length &&
        /^[-*+]\s+/.test((lines[index] ?? '').trim())
      ) {
        items.push((lines[index] ?? '').trim().replace(/^[-*+]\s+/, ''));
        index += 1;
      }
      blocks.push({ kind: 'unordered-list', items });
      continue;
    }

    if (/^\d+[.)]\s+/.test(line.trim())) {
      const items: string[] = [];
      while (
        index < lines.length &&
        /^\d+[.)]\s+/.test((lines[index] ?? '').trim())
      ) {
        items.push((lines[index] ?? '').trim().replace(/^\d+[.)]\s+/, ''));
        index += 1;
      }
      blocks.push({ kind: 'ordered-list', items });
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const paragraphLine = lines[index] ?? '';
      const trimmedLine = paragraphLine.trim();
      if (
        trimmedLine.length === 0 ||
        /^```/.test(trimmedLine) ||
        /^(#{1,6})\s+/.test(trimmedLine) ||
        /^[-*+]\s+/.test(trimmedLine) ||
        /^\d+[.)]\s+/.test(trimmedLine)
      ) {
        break;
      }
      paragraphLines.push(trimmedLine);
      index += 1;
    }
    blocks.push({ kind: 'paragraph', text: paragraphLines.join(' ') });
  }

  return blocks;
}

function ReasoningMarkdown({ text }: { text: string }) {
  const blocks = parseReasoningMarkdown(text);

  return (
    <div className={styles.reasoningText}>
      {blocks.map((block, index) => {
        if (block.kind === 'heading') {
          return block.level <= 2 ? (
            <h3 key={index}>{block.text}</h3>
          ) : (
            <h4 key={index}>{block.text}</h4>
          );
        }
        if (block.kind === 'lead') {
          return (
            <p className={styles.reasoningLead} key={index}>
              {block.text}
            </p>
          );
        }
        if (block.kind === 'unordered-list') {
          return (
            <ul key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{item}</li>
              ))}
            </ul>
          );
        }
        if (block.kind === 'ordered-list') {
          return (
            <ol key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{item}</li>
              ))}
            </ol>
          );
        }
        if (block.kind === 'code') {
          return (
            <pre key={index}>
              <code data-language={block.language || undefined}>
                {block.text}
              </code>
            </pre>
          );
        }
        return <p key={index}>{block.text}</p>;
      })}
    </div>
  );
}

function jsonText(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function extractTracebackParameters(
  traceback: string,
  knownNames: string[],
): string[] {
  // A build123d ValueError traceback carries the offending source line, e.g.
  //   File ".../model.py", line 85, in <module>
  //     base_profile = RectangleRounded(CASE_W, CASE_D, OUTER_R)
  // Pull the referenced top-level constants so only the culprit is flagged.
  const sourceLines = traceback
    .split('\n')
    .filter((line) => /^\s{4}\S/.test(line) && !line.includes('File "'));
  const matched = new Set<string>();
  for (const line of sourceLines) {
    for (const name of knownNames) {
      if (new RegExp(`\\b${name}\\b`, 'u').test(line)) matched.add(name);
    }
  }
  return [...matched];
}

function prettifyJsonText(text: string): string {
  const line = text.trim().replace(/\n+$/u, '');
  try {
    return `${JSON.stringify(JSON.parse(line), null, 2)}\n`;
  } catch {
    return text;
  }
}

function formatByteLength(byteLength: number): string {
  if (byteLength < 1_024) return `${String(byteLength)} B`;
  if (byteLength < 1_048_576) {
    return `${(byteLength / 1_024).toFixed(byteLength < 10_240 ? 1 : 0)} KB`;
  }
  return `${(byteLength / 1_048_576).toFixed(1)} MB`;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function workspaceFiles(
  snapshot: CadWorkspaceSnapshot | undefined,
): WorkspaceFile[] {
  if (snapshot === undefined) return [];
  const files: WorkspaceFile[] = [];
  if (snapshot.source !== undefined) {
    files.push({
      id: 'source:model.py',
      label: 'model.py',
      path: 'cad/model.py',
      category: 'source',
      text: snapshot.source,
    });
  }
  if (snapshot.parameters !== undefined) {
    files.push({
      id: 'data:parameters.json',
      label: 'parameters.json',
      path: 'cad/parameters.json',
      category: 'data',
      text: jsonText(snapshot.parameters),
    });
  }
  if (snapshot.designBrief !== undefined) {
    files.push({
      id: 'data:design-brief.json',
      label: 'design-brief.json',
      path: 'cad/design-brief.json',
      category: 'data',
      text: jsonText(snapshot.designBrief),
    });
  }
  if (snapshot.designBrief?.colorRegionPlan !== undefined) {
    files.push({
      id: 'data:color-plan.json',
      label: 'color-plan.json',
      path: 'cad/color-plan.json',
      category: 'data',
      text: jsonText(snapshot.designBrief.colorRegionPlan),
    });
  }
  if (snapshot.research !== undefined) {
    files.push({
      id: 'data:research.json',
      label: 'research.json',
      path: `runs/${snapshot.runId}/research.json`,
      category: 'data',
      text: jsonText(snapshot.research),
    });
  }
  if (snapshot.buildReport !== undefined) {
    files.push({
      id: 'report:build-report.json',
      label: 'build-report.json',
      path: `runs/${snapshot.runId}/build-report.json`,
      category: 'report',
      text: jsonText(snapshot.buildReport),
    });
  }
  if (snapshot.qaReport !== undefined) {
    files.push({
      id: 'report:qa-report.json',
      label: 'qa-report.json',
      path: `runs/${snapshot.runId}/qa-report.json`,
      category: 'report',
      text: jsonText(snapshot.qaReport),
    });
  }
  return [
    ...files,
    ...snapshot.artifacts
      .filter(
        ({ metadata }) =>
          ![
            'model-source',
            'build-report',
            'color-plan',
            'design-brief',
            'qa-report',
            'research-packet',
            'preview-glb',
          ].includes(metadata.kind),
      )
      .map((artifact) => ({
        id: `artifact:${artifact.metadata.id}`,
        label: artifact.metadata.fileName,
        path:
          artifact.metadata.kind === 'region-stl'
            ? `runs/${snapshot.runId}/regions/${artifact.metadata.fileName}`
            : `runs/${snapshot.runId}/${artifact.metadata.fileName}`,
        category:
          artifact.metadata.kind === 'build-report' ||
          artifact.metadata.kind === 'qa-report'
            ? ('report' as const)
            : ('model' as const),
        artifact,
      })),
  ];
}

function downloadBytes(fileName: string, mediaType: string, bytes: Uint8Array) {
  const blob = new Blob([Uint8Array.from(bytes)], { type: mediaType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = safeDownloadFileName(fileName);
  anchor.click();
  URL.revokeObjectURL(url);
}

type DownloadJob = {
  fileName: string;
  mediaType: string;
  bytes: Uint8Array;
};

function enqueueDownloads(jobs: DownloadJob[]) {
  let delay = 0;
  for (const job of jobs) {
    globalThis.setTimeout(() => {
      downloadBytes(job.fileName, job.mediaType, job.bytes);
    }, delay);
    delay += 350;
  }
}

function mediaTypeForFileName(fileName: string): string {
  const suffix = fileName.toLowerCase().split('.').pop() ?? '';
  switch (suffix) {
    case 'stl':
      return 'model/stl';
    case 'glb':
      return 'model/gltf-binary';
    case '3mf':
      return 'model/3mf';
    case 'obj':
      return 'model/obj';
    case 'step':
    case 'stp':
      return 'model/step';
    case 'py':
      return 'text/x-python';
    case 'json':
      return 'application/json';
    default:
      return 'application/octet-stream';
  }
}

function viewerModelForArtifact(
  artifact: CadWorkspaceArtifact,
  colorRegionPlan: ColorRegionPlan | undefined,
): ViewerModel | undefined {
  const { metadata, bytes } = artifact;
  if (
    metadata.kind !== 'model-3mf' &&
    metadata.kind !== 'stl' &&
    metadata.kind !== 'region-stl' &&
    metadata.kind !== 'preview-glb'
  ) {
    return undefined;
  }
  const suffix = metadata.fileName.toLowerCase().split('.').pop() ?? '';
  const format: ViewerModel['parts'][number]['format'] =
    suffix === 'glb' ? 'glb' : suffix === '3mf' ? '3mf' : 'stl';
  const region =
    metadata.regionName === undefined
      ? undefined
      : colorRegionPlan?.regions.find(
          (candidate) =>
            candidate.id === metadata.regionName ||
            candidate.name === metadata.regionName,
        );
  return {
    id: metadata.id,
    name: metadata.fileName,
    parts: [
      {
        id: metadata.id,
        name: metadata.fileName,
        format,
        bytes: Uint8Array.from(bytes).buffer,
        ...(region === undefined ? {} : { region }),
      },
    ],
  };
}

function supportsSeparatedPreview(model: ViewerModel | undefined): boolean {
  return (
    model !== undefined &&
    ((model.separatedParts?.length ?? 0) > 0 ||
      model.parts.length > 1 ||
      model.parts.some((part) => part.format === '3mf'))
  );
}

function modelWithPreviewLayout(
  model: ViewerModel | undefined,
  layout: 'assembled' | 'separated',
): ViewerModel | undefined {
  if (model === undefined) return undefined;
  return {
    ...model,
    layout: supportsSeparatedPreview(model) ? layout : 'assembled',
  };
}

function expectedToolForPhase(
  context: RunContext | undefined,
): keyof CadTools | undefined {
  switch (context?.phase) {
    case 'briefing':
      return 'saveDesignBrief';
    case 'coding':
      return 'writeCadSource';
    case 'building':
      return 'buildAndCheck';
    case 'visual_review_waiting':
      return context.visualReviewConsent === 'approved'
        ? 'requestVisualReview'
        : undefined;
    case 'ready_to_finish':
      return 'finishCadRun';
    default:
      return undefined;
  }
}

function hasPendingToolCall(
  messages: CadUiMessage[],
  toolName: keyof CadTools,
): boolean {
  return messages.some((message) =>
    message.parts.some(
      (part) =>
        isToolUIPart(part) &&
        getToolName(part) === toolName &&
        part.state !== 'output-available' &&
        part.state !== 'output-error',
    ),
  );
}

type ToolRetryAttempt = {
  attempt: number;
  limit: number;
  halted: boolean;
};

function toolRetryAttempts(
  messages: CadUiMessage[],
): Map<string, ToolRetryAttempt> {
  const attempts = new Map<string, ToolRetryAttempt>();
  let consecutiveErrors = 0;

  for (const message of messages) {
    if (message.role === 'user') {
      consecutiveErrors = 0;
      continue;
    }
    if (message.role !== 'assistant') continue;
    for (const part of message.parts) {
      if (!isToolUIPart(part)) continue;
      if (part.state === 'output-available') {
        consecutiveErrors = 0;
        continue;
      }
      if (part.state !== 'output-error') continue;
      consecutiveErrors += 1;
      const limit = part.errorText.includes('CAD runtime bootstrap exceeded')
        ? 1
        : MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS;
      attempts.set(part.toolCallId, {
        attempt: consecutiveErrors,
        limit,
        halted: consecutiveErrors >= limit,
      });
    }
  }

  return attempts;
}

function objectRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : undefined;
}

function toolOutputFailed(output: unknown): boolean {
  const record = objectRecord(output);
  return record?.status === 'failed' || record?.passed === false;
}

async function runResearch(
  runId: string,
  query: string,
): Promise<ResearchPacket | undefined> {
  const response = await fetch('/api/research', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ schemaVersion: 1, runId, query, enabled: true }),
  });
  if (!response.ok || response.body === null) {
    console.warn(
      `[research] HTTP ${String(response.status)} failed for run ${runId}.`,
    );
    return undefined;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let packet: ResearchPacket | undefined;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.trim().length === 0) continue;
      const event = researchStreamEventSchema.parse(JSON.parse(line));
      if (event.type === 'research-result') packet = event.packet;
    }
    if (done) break;
  }
  if (buffer.trim().length > 0) {
    const event = researchStreamEventSchema.parse(JSON.parse(buffer));
    if (event.type === 'research-result') packet = event.packet;
  }
  if (packet !== undefined && packet.status === 'failed') {
    console.warn(
      `[research] run ${runId} returned no usable findings.${
        packet.warnings.length === 0
          ? ''
          : ` Warnings: ${packet.warnings.join(' | ')}`
      }`,
    );
  }
  return packet;
}

const MAX_RESEARCH_ATTEMPTS = 3;
const RESEARCH_RETRY_DELAY_MS = 1_500;

function researchRetryDelay(attempt: number): number {
  return RESEARCH_RETRY_DELAY_MS * Math.max(1, attempt - 1);
}

async function runResearchWithRetry(
  runId: string,
  query: string,
  onAttempt: (attempt: number, total: number) => void,
): Promise<ResearchPacket | undefined> {
  let lastPacket: ResearchPacket | undefined;
  for (let attempt = 1; attempt <= MAX_RESEARCH_ATTEMPTS; attempt++) {
    onAttempt(attempt, MAX_RESEARCH_ATTEMPTS);
    try {
      const packet = await runResearch(runId, query);
      if (packet !== undefined && packet.status !== 'failed') return packet;
      lastPacket = packet;
    } catch (error) {
      console.error(
        `[research] run ${runId} threw on attempt ${String(attempt)}:`,
        error,
      );
    }
    if (attempt < MAX_RESEARCH_ATTEMPTS) {
      await new Promise((resolve) =>
        setTimeout(resolve, researchRetryDelay(attempt)),
      );
    }
  }
  return lastPacket;
}

function phaseLabel(
  context: RunContext | undefined,
  language: Language,
): string {
  const phase = context?.phase;
  if (language === 'en') return phase?.replaceAll('_', ' ') ?? 'not started';
  if (phase === undefined) return '尚未开始';
  const labels: Record<RunContext['phase'], string> = {
    briefing: '整理需求',
    coding: '编写代码',
    building: '构建中',
    qa: '质量检查',
    visual_review_waiting: '等待视觉审查',
    visual_review: '视觉审查',
    ready_to_finish: '即将完成',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  };
  return labels[phase];
}

function toolPhase(toolName: string): string {
  switch (toolName) {
    case 'saveDesignBrief':
      return 'briefing';
    case 'writeCadSource':
      return 'coding';
    case 'buildAndCheck':
      return 'building';
    case 'requestVisualReview':
      return 'visual_review_waiting';
    case 'finishCadRun':
      return 'ready_to_finish';
    default:
      return 'tool call';
  }
}

function activeAgentPhase(status: string): RunContext['phase'] {
  switch (status) {
    case 'briefing':
    case 'coding':
    case 'building':
    case 'qa':
    case 'visual_review_waiting':
    case 'visual_review':
    case 'ready_to_finish':
    case 'completed':
    case 'failed':
    case 'cancelled':
      return status;
    default:
      throw new Error(`Workflow state ${status} is not a CAD Agent phase.`);
  }
}

export type CadDownloadTarget =
  | {
      fileName: string;
      category: 'model' | 'file' | 'storage';
    }
  | undefined;

export type CadWorkbenchHandle = {
  downloadCurrent: () => Promise<void>;
};

type CadWorkbenchProps = {
  storageOpen?: boolean;
  onStorageOpenChange?: (open: boolean) => void;
  onDownloadTargetChange?: (target: CadDownloadTarget) => void;
  ref?: Ref<CadWorkbenchHandle>;
};

export function CadWorkbench({
  storageOpen = false,
  onStorageOpenChange,
  onDownloadTargetChange,
  ref,
}: CadWorkbenchProps) {
  const { language, t } = useI18n();
  const text = (english: string, chinese: string) =>
    language === 'zh' ? chinese : english;
  const projectDisplayName = (
    projectId: string | undefined,
    fallback: string,
  ) =>
    isBundledPomodoroProject(projectId)
      ? bundledPomodoroProjectName(language)
      : fallback;
  const [prompt, setPrompt] = useState('');
  const [preference, setPreference] = useState<CadWorkflowPreference>('auto');
  const [searchEnabled, setSearchEnabled] = useState(false);
  const [visualReviewApproved, setVisualReviewApproved] = useState(false);
  const [imageAttachments, setImageAttachments] = useState<ImageInput[]>([]);
  const [modelSettings, setModelSettings] = useState<ModelProfileSettings>();
  const [selectedModelProfileId, setSelectedModelProfileId] =
    useState<string>();
  const [addModelDialogOpen, setAddModelDialogOpen] = useState(false);
  const [addModelDialogDraft, setAddModelDialogDraft] =
    useState<AddModelDialogDraft>({
      modelId: '',
      displayName: '',
      imageInput: false,
    });
  const [addModelDialogError, setAddModelDialogError] = useState<string>();
  const [addModelDialogSaving, setAddModelDialogSaving] = useState(false);
  const [started, setStarted] = useState(false);
  const [statusMessage, setStatusMessage] = useState(
    'Choose the run options, then start the CAD Agent.',
  );
  const [research, setResearch] = useState<ResearchPacket>();
  const [model, setModel] = useState<ViewerModel>();
  const [previewLayout, setPreviewLayout] = useState<'assembled' | 'separated'>(
    'assembled',
  );
  const [workspaceSnapshot, setWorkspaceSnapshot] =
    useState<CadWorkspaceSnapshot>();
  const [parameters, setParameters] = useState<ParameterSet>();
  const [selectedFileId, setSelectedFileId] = useState('model:preview');
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [writebackPreview, setWritebackPreview] =
    useState<SourceWritebackPreview>();
  const [forceWriteback, setForceWriteback] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [parameterScaleNotice, setParameterScaleNotice] = useState<{
    changedName: string;
    scaledNames: string[];
    source: string;
  }>();
  const [parameterBuildIssue, setParameterBuildIssue] = useState<{
    kind: 'build-error' | 'qa-failed';
    message: string;
    names: string[];
  }>();
  const [runPreparing, setRunPreparing] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(true);
  const [leftWidth, setLeftWidth] = useState(340);
  const [rightWidth, setRightWidth] = useState(320);
  const [logHeight, setLogHeight] = useState(208);
  const [logCollapsed, setLogCollapsed] = useState(true);
  const [leftView, setLeftView] = useState<'chat' | 'files'>('chat');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [projectRecords, setProjectRecords] = useState<CadProjectRecord[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>();
  const [selectedRunId, setSelectedRunId] = useState<string>();
  const [pendingConversationTurn, setPendingConversationTurn] =
    useState<PendingConversationTurn>();
  const [runOptionsPanel, setRunOptionsPanel] = useState<RunOptionsPanel>();
  const [opfsFiles, setOpfsFiles] = useState<OpfsWorkspaceFile[]>([]);
  const [expandedOpfsProjects, setExpandedOpfsProjects] =
    useState<Set<string>>();
  const [selectedOpfsPaths, setSelectedOpfsPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [opfsLoading, setOpfsLoading] = useState(false);
  const [opfsError, setOpfsError] = useState<string>();
  const [cleanupConfirmation, setCleanupConfirmation] = useState('');
  const [cleanupPending, setCleanupPending] = useState(false);
  const [opfsPreview, setOpfsPreview] = useState<{
    file: OpfsWorkspaceFile;
    model?: ViewerModel;
    text?: string;
  }>();
  const [viewerToolbarTarget, setViewerToolbarTarget] =
    useState<HTMLDivElement | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<RuntimeActivityEvent[]>(
    [],
  );
  const [resizingSide, setResizingSide] = useState<ResizeSide>();
  const [resizingLog, setResizingLog] = useState(false);
  const [pendingReview, setPendingReview] = useState<PendingVisualReview>();
  const [activityFailure, setActivityFailure] = useState<ActivityFailure>();
  const [runStartedAt, setRunStartedAt] = useState<number>();
  const [runFinishedAt, setRunFinishedAt] = useState<number>();
  const contextRef = useRef<RunContext | undefined>(undefined);
  const activeContextRef = useRef<RunContext | undefined>(undefined);
  const activeRunIdRef = useRef<string | undefined>(undefined);
  const controllerRef = useRef<CadAgentProjectController | undefined>(
    undefined,
  );
  const executorRef = useRef<
    { cacheKey: string; executor: BrowserCadExecutor } | undefined
  >(undefined);
  const buildQueueRef = useRef<
    LatestBuildQueue<ParameterSet, CadWorkspaceSnapshot> | undefined
  >(undefined);
  const failedToolCallRef = useRef<string | undefined>(undefined);
  const retryEffectSignatureRef = useRef<string | undefined>(undefined);
  const streamFailureRef = useRef<string | undefined>(undefined);
  const completedEffectSignatureRef = useRef<string | undefined>(undefined);
  const restoreEffectCompletedRef = useRef(false);
  const rebuildSequenceRef = useRef(0);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const centerPanelRef = useRef<HTMLElement>(null);
  const conversationRef = useRef<HTMLElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const executionMenuRef = useRef<HTMLDivElement>(null);
  const runOptionsPopoverRef = useRef<HTMLDivElement>(null);
  const runOptionsTriggerRef = useRef<HTMLDivElement>(null);
  const activeResizeRef = useRef<ActiveResize | undefined>(undefined);
  const activeLogResizeRef = useRef<ActiveLogResize | undefined>(undefined);
  const cleanupConfirmRef = useRef<HTMLDivElement>(null);
  const runtimeEventSequenceRef = useRef(0);
  const lastStatusUpdateRef = useRef(0);

  const transport = useMemo(
    () =>
      new DefaultChatTransport<CadUiMessage>({
        api: '/api/cad-agent',
        body: () => ({ runContext: contextRef.current }),
      }),
    [],
  );

  const {
    addToolOutput,
    clearError,
    error,
    messages,
    sendMessage,
    setMessages,
    status,
    stop,
  } = useChat<CadUiMessage>({
    transport,
    // Limit re-renders of this large workbench during agent streaming. The
    // default updates React on every streamed chunk, which serializes the
    // whole conversation and writes it to OPFS on each change.
    throttle: 150,
    sendAutomaticallyWhen: shouldContinueCadAgent,
    onError: (chatError) => {
      const current = contextRef.current;
      const controller = controllerRef.current;
      const reason = chatError.message || 'CAD Agent request failed.';
      if (
        current === undefined ||
        controller === undefined ||
        ['completed', 'failed', 'cancelled'].includes(current.phase) ||
        streamFailureRef.current === reason
      ) {
        return;
      }
      streamFailureRef.current = reason;
      setActivityFailure({
        phase: current.phase,
        reason,
        tool: expectedToolForPhase(current),
      });
      contextRef.current = { ...current, phase: 'failed' };
      activeContextRef.current = contextRef.current;
      setRunFinishedAt((finishedAt) => finishedAt ?? Date.now());
      setStatusMessage(reason);
      void controller.fail(reason).catch((failureError: unknown) => {
        setStatusMessage(
          failureError instanceof Error
            ? `${reason} Failed to persist terminal state: ${failureError.message}`
            : `${reason} Failed to persist terminal state.`,
        );
      });
    },
    onToolCall: async ({ toolCall }) => {
      if (toolCall.dynamic) return;
      const controller = controllerRef.current;
      if (controller === undefined) return;
      try {
        const output = await controller.handleToolCall(
          toolCall.toolName,
          toolCall.input,
        );
        const state = controller.coordinator.state;
        const current = contextRef.current;
        if (current !== undefined) {
          contextRef.current = {
            ...current,
            phase: activeAgentPhase(state.status),
          };
          activeContextRef.current = contextRef.current;
        }
        setModel(controller.viewerModel());
        const snapshot = await controller.workspaceSnapshot();
        setWorkspaceSnapshot(snapshot);
        setParameters(snapshot.parameters);
        setStatusMessage(
          state.status === 'completed'
            ? 'Run completed and immutable artifacts were saved to OPFS.'
            : `${toolCall.toolName} finished; ${state.status.replaceAll('_', ' ')}.`,
        );
        addToolOutput({
          tool: toolCall.toolName,
          toolCallId: toolCall.toolCallId,
          output,
        });
      } catch (toolError) {
        setStatusMessage(
          toolError instanceof Error ? toolError.message : 'CAD tool failed.',
        );
        addToolOutput({
          tool: toolCall.toolName,
          toolCallId: toolCall.toolCallId,
          state: 'output-error',
          errorText:
            toolError instanceof Error ? toolError.message : 'CAD tool failed.',
        });
      }
    },
  });

  useEffect(() => {
    const controller = controllerRef.current;
    if (
      controller === undefined ||
      messages.length === 0 ||
      selectedRunId === undefined ||
      selectedRunId !== activeRunIdRef.current
    ) {
      return;
    }
    // Persisting on every streamed chunk re-serializes the entire
    // conversation (including multi-MB tool inputs) and writes it twice to
    // OPFS each time, which freezes and crashes the tab during a long run.
    // Instead, persist immediately once the stream is idle or after a
    // trailing debounce while chunks are still arriving.
    const persist = (latest: CadUiMessage[]) => {
      void controller.saveMessages(latest).catch((saveError: unknown) => {
        setStatusMessage(
          saveError instanceof Error
            ? saveError.message
            : 'Chat persistence failed.',
        );
      });
    };
    if (status === 'ready' || status === 'error') {
      persist(messages);
      return;
    }
    const timer = globalThis.setTimeout(() => persist(messages), 1_000);
    return () => globalThis.clearTimeout(timer);
  }, [messages, status, selectedRunId]);

  useEffect(() => {
    const current = contextRef.current;
    const finalMessage = messages.findLast(
      (message) =>
        message.role === 'assistant' &&
        message.parts.some(
          (part) => part.type === 'text' && part.text.trim().length > 0,
        ),
    );
    if (
      current?.phase !== 'completed' ||
      status !== 'ready' ||
      finalMessage === undefined
    ) {
      return;
    }
    const completionSignature = `${current.runId}:${finalMessage.id}`;
    if (completedEffectSignatureRef.current === completionSignature) return;
    completedEffectSignatureRef.current = completionSignature;
    setRunFinishedAt((finishedAt) => finishedAt ?? Date.now());
    void listCadAgentProjects()
      .then(setProjectRecords)
      .catch(() => undefined);
  }, [messages, status]);

  useEffect(() => {
    const toolFailure = cadAgentToolErrorState(messages);
    if (toolFailure === undefined) {
      retryEffectSignatureRef.current = undefined;
      return;
    }
    const retrySignature = `${toolFailure.toolCallId}:${String(toolFailure.consecutiveErrors)}:${toolFailure.halted ? 'halted' : 'retrying'}:${toolFailure.errorText}`;
    if (retryEffectSignatureRef.current === retrySignature) return;

    if (!toolFailure.halted) {
      retryEffectSignatureRef.current = retrySignature;
      setStatusMessage(
        `${toolFailure.toolName} failed: ${toolFailure.errorText} Retrying (${String(toolFailure.consecutiveErrors)}/${String(MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS)})…`,
      );
      return;
    }

    const current = contextRef.current;
    const controller = controllerRef.current;
    if (
      current === undefined ||
      controller === undefined ||
      ['completed', 'failed', 'cancelled'].includes(current.phase) ||
      failedToolCallRef.current === toolFailure.toolCallId
    ) {
      return;
    }

    retryEffectSignatureRef.current = retrySignature;
    failedToolCallRef.current = toolFailure.toolCallId;
    stop();
    const reason = `${toolFailure.toolName} failed ${String(toolFailure.consecutiveErrors)} consecutive ${toolFailure.consecutiveErrors === 1 ? 'time' : 'times'}: ${toolFailure.errorText}`;
    setActivityFailure({
      phase: current.phase,
      reason,
      tool: toolFailure.toolName,
    });
    contextRef.current = { ...current, phase: 'failed' };
    activeContextRef.current = contextRef.current;
    setRunFinishedAt((finishedAt) => finishedAt ?? Date.now());
    setStatusMessage(reason);
    void controller.fail(reason).catch((failureError: unknown) => {
      setStatusMessage(
        failureError instanceof Error
          ? `${reason} Failed to persist terminal state: ${failureError.message}`
          : `${reason} Failed to persist terminal state.`,
      );
    });
  }, [messages, stop]);

  useEffect(() => {
    if (restoreEffectCompletedRef.current) return;
    let active = true;
    void (async () => {
      let bundledError: unknown;
      try {
        await ensureBundledPomodoroProject();
      } catch (error) {
        bundledError = error;
      }
      const records = await listCadAgentProjects();
      const initialProjectId = selectInitialProjectId(records);
      if (initialProjectId === undefined && bundledError !== undefined) {
        throw bundledError;
      }
      let restored =
        initialProjectId === undefined
          ? undefined
          : await loadCadAgentProject(initialProjectId);
      if (
        restored !== undefined &&
        isBundledPomodoroProject(initialProjectId)
      ) {
        const manifestModel = await loadBundledPomodoroViewerModel().catch(
          () => undefined,
        );
        if (manifestModel !== undefined) {
          restored = {
            ...restored,
            model: manifestModel,
            workspace: { ...restored.workspace, model: manifestModel },
          };
        }
      }
      return { initialProjectId, records, restored };
    })()
      .then(async ({ initialProjectId, records, restored }) => {
        if (!active) return;
        setProjectRecords(records);
        if (restored === undefined) {
          restoreEffectCompletedRef.current = true;
          return;
        }
        const validated =
          restored.messages.length === 0
            ? []
            : await validateUIMessages<CadUiMessage>({
                messages: restored.messages,
                tools: createCadTools(restored.workflowKind),
              });
        if (!active) return;
        restoreEffectCompletedRef.current = true;
        setMessages(validated);
        setSelectedProjectId(restored.projectId);
        if (isBundledPomodoroProject(initialProjectId)) {
          setSelectedFileId(
            bundledPomodoroPreviewFileId(restored.workspace.artifacts),
          );
          setOpfsPreview(undefined);
        }
        activeRunIdRef.current = restored.runId;
        setSelectedRunId(restored.runId);
        setStarted(true);
        setRunStartedAt(undefined);
        setRunFinishedAt(undefined);
        setActivityFailure(
          restored.phase === 'failed' && restored.failureReason !== undefined
            ? {
                phase: 'failed',
                reason: restored.failureReason,
                tool: undefined,
              }
            : undefined,
        );
        setResearch(restored.research);
        setModel(restored.model);
        setWorkspaceSnapshot(restored.workspace);
        setParameters(restored.workspace.parameters);
        const controller = await CadAgentProjectController.restore({
          projectId: restored.projectId,
          executor: pageExecutor(),
          requestVisualReview,
        });
        if (!active) return;
        controllerRef.current = controller;
        const restoredModelSettings = await controller?.modelProfileSettings();
        if (restoredModelSettings !== undefined) {
          setModelSettings(restoredModelSettings);
          setSelectedModelProfileId(
            restoredModelSettings.defaultProfileId ?? undefined,
          );
        }
        contextRef.current = {
          runId: restored.runId,
          workflowKind: restored.workflowKind,
          phase: restored.phase,
          userRequest: 'Restored local CAD Agent run.',
          visualReviewConsent: 'declined',
          ...(restored.research === undefined
            ? {}
            : { research: restored.research }),
        };
        activeContextRef.current = contextRef.current;
        setStatusMessage(
          restored.phase === 'failed' && restored.failureReason !== undefined
            ? restored.failureReason
            : `Validated and restored ${restored.phase} run ${restored.runId} from OPFS.`,
        );
      })
      .catch((restoreError: unknown) => {
        if (active) {
          restoreEffectCompletedRef.current = true;
          setStatusMessage(
            restoreError instanceof Error
              ? `Stored run failed validation: ${restoreError.message}`
              : 'Stored run failed validation.',
          );
        }
      });
    return () => {
      active = false;
    };
  }, [setMessages]);

  useEffect(
    () => () => {
      controllerRef.current?.dispose();
      buildQueueRef.current?.dispose();
      executorRef.current?.executor.dispose();
    },
    [],
  );

  const refreshOpfsFiles = useCallback(async () => {
    setOpfsLoading(true);
    setOpfsError(undefined);
    try {
      await ensureBundledPomodoroProject();
      const nextFiles = (await listOpfsWorkspaceFiles()).filter(
        ({ projectId, runId }) =>
          !isBundledPomodoroProject(projectId) ||
          runId === BUNDLED_POMODORO_RUN_ID,
      );
      setOpfsFiles(nextFiles);
      const available = new Set(
        nextFiles
          .filter(({ projectId }) => !isBundledPomodoroProject(projectId))
          .map(({ path }) => path),
      );
      setSelectedOpfsPaths(
        (current) =>
          new Set([...current].filter((path) => available.has(path))),
      );
      setOpfsPreview((current) =>
        current === undefined || available.has(current.file.path)
          ? current
          : undefined,
      );
    } catch (storageError) {
      setOpfsError(
        storageError instanceof Error
          ? storageError.message
          : 'Unable to inspect browser OPFS.',
      );
    } finally {
      setOpfsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!storageOpen) return;
    void refreshOpfsFiles();
  }, [refreshOpfsFiles, storageOpen]);

  useEffect(() => {
    if (leftView !== 'chat') return;
    const conversation = conversationRef.current;
    if (conversation !== null) {
      conversation.scrollTop = conversation.scrollHeight;
    }
  }, [activityFailure, leftView, messages, research, status, statusMessage]);

  useEffect(() => {
    if (leftView !== 'chat') {
      setRunOptionsPanel(undefined);
    }
  }, [leftView]);

  useEffect(() => {
    if (runOptionsPanel === undefined) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setRunOptionsPanel(undefined);
    };
    globalThis.addEventListener('keydown', closeOnEscape);
    return () => globalThis.removeEventListener('keydown', closeOnEscape);
  }, [runOptionsPanel]);

  useEffect(() => {
    if (!historyOpen && runOptionsPanel === undefined) return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;

      if (historyOpen && !executionMenuRef.current?.contains(target)) {
        setHistoryOpen(false);
      }

      if (
        runOptionsPanel !== undefined &&
        !runOptionsPopoverRef.current?.contains(target) &&
        !runOptionsTriggerRef.current?.contains(target)
      ) {
        setRunOptionsPanel(undefined);
      }
    };

    document.addEventListener('pointerdown', closeOnOutsidePointer);
    return () =>
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
  }, [historyOpen, runOptionsPanel]);

  useEffect(() => {
    const stored = globalThis.localStorage?.getItem(
      'amagine3d-workspace-layout-v2',
    );
    if (stored === null || stored === undefined) return;
    try {
      const value = JSON.parse(stored) as Record<string, unknown>;
      if (typeof value.leftCollapsed === 'boolean') {
        setLeftCollapsed(value.leftCollapsed);
      }
      if (typeof value.rightCollapsed === 'boolean') {
        setRightCollapsed(value.rightCollapsed);
      }
      if (typeof value.leftWidth === 'number') {
        setLeftWidth(clamp(value.leftWidth, LEFT_PANEL_MIN, LEFT_PANEL_MAX));
      }
      if (typeof value.rightWidth === 'number') {
        setRightWidth(
          clamp(value.rightWidth, RIGHT_PANEL_MIN, RIGHT_PANEL_MAX),
        );
      }
      if (typeof value.logCollapsed === 'boolean') {
        setLogCollapsed(value.logCollapsed);
      }
      if (typeof value.logHeight === 'number') {
        setLogHeight(clamp(value.logHeight, LOG_PANEL_MIN, LOG_PANEL_MAX));
      }
      if (value.leftView === 'chat' || value.leftView === 'files') {
        setLeftView(value.leftView);
      }
      if (typeof value.selectedFileId === 'string') {
        setSelectedFileId(value.selectedFileId);
      }
    } catch {
      globalThis.localStorage?.removeItem('amagine3d-workspace-layout-v2');
    }
  }, []);

  useEffect(() => {
    globalThis.localStorage?.setItem(
      'amagine3d-workspace-layout-v2',
      JSON.stringify({
        leftCollapsed,
        rightCollapsed,
        leftWidth,
        rightWidth,
        logCollapsed,
        logHeight,
        leftView,
        selectedFileId,
      }),
    );
  }, [
    leftCollapsed,
    leftView,
    leftWidth,
    logCollapsed,
    logHeight,
    rightCollapsed,
    rightWidth,
    selectedFileId,
  ]);

  useEffect(() => {
    if (writebackPreview === undefined || parameters === undefined) return;
    const controller = controllerRef.current;
    if (controller === undefined) return;
    try {
      setWritebackPreview(controller.prepareSourceWriteback(parameters));
    } catch (writebackError) {
      setWritebackPreview(undefined);
      setStatusMessage(
        writebackError instanceof Error
          ? writebackError.message
          : 'Source writeback preview failed.',
      );
    }
  }, [parameters]);

  const pageExecutor = (): BrowserCadExecutor => {
    if (executorRef.current?.cacheKey !== CAD_RUNTIME_MANIFEST.cacheKey) {
      executorRef.current?.executor.dispose();
      executorRef.current = {
        cacheKey: CAD_RUNTIME_MANIFEST.cacheKey,
        executor: createBrowserCadExecutor({
          onEvent: (executorEvent) => {
            runtimeEventSequenceRef.current += 1;
            // The Activity log surfaces build milestones and failures only.
            // Info-level stdout lines are mostly model-script print dumps that
            // drown the log; they still reach the throttled status message.
            if (
              executorEvent.type === 'progress' ||
              (executorEvent.type === 'log' &&
                (executorEvent.level === 'warning' ||
                  executorEvent.level === 'error'))
            ) {
              const activityEvent: RuntimeActivityEvent = {
                id: runtimeEventSequenceRef.current,
                occurredAt: Date.now(),
                event: executorEvent,
              };
              setRuntimeEvents((current) => [
                ...current.slice(-99),
                activityEvent,
              ]);
            }
            if (executorEvent.type === 'progress') {
              setStatusMessage(executorEvent.message);
              return;
            }
            const line = executorEvent.line.trim();
            if (line.length === 0) return;
            // Build logs can emit hundreds of lines per second. Throttle the
            // status-message re-renders so a noisy Python script does not
            // force the whole workbench (viewer included) to repaint per line.
            // Error lines always surface so a failing build is never hidden.
            const now = Date.now();
            if (
              executorEvent.level !== 'error' &&
              now - lastStatusUpdateRef.current < 250
            ) {
              return;
            }
            lastStatusUpdateRef.current = now;
            setStatusMessage(
              `CAD runtime: ${line.slice(0, 240)}${line.length > 240 ? '…' : ''}`,
            );
          },
        }),
      };
    }
    return executorRef.current.executor;
  };

  const requestVisualReview = (
    input: VisualReviewInput,
  ): Promise<VisualReviewDecision> => {
    setModel(controllerRef.current?.viewerModel());
    setStatusMessage('Visual review is waiting for your decision.');
    return new Promise((resolve) => {
      setPendingReview({ input, resolve });
    });
  };

  const attachmentFileParts = () =>
    toFileUIParts(
      imageAttachments.map((attachment) => ({
        name: attachment.fileName,
        type: attachment.mediaType,
        bytes: attachment.bytes,
      })),
    );

  const handleImageSelection = async (files: FileList | null) => {
    if (files === null) return;
    const inputs: ImageInput[] = [];
    for (const file of Array.from(files)) {
      inputs.push({
        fileName: file.name,
        mediaType: file.type,
        bytes: new Uint8Array(await file.arrayBuffer()),
      });
    }
    validateImageInputs(inputs);
    setImageAttachments((current) => [...current, ...inputs].slice(0, 4));
  };

  const handleImageDrop = (event: React.DragEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    void handleImageSelection(event.dataTransfer.files);
  };

  const handleImagePaste = (
    event: React.ClipboardEvent<HTMLTextAreaElement>,
  ) => {
    const files = event.clipboardData.files;
    if (files.length > 0) void handleImageSelection(files);
  };

  const imagePreviewUrl = (input: ImageInput): string => {
    let binary = '';
    for (const byte of input.bytes) binary += String.fromCharCode(byte);
    return `data:${input.mediaType};base64,${globalThis.btoa(binary)}`;
  };

  const updateModelSettings = async (next: ModelProfileSettings) => {
    setModelSettings(next);
    const controller = controllerRef.current;
    if (controller !== undefined) {
      await controller.saveModelProfileSettings(next);
    } else {
      const repository = await OpfsProjectRepository.open();
      await repository.saveModelProfileSettings(next);
    }
  };

  const registerServerModelProfile = async (profile: ModelProfile) => {
    const response = await fetch('/api/model-profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: profile.id,
        modelId: profile.modelId,
        connectionId: profile.connectionId,
        provider: profile.provider,
        capabilities: profile.capabilities,
      }),
    });
    if (!response.ok)
      throw new Error(
        'The model profile could not be registered on the server.',
      );
  };

  const validateServerModelProfile = async (profile: ModelProfile) => {
    setStatusMessage(
      `Validating ${profile.displayName} with text and tool-calling smoke…`,
    );
    try {
      const response = await fetch('/api/model-profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: profile.id,
          modelId: profile.modelId,
          connectionId: profile.connectionId,
          provider: profile.provider,
          capabilities: profile.capabilities,
          action: 'validate',
        }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => undefined)) as
          { error?: unknown } | undefined;
        throw new Error(
          typeof body?.error === 'string'
            ? body.error
            : 'Provider smoke failed.',
        );
      }
      const validated = {
        ...profile,
        revision: profile.revision + 1,
        validation: {
          status: 'valid' as const,
          validatedAt: new Date().toISOString(),
          reason: null,
          sdkVersion: 'ai-sdk-7',
        },
      };
      await updateModelSettings({
        schemaVersion: 1,
        defaultProfileId: modelSettings?.defaultProfileId ?? null,
        profiles: (modelSettings?.profiles ?? []).map((item) =>
          item.id === profile.id ? validated : item,
        ),
      });
      setStatusMessage(
        `${profile.displayName} passed text and tool-calling smoke.`,
      );
    } catch (error) {
      const failed = {
        ...profile,
        revision: profile.revision + 1,
        validation: {
          status: 'failed' as const,
          validatedAt: new Date().toISOString(),
          reason:
            error instanceof Error ? error.message : 'Provider smoke failed.',
          sdkVersion: 'ai-sdk-7',
        },
      };
      await updateModelSettings({
        schemaVersion: 1,
        defaultProfileId: modelSettings?.defaultProfileId ?? null,
        profiles: (modelSettings?.profiles ?? []).map((item) =>
          item.id === profile.id ? failed : item,
        ),
      });
      setStatusMessage(failed.validation.reason ?? 'Provider smoke failed.');
    }
  };

  const addModelProfile = () => {
    setAddModelDialogDraft({
      modelId: '',
      displayName: '',
      imageInput: false,
    });
    setAddModelDialogError(undefined);
    setAddModelDialogOpen(true);
  };

  const submitAddModelProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const modelId = addModelDialogDraft.modelId.trim();
    const displayName = addModelDialogDraft.displayName.trim() || modelId;
    if (!modelId) {
      setAddModelDialogError('Enter a model ID before saving.');
      return;
    }
    setAddModelDialogSaving(true);
    setAddModelDialogError(undefined);
    const profile: ModelProfile = {
      schemaVersion: 1,
      id: crypto.randomUUID(),
      revision: 1,
      displayName,
      connectionId: 'gateway',
      provider: 'openai-compatible',
      modelId,
      defaultParameters: {},
      capabilities: {
        textInput: true,
        imageInput: addModelDialogDraft.imageInput,
        toolCalling: true,
        reasoning: false,
      },
      enabled: true,
      validation: {
        status: 'pending',
        validatedAt: null,
        reason: 'Not validated yet.',
        sdkVersion: null,
      },
    };
    try {
      await registerServerModelProfile(profile);
      await updateModelSettings({
        schemaVersion: 1,
        defaultProfileId: modelSettings?.defaultProfileId ?? null,
        profiles: [...(modelSettings?.profiles ?? []), profile],
      });
      setSelectedModelProfileId(profile.id);
      setAddModelDialogOpen(false);
      setStatusMessage(`${displayName} was added and is ready for validation.`);
    } catch (error) {
      setAddModelDialogError(
        error instanceof Error
          ? error.message
          : 'The model profile could not be added.',
      );
    } finally {
      setAddModelDialogSaving(false);
    }
  };

  const toggleModelProfile = async (profile: ModelProfile) => {
    if (profile.validation.status !== 'valid') {
      setStatusMessage(
        'This model profile must pass its provider smoke before it can run.',
      );
      return;
    }
    await updateModelSettings({
      schemaVersion: 1,
      defaultProfileId: modelSettings?.defaultProfileId ?? null,
      profiles: (modelSettings?.profiles ?? []).map((item) =>
        item.id === profile.id
          ? { ...item, enabled: !item.enabled, revision: item.revision + 1 }
          : item,
      ),
    });
  };

  const editModelProfile = async (profile: ModelProfile) => {
    const displayName = globalThis
      .prompt('Display name', profile.displayName)
      ?.trim();
    if (!displayName || displayName === profile.displayName) return;
    await updateModelSettings({
      schemaVersion: 1,
      defaultProfileId: modelSettings?.defaultProfileId ?? null,
      profiles: (modelSettings?.profiles ?? []).map((item) =>
        item.id === profile.id
          ? { ...item, displayName, revision: item.revision + 1 }
          : item,
      ),
    });
  };

  const deleteModelProfile = async (profile: ModelProfile) => {
    if (
      !globalThis.confirm(
        `Delete ${profile.displayName}? Historical run snapshots are retained.`,
      )
    )
      return;
    await updateModelSettings({
      schemaVersion: 1,
      defaultProfileId:
        modelSettings?.defaultProfileId === profile.id
          ? null
          : (modelSettings?.defaultProfileId ?? null),
      profiles: (modelSettings?.profiles ?? []).filter(
        (item) => item.id !== profile.id,
      ),
    });
    if (selectedModelProfileId === profile.id)
      setSelectedModelProfileId(undefined);
    await fetch(`/api/model-profiles?id=${encodeURIComponent(profile.id)}`, {
      method: 'DELETE',
    });
  };

  const setDefaultModelProfile = async (profile: ModelProfile) => {
    if (profile.validation.status !== 'valid' || !profile.enabled) return;
    await updateModelSettings({
      schemaVersion: 1,
      defaultProfileId: profile.id,
      profiles: modelSettings?.profiles ?? [],
    });
    setSelectedModelProfileId(profile.id);
  };

  const selectProject = async (projectId: string) => {
    setHistoryOpen(false);
    let restored = await loadCadAgentProject(projectId);
    if (restored === undefined) {
      setStatusMessage('This project could not be restored.');
      return;
    }
    if (isBundledPomodoroProject(projectId)) {
      const manifestModel = await loadBundledPomodoroViewerModel().catch(
        () => undefined,
      );
      if (manifestModel !== undefined) {
        restored = {
          ...restored,
          model: manifestModel,
          workspace: { ...restored.workspace, model: manifestModel },
        };
      }
    }
    const validated =
      restored.messages.length === 0
        ? []
        : await validateUIMessages<CadUiMessage>({
            messages: restored.messages,
            tools: createCadTools(restored.workflowKind),
          });
    setMessages(validated);
    setWorkspaceSnapshot(restored.workspace);
    setParameters(restored.workspace.parameters);
    setModel(restored.model);
    setResearch(restored.research);
    setSelectedFileId(
      isBundledPomodoroProject(projectId)
        ? bundledPomodoroPreviewFileId(restored.workspace.artifacts)
        : 'model:preview',
    );
    setLeftView('chat');
    setSelectedProjectId(projectId);
    setSelectedRunId(restored.runId);
    setStarted(true);
    setRunStartedAt(undefined);
    setRunFinishedAt(undefined);
    setActivityFailure(
      restored.phase === 'failed' && restored.failureReason !== undefined
        ? {
            phase: 'failed',
            reason: restored.failureReason,
            tool: undefined,
          }
        : undefined,
    );
    setPendingReview(undefined);
    const previous = controllerRef.current;
    const controller = await CadAgentProjectController.restore({
      projectId,
      executor: pageExecutor(),
      requestVisualReview,
    });
    previous?.dispose();
    controllerRef.current = controller;
    activeRunIdRef.current = restored.runId;
    contextRef.current = {
      runId: restored.runId,
      workflowKind: restored.workflowKind,
      phase: restored.phase,
      userRequest: 'Continue the stored CAD project.',
      visualReviewConsent: 'declined',
      ...(restored.research === undefined
        ? {}
        : { research: restored.research }),
    };
    activeContextRef.current = contextRef.current;
    setStatusMessage(
      restored.phase === 'failed' && restored.failureReason !== undefined
        ? restored.failureReason
        : 'Project restored. Continue the conversation below.',
    );
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submittedPrompt = prompt.trim();
    if (submittedPrompt.length === 0) return;
    setRunOptionsPanel(undefined);
    const submissionPlan = planCadChatSubmission({
      started,
      runActive,
      readOnlyProject: isBundledPomodoroProject(selectedProjectId),
      hasController: controllerRef.current !== undefined,
      ...(selectedProjectId === undefined ? {} : { selectedProjectId }),
      ...(contextRef.current === undefined
        ? {}
        : { current: contextRef.current }),
      ...(workspaceSnapshot === undefined
        ? {}
        : { workspace: workspaceSnapshot }),
    });
    if (submissionPlan.kind === 'blocked') {
      setStatusMessage(
        'The current chat could not be matched to its project. Restore it from project history or start a new project explicitly.',
      );
      return;
    }
    const selectedProfile = modelSettings?.profiles.find(
      (profile) => profile.id === selectedModelProfileId,
    );
    const modelSnapshot =
      selectedProfile?.validation.status === 'valid'
        ? freezeModelProfile(selectedProfile)
        : undefined;
    const researchRequested = searchEnabled;
    const visualReviewConsent = visualReviewApproved
      ? ('approved' as const)
      : ('declined' as const);
    if (selectedProfile !== undefined)
      await registerServerModelProfile(selectedProfile);
    if (
      imageAttachments.length > 0 &&
      selectedProfile !== undefined &&
      (selectedProfile.validation.status !== 'valid' ||
        !selectedProfile.capabilities.imageInput)
    ) {
      setStatusMessage(
        'The selected model has not been validated for image input. Choose a validated vision tool model first.',
      );
      return;
    }
    setRunPreparing(true);
    try {
      if (
        submissionPlan.kind === 'continue-project' &&
        workspaceSnapshot !== undefined
      ) {
        const { baseRevisionId, mode, parentRunId, projectId } = submissionPlan;
        const previous = controllerRef.current;
        if (previous === undefined) return;
        const runId = crypto.randomUUID();
        const pendingTurn: PendingConversationTurn = {
          id: runId,
          text: submittedPrompt,
          attachments: imageAttachments.map((attachment) => ({
            fileName: attachment.fileName,
            url: imagePreviewUrl(attachment),
          })),
          research: {
            status: researchRequested ? 'running' : 'skipped',
          },
        };
        setPendingConversationTurn(pendingTurn);
        failedToolCallRef.current = undefined;
        streamFailureRef.current = undefined;
        setActivityFailure(undefined);
        clearError();
        setRunStartedAt(Date.now());
        setRuntimeEvents([]);
        setRunFinishedAt(undefined);
        setPendingReview(undefined);
        setStatusMessage(
          researchRequested
            ? 'Running Web Research for this continuation…'
            : mode === 'modification'
              ? 'Preparing the modification workflow…'
              : 'Preparing the continuation workflow…',
        );
        let packet: ResearchPacket | undefined;
        let researchStage: ResearchStage = { status: 'skipped' };
        if (researchRequested) {
          packet = await runResearchWithRetry(
            runId,
            submittedPrompt,
            (attempt, total) => {
              setPendingConversationTurn((current) =>
                current?.id === runId && current.research.status === 'running'
                  ? {
                      ...current,
                      research: { status: 'running', attempt, total },
                    }
                  : current,
              );
              setStatusMessage(
                attempt > 1
                  ? `Web Research failed; retrying (${String(attempt)}/${String(total)})…`
                  : 'Running Web Research for this continuation…',
              );
            },
          );
          researchStage =
            packet === undefined || packet.status === 'failed'
              ? { status: 'failed' }
              : { status: 'completed', packet };
        }
        setPendingConversationTurn((current) =>
          current?.id === runId
            ? { ...current, research: researchStage }
            : current,
        );
        setResearch(packet);
        const controller = await CadAgentProjectController.open({
          projectId,
          runId,
          userRequest: submittedPrompt,
          preference: workspaceSnapshot.workflowKind,
          researchEnabled: researchRequested,
          visualReviewConsent,
          ...(packet === undefined ? {} : { research: packet }),
          parentRunId,
          ...(baseRevisionId === undefined ? {} : { baseRevisionId }),
          ...(selectedModelProfileId === undefined
            ? {}
            : { modelProfileId: selectedModelProfileId }),
          ...(modelSnapshot === undefined ? {} : { modelSnapshot }),
          mode,
          requestVisualReview,
          executor: pageExecutor(),
        });
        previous.dispose();
        controllerRef.current = controller;
        activeRunIdRef.current = runId;
        setSelectedProjectId(projectId);
        setSelectedRunId(runId);
        setHistoryOpen(false);
        setProjectRecords((current) =>
          current.map((record) =>
            record.projectId === projectId
              ? {
                  ...record,
                  runId,
                  updatedAt: new Date().toISOString(),
                  status: 'active',
                  workflowKind: controller.coordinator.workflowKind,
                }
              : record,
          ),
        );
        const nextSnapshot = await controller.workspaceSnapshot();
        setWorkspaceSnapshot(nextSnapshot);
        setModel(undefined);
        contextRef.current = {
          runId,
          workflowKind: workspaceSnapshot.workflowKind,
          phase: 'briefing',
          userRequest: submittedPrompt,
          visualReviewConsent,
          ...(selectedModelProfileId === undefined
            ? {}
            : { modelProfileId: selectedModelProfileId }),
          ...(packet === undefined ? {} : { research: packet }),
          ...(mode === 'modification' && workspaceSnapshot.source !== undefined
            ? {
                modificationContext: {
                  source: workspaceSnapshot.source,
                  designBrief: workspaceSnapshot.designBrief ?? {},
                  parameterSchema: workspaceSnapshot.parameters ?? {},
                  ...(workspaceSnapshot.qaReport === undefined
                    ? {}
                    : { latestQa: workspaceSnapshot.qaReport }),
                },
              }
            : {}),
        };
        activeContextRef.current = contextRef.current;
        setStatusMessage(
          mode === 'modification'
            ? `${controller.coordinator.workflowKind} modification selected: ${controller.coordinator.selection.reason}`
            : `${controller.coordinator.workflowKind} continuation selected: ${controller.coordinator.selection.reason}`,
        );
        const files = attachmentFileParts();
        if (imageAttachments.length > 0)
          await controller.saveImageAttachments(imageAttachments);
        setPrompt('');
        const sendRequest = sendMessage({
          text: submittedPrompt,
          ...(files.length === 0 ? {} : { files }),
          metadata: { runId, research: researchStage },
        });
        setPendingConversationTurn(undefined);
        setImageAttachments([]);
        setRunPreparing(false);
        await sendRequest;
        return;
      }
      const projectId = `cad-${crypto.randomUUID()}`;
      const runId = crypto.randomUUID();
      const projectName = executionDisplayTitle(
        submittedPrompt,
        text('Untitled project', '未命名项目'),
        80,
      );
      setPendingConversationTurn({
        id: runId,
        text: submittedPrompt,
        attachments: imageAttachments.map((attachment) => ({
          fileName: attachment.fileName,
          url: imagePreviewUrl(attachment),
        })),
        research: { status: researchRequested ? 'running' : 'skipped' },
      });
      failedToolCallRef.current = undefined;
      streamFailureRef.current = undefined;
      setActivityFailure(undefined);
      clearError();
      setStarted(true);
      setRunStartedAt(Date.now());
      setRuntimeEvents([]);
      setRunFinishedAt(undefined);
      setMessages([]);
      setModel(undefined);
      setResearch(undefined);
      setWorkspaceSnapshot(undefined);
      setParameters(undefined);
      setSelectedFileId('model:preview');
      setWritebackPreview(undefined);
      buildQueueRef.current?.dispose();
      buildQueueRef.current = undefined;
      setStatusMessage(
        researchRequested
          ? 'Running the isolated Web Research stage…'
          : 'Skipping Web Research and selecting a workflow…',
      );
      let packet: ResearchPacket | undefined;
      let researchStage: ResearchStage = { status: 'skipped' };
      if (researchRequested) {
        packet = await runResearchWithRetry(
          runId,
          submittedPrompt,
          (attempt, total) => {
            setPendingConversationTurn((current) =>
              current?.id === runId && current.research.status === 'running'
                ? {
                    ...current,
                    research: { status: 'running', attempt, total },
                  }
                : current,
            );
            setStatusMessage(
              attempt > 1
                ? `Web Research failed; retrying (${String(attempt)}/${String(total)})…`
                : 'Running the isolated Web Research stage…',
            );
          },
        );
        setResearch(packet);
        researchStage =
          packet === undefined || packet.status === 'failed'
            ? { status: 'failed' }
            : { status: 'completed', packet };
      }
      setPendingConversationTurn((current) =>
        current?.id === runId
          ? { ...current, research: researchStage }
          : current,
      );
      const previous = controllerRef.current;
      const controller = await CadAgentProjectController.open({
        projectId,
        projectName,
        runId,
        userRequest: submittedPrompt,
        preference,
        researchEnabled: researchRequested,
        visualReviewConsent,
        ...(packet === undefined ? {} : { research: packet }),
        requestVisualReview,
        executor: pageExecutor(),
        ...(selectedModelProfileId === undefined
          ? {}
          : { modelProfileId: selectedModelProfileId }),
        ...(modelSnapshot === undefined ? {} : { modelSnapshot }),
      });
      previous?.dispose();
      controllerRef.current = controller;
      const settings = await controller.modelProfileSettings();
      if (settings !== undefined) setModelSettings(settings);
      activeRunIdRef.current = runId;
      setSelectedProjectId(projectId);
      setSelectedRunId(runId);
      setHistoryOpen(false);
      setProjectRecords((current) => [
        {
          projectId,
          runId,
          title: projectName,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          status: 'active',
          workflowKind: controller.coordinator.workflowKind,
        },
        ...current.filter((record) => record.projectId !== projectId),
      ]);
      const initialSnapshot = await controller.workspaceSnapshot();
      setWorkspaceSnapshot(initialSnapshot);
      const initialContext: RunContext = {
        runId,
        workflowKind: controller.coordinator.workflowKind,
        phase: 'briefing',
        userRequest: submittedPrompt,
        visualReviewConsent,
        ...(selectedModelProfileId === undefined
          ? {}
          : { modelProfileId: selectedModelProfileId }),
        ...(packet === undefined ? {} : { research: packet }),
      };
      contextRef.current = initialContext;
      activeContextRef.current = initialContext;
      void listCadAgentProjects()
        .then((records) =>
          setProjectRecords(
            records.map((record) =>
              record.projectId === projectId
                ? { ...record, title: projectName }
                : record,
            ),
          ),
        )
        .catch(() => undefined);
      setStatusMessage(
        `${controller.coordinator.workflowKind} selected: ${controller.coordinator.selection.reason}`,
      );
      // The transport resolves contextRef for every automatic tool-loop request.
      // A per-send body would be recursively reused by AI SDK and keep the phase
      // frozen at briefing after saveDesignBrief completes.
      const files = attachmentFileParts();
      if (controller !== undefined && imageAttachments.length > 0) {
        await controller.saveImageAttachments(imageAttachments);
      }
      setPrompt('');
      const sendRequest = sendMessage({
        text: submittedPrompt,
        ...(files.length === 0 ? {} : { files }),
        metadata: { runId, research: researchStage },
      });
      setPendingConversationTurn(undefined);
      setImageAttachments([]);
      setRunPreparing(false);
      await sendRequest;
    } finally {
      setPendingConversationTurn(undefined);
      setRunPreparing(false);
    }
  };

  const decideVisualReview = (decision: VisualReviewDecision) => {
    const pending = pendingReview;
    if (pending === undefined) return;
    setPendingReview(undefined);
    setRunPreparing(false);
    pending.resolve(decision);
  };

  const cancel = async () => {
    stop();
    let persistenceWarning: string | undefined;
    try {
      await controllerRef.current?.cancel();
    } catch (cancelError) {
      persistenceWarning =
        cancelError instanceof Error
          ? `Run stopped, but its cancellation state could not be saved: ${cancelError.message}`
          : 'Run stopped, but its cancellation state could not be saved.';
    }
    const current = contextRef.current;
    if (current !== undefined) {
      contextRef.current = { ...current, phase: 'cancelled' };
      activeContextRef.current = contextRef.current;
    }
    setRunFinishedAt((finishedAt) => finishedAt ?? Date.now());
    setStatusMessage(
      persistenceWarning ??
        'Run cancelled; its recoverable state remains in OPFS.',
    );
  };

  const beginNewRun = () => {
    controllerRef.current?.dispose();
    controllerRef.current = undefined;
    contextRef.current = undefined;
    activeContextRef.current = undefined;
    activeRunIdRef.current = undefined;
    failedToolCallRef.current = undefined;
    streamFailureRef.current = undefined;
    setActivityFailure(undefined);
    clearError();
    setMessages([]);
    setResearch(undefined);
    setModel(undefined);
    setWorkspaceSnapshot(undefined);
    setParameters(undefined);
    setSelectedFileId('model:preview');
    setWritebackPreview(undefined);
    setPendingReview(undefined);
    setRunStartedAt(undefined);
    setRunFinishedAt(undefined);
    buildQueueRef.current?.dispose();
    buildQueueRef.current = undefined;
    setStarted(false);
    setSelectedProjectId(undefined);
    setSelectedRunId(undefined);
    setPendingConversationTurn(undefined);
    setHistoryOpen(false);
    setRunOptionsPanel(undefined);
    setStatusMessage('Ready for a new CAD project.');
  };

  const beginFreshRun = async () => {
    if (!started) return;
    const phase = contextRef.current?.phase;
    if (
      phase !== undefined &&
      !['completed', 'failed', 'cancelled'].includes(phase)
    ) {
      await cancel();
      beginNewRun();
      return;
    }
    beginNewRun();
  };

  const handleComposerKeyDown = (
    event: ReactKeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }
    event.preventDefault();
    if (composerAvailable && prompt.trim().length > 0) {
      event.currentTarget.form?.requestSubmit();
    }
  };

  useEffect(() => {
    const input = composerInputRef.current;
    if (input === null) return;
    input.style.height = 'auto';
    input.style.height = `${String(Math.min(input.scrollHeight, 224))}px`;
  }, [prompt]);

  const scheduleParameterBuild = (nextParameters: ParameterSet) => {
    const controller = controllerRef.current;
    if (controller === undefined) return;
    const changedNames = nextParameters.parameters
      .filter(
        (parameter) => !Object.is(parameter.value, parameter.defaultValue),
      )
      .map((parameter) => parameter.name);
    setParameters(nextParameters);
    setWorkspaceSnapshot((current) =>
      current === undefined
        ? current
        : { ...current, parameters: nextParameters },
    );
    buildQueueRef.current ??= new LatestBuildQueue({
      debounceMs: 400,
      execute: (queuedParameters, signal) =>
        controller.rebuildWithParameters(queuedParameters, signal),
    });
    rebuildSequenceRef.current += 1;
    const sequence = rebuildSequenceRef.current;
    setRebuilding(true);
    setParameterBuildIssue(undefined);
    setStatusMessage(
      'Parameter change queued; only the latest value will build.',
    );
    void buildQueueRef.current
      .submit(nextParameters)
      .then((snapshot) => {
        if (sequence !== rebuildSequenceRef.current) return;
        setWorkspaceSnapshot(snapshot);
        setParameters(snapshot.parameters);
        setModel(snapshot.model);
        const qaFailed = snapshot.qaReport?.status === 'failed';
        if (qaFailed) {
          const failedChecks = (snapshot.qaReport?.checks ?? [])
            .filter((check) => check.status === 'failed')
            .map((check) => check.id);
          setParameterBuildIssue({
            kind: 'qa-failed',
            message:
              failedChecks.length === 0
                ? 'The rebuilt model did not pass deterministic QA, but the preview reflects your change.'
                : `Deterministic QA failed (${failedChecks.join(', ')}); the preview reflects your change.`,
            names: changedNames,
          });
          setStatusMessage(
            `Parameter rebuild ${snapshot.runId} did not pass QA; preview updated anyway.`,
          );
        } else {
          setParameterBuildIssue(undefined);
          setStatusMessage(
            `Parameter rebuild ${snapshot.runId} passed QA and became the current immutable run.`,
          );
        }
      })
      .catch((buildError: unknown) => {
        if (buildError instanceof BuildSuperseded) return;
        if (sequence !== rebuildSequenceRef.current) return;
        const message =
          buildError instanceof Error
            ? buildError.message
            : 'Parameter rebuild failed.';
        const failingNames = extractTracebackParameters(
          message,
          (nextParameters.parameters ?? []).map((parameter) => parameter.name),
        );
        runtimeEventSequenceRef.current += 1;
        setRuntimeEvents((current) => [
          ...current.slice(-99),
          {
            id: runtimeEventSequenceRef.current,
            occurredAt: Date.now(),
            event: {
              schemaVersion: SCHEMA_VERSION,
              requestId: 'parameter-rebuild',
              type: 'log',
              level: 'error',
              line: message,
              truncated: false,
            },
          },
        ]);
        setParameterBuildIssue({
          kind: 'build-error',
          message,
          names: failingNames.length === 0 ? changedNames : failingNames,
        });
        setStatusMessage(
          buildError instanceof Error
            ? `Parameter rebuild failed: ${buildError.message}`
            : 'Parameter rebuild failed.',
        );
      })
      .finally(() => {
        if (sequence === rebuildSequenceRef.current) setRebuilding(false);
      });
  };

  const updateParameter = (name: string, value: ParameterValue) => {
    if (parameters === undefined) return;
    try {
      const coupling = couplingForParameter(parameters, name);
      if (coupling === undefined) {
        setParameterScaleNotice(undefined);
        scheduleParameterBuild(changeParameter(parameters, name, value));
        return;
      }
      const adjustment = applyProportionalAdjustment(
        parameters,
        coupling,
        name,
        value,
      );
      if (adjustment.scaledNames.length > 0) {
        setParameterScaleNotice({
          changedName: name,
          scaledNames: adjustment.scaledNames,
          source: coupling.source,
        });
      } else {
        setParameterScaleNotice(undefined);
      }
      scheduleParameterBuild(adjustment.parameterSet);
    } catch (parameterError) {
      setStatusMessage(
        parameterError instanceof Error
          ? parameterError.message
          : 'Parameter value is invalid.',
      );
    }
  };

  const undoParameters = () => {
    if (parameters !== undefined) {
      setParameterScaleNotice(undefined);
      scheduleParameterBuild(undoParameterChange(parameters));
    }
  };

  const redoParameters = () => {
    if (parameters !== undefined) {
      setParameterScaleNotice(undefined);
      scheduleParameterBuild(redoParameterChange(parameters));
    }
  };

  const resetAllParameters = () => {
    if (parameters !== undefined) {
      setParameterScaleNotice(undefined);
      setParameterBuildIssue(undefined);
      scheduleParameterBuild(resetParameters(parameters));
    }
  };

  const previewSourceWriteback = () => {
    const controller = controllerRef.current;
    if (controller === undefined || parameters === undefined) return;
    try {
      setWritebackPreview(controller.prepareSourceWriteback(parameters));
      setForceWriteback(false);
    } catch (writebackError) {
      setStatusMessage(
        writebackError instanceof Error
          ? writebackError.message
          : 'Source writeback preview failed.',
      );
    }
  };

  const confirmSourceWriteback = async () => {
    const controller = controllerRef.current;
    if (controller === undefined || writebackPreview === undefined) return;
    if (writebackQaFailed && !forceWriteback) {
      setStatusMessage(
        text(
          'QA did not pass for these parameters. Check “Force write” to save them to the source anyway.',
          '这些参数未通过 QA。勾选“强制写入”仍可将其保存到源文件。',
        ),
      );
      return;
    }
    const snapshot = await controller.confirmSourceWriteback(writebackPreview);
    setWorkspaceSnapshot(snapshot);
    setParameters(snapshot.parameters);
    setWritebackPreview(undefined);
    setForceWriteback(false);
    setSelectedFileId('source:model.py');
    setStatusMessage('Parameter values were written to a new source revision.');
  };

  const restoreRevision = async (revisionId: string) => {
    const controller = controllerRef.current;
    if (controller === undefined) return;
    const snapshot = await controller.restoreRevision(revisionId);
    buildQueueRef.current?.dispose();
    buildQueueRef.current = undefined;
    setWorkspaceSnapshot(snapshot);
    setParameters(snapshot.parameters);
    setSelectedFileId('source:model.py');
    if (snapshot.parameters !== undefined) {
      scheduleParameterBuild(snapshot.parameters);
    } else {
      setStatusMessage('The selected source was restored as a new revision.');
    }
  };

  const panelMaximum = (side: ResizeSide): number => {
    const hardMaximum = side === 'left' ? LEFT_PANEL_MAX : RIGHT_PANEL_MAX;
    const minimum = side === 'left' ? LEFT_PANEL_MIN : RIGHT_PANEL_MIN;
    const workspaceWidth = workspaceRef.current?.clientWidth;
    if (workspaceWidth === undefined) return hardMaximum;
    const oppositeWidth =
      side === 'left'
        ? rightCollapsed
          ? COLLAPSED_PANEL_WIDTH
          : rightWidth
        : leftCollapsed
          ? COLLAPSED_PANEL_WIDTH
          : leftWidth;
    return Math.max(
      minimum,
      Math.min(
        hardMaximum,
        workspaceWidth - oppositeWidth - RESIZER_TOTAL_WIDTH - CENTER_PANEL_MIN,
      ),
    );
  };

  const resizePanel = (side: ResizeSide, nextWidth: number) => {
    const minimum = side === 'left' ? LEFT_PANEL_MIN : RIGHT_PANEL_MIN;
    const next = clamp(nextWidth, minimum, panelMaximum(side));
    if (side === 'left') setLeftWidth(next);
    else setRightWidth(next);
  };

  const beginResize = (
    side: ResizeSide,
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    if (
      (side === 'left' && leftCollapsed) ||
      (side === 'right' && rightCollapsed)
    ) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    activeResizeRef.current = {
      side,
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: side === 'left' ? leftWidth : rightWidth,
    };
    setResizingSide(side);
  };

  const continueResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = activeResizeRef.current;
    if (active === undefined || active.pointerId !== event.pointerId) return;
    const pointerDelta = event.clientX - active.startX;
    resizePanel(
      active.side,
      active.startWidth +
        (active.side === 'left' ? pointerDelta : -pointerDelta),
    );
  };

  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = activeResizeRef.current;
    if (active === undefined || active.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    activeResizeRef.current = undefined;
    setResizingSide(undefined);
  };

  const resizeWithKeyboard = (
    side: ResizeSide,
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const current = side === 'left' ? leftWidth : rightWidth;
    resizePanel(side, current + direction * (side === 'left' ? 24 : -24));
  };

  const logPanelMaximum = (): number => {
    const centerHeight = centerPanelRef.current?.clientHeight;
    if (centerHeight === undefined) return LOG_PANEL_MAX;
    return Math.max(
      LOG_PANEL_MIN,
      Math.min(LOG_PANEL_MAX, centerHeight - PREVIEW_PANEL_MIN - 72),
    );
  };

  const resizeLogPanel = (nextHeight: number) => {
    setLogHeight(clamp(nextHeight, LOG_PANEL_MIN, logPanelMaximum()));
  };

  const beginLogResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (logCollapsed) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    activeLogResizeRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight: logHeight,
    };
    setResizingLog(true);
  };

  const continueLogResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = activeLogResizeRef.current;
    if (active === undefined || active.pointerId !== event.pointerId) return;
    resizeLogPanel(active.startHeight - (event.clientY - active.startY));
  };

  const finishLogResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = activeLogResizeRef.current;
    if (active === undefined || active.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    activeLogResizeRef.current = undefined;
    setResizingLog(false);
  };

  const resizeLogWithKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    resizeLogPanel(logHeight + (event.key === 'ArrowUp' ? 24 : -24));
  };

  const toggleOpfsSelection = (path: string) => {
    const file = opfsFiles.find((candidate) => candidate.path === path);
    if (isBundledPomodoroProject(file?.projectId)) return;
    setCleanupConfirmation('');
    setSelectedOpfsPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleAllOpfsFiles = () => {
    setCleanupConfirmation('');
    setSelectedOpfsPaths((current) => {
      const availablePaths = opfsFiles
        .filter(({ projectId }) => !isBundledPomodoroProject(projectId))
        .map(({ path }) => path);
      const allSelected =
        availablePaths.length > 0 &&
        availablePaths.every((path) => current.has(path));
      return allSelected ? new Set() : new Set(availablePaths);
    });
  };

  const toggleOpfsProject = (projectId: string) => {
    if (isBundledPomodoroProject(projectId)) return;
    setCleanupConfirmation('');
    setSelectedOpfsPaths((current) => {
      const projectPaths = opfsFiles
        .filter(({ projectId: id }) => id === projectId)
        .map(({ path }) => path);
      const next = new Set(current);
      const allSelected =
        projectPaths.length > 0 &&
        projectPaths.every((path) => current.has(path));
      if (allSelected) {
        for (const path of projectPaths) next.delete(path);
      } else {
        for (const path of projectPaths) next.add(path);
      }
      return next;
    });
  };

  const selectOpfsProjectForCleanup = (projectId: string) => {
    if (isBundledPomodoroProject(projectId)) return;
    setCleanupConfirmation('');
    setSelectedOpfsPaths(
      new Set(
        opfsFiles
          .filter(({ projectId: id }) => id === projectId)
          .map(({ path }) => path),
      ),
    );
    globalThis.requestAnimationFrame(() => {
      cleanupConfirmRef.current?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
      cleanupConfirmRef.current
        ?.querySelector('input')
        ?.focus({ preventScroll: true });
    });
  };

  const previewOpfsFile = async (file: OpfsWorkspaceFile) => {
    setOpfsError(undefined);
    try {
      const suffix = file.fileName.toLowerCase().split('.').pop() ?? '';
      if (
        file.category === 'model' &&
        suffix === '3mf' &&
        isBundledPomodoroProject(file.projectId)
      ) {
        const manifestModel = await loadBundledPomodoroViewerModel().catch(
          () => undefined,
        );
        if (manifestModel !== undefined) {
          setOpfsPreview({ file, model: manifestModel });
          return;
        }
      }
      const bytes = await readOpfsWorkspaceFile(file.path);
      if (bytes === undefined) {
        setOpfsError(`File no longer exists: ${file.path}`);
        return;
      }
      if (
        file.category === 'model' &&
        (suffix === '3mf' || suffix === 'glb' || suffix === 'stl')
      ) {
        const model: ViewerModel = {
          id: file.path,
          name: file.fileName,
          parts: [
            {
              id: file.path,
              name: file.fileName,
              format:
                suffix === 'glb' ? 'glb' : suffix === '3mf' ? '3mf' : 'stl',
              bytes: bytes.buffer.slice(
                bytes.byteOffset,
                bytes.byteOffset + bytes.byteLength,
              ) as ArrayBuffer,
            },
          ],
        };
        setOpfsPreview({ file, model });
      } else if (file.category === 'execution' || suffix === 'json') {
        const text = new TextDecoder().decode(bytes);
        const pretty = suffix === 'json' ? prettifyJsonText(text) : text;
        setOpfsPreview({
          file,
          text:
            pretty.length > 200_000
              ? `${pretty.slice(0, 200_000)}\n… (preview truncated)`
              : pretty,
        });
      } else {
        setOpfsPreview({
          file,
          text: `(${file.fileName}: ${formatByteLength(file.byteLength)} binary model; no inline preview.)`,
        });
      }
    } catch (previewError) {
      setOpfsError(
        previewError instanceof Error
          ? previewError.message
          : 'Unable to preview the selected file.',
      );
    }
  };

  const navigateLeftTabs = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const nextView = leftView === 'chat' ? 'files' : 'chat';
    setLeftView(nextView);
    globalThis.document
      ?.getElementById(nextView === 'chat' ? 'chat-tab' : 'files-tab')
      ?.focus({ preventScroll: true });
  };

  const cleanupSelectedOpfsFiles = async () => {
    const protectedPathSelected = opfsFiles.some(
      ({ path, projectId }) =>
        selectedOpfsPaths.has(path) && isBundledPomodoroProject(projectId),
    );
    if (
      cleanupConfirmation !== 'DELETE' ||
      selectedOpfsPaths.size === 0 ||
      cleanupPending ||
      protectedPathSelected
    ) {
      return;
    }
    setCleanupPending(true);
    setOpfsError(undefined);
    try {
      await removeOpfsWorkspaceFiles([...selectedOpfsPaths]);
      const removedCount = selectedOpfsPaths.size;
      setSelectedOpfsPaths(new Set());
      setCleanupConfirmation('');
      setOpfsPreview(undefined);
      await refreshOpfsFiles();
      setStatusMessage(
        `Removed ${String(removedCount)} selected OPFS files. Affected saved runs may no longer restore.`,
      );
    } catch (cleanupError) {
      setOpfsError(
        cleanupError instanceof Error
          ? cleanupError.message
          : 'Unable to clean the selected OPFS files.',
      );
    } finally {
      setCleanupPending(false);
    }
  };

  const busy = runPreparing || status === 'submitted' || status === 'streaming';
  const toolFailure = cadAgentToolErrorState(messages);
  const retryAttempts = toolRetryAttempts(messages);
  const currentPhase = contextRef.current?.phase;
  const activityStatus =
    currentPhase !== undefined &&
    ['completed', 'failed', 'cancelled'].includes(currentPhase)
      ? currentPhase
      : toolFailure?.halted === true
        ? 'failed'
        : toolFailure !== undefined
          ? 'retrying'
          : busy
            ? 'working'
            : status;
  const terminalActivityStatus = ['completed', 'failed', 'cancelled'].includes(
    activityStatus,
  );
  const expectedTool = expectedToolForPhase(contextRef.current);
  const showExpectedTool =
    expectedTool !== undefined && !hasPendingToolCall(messages, expectedTool);
  const expectedToolStatus =
    status === 'submitted'
      ? 'request submitted'
      : status === 'streaming'
        ? 'model is generating input'
        : 'queued';
  const lastAssistantMessage = messages.reduce<CadUiMessage | undefined>(
    (lastMessage, message) =>
      message.role === 'assistant' ? message : lastMessage,
    undefined,
  );
  const lastAssistantMessageId = lastAssistantMessage?.id;
  const lastPendingToolCallId = messages.reduce<string | undefined>(
    (latestToolCallId, message) =>
      message.parts.reduce<string | undefined>((latestPartId, part) => {
        if (
          isToolUIPart(part) &&
          part.state !== 'output-available' &&
          part.state !== 'output-error'
        ) {
          return part.toolCallId;
        }
        return latestPartId;
      }, latestToolCallId),
    undefined,
  );
  const fallbackActivity =
    activityFailure ??
    (showExpectedTool && expectedTool !== undefined
      ? {
          phase: contextRef.current?.phase ?? ('briefing' as const),
          reason: expectedToolStatus,
          tool: expectedTool,
        }
      : undefined);
  const fallbackCoveredByTool =
    activityFailure !== undefined &&
    (lastAssistantMessage?.parts.some(
      (part) =>
        isToolUIPart(part) &&
        (activityFailure.tool === undefined ||
          activityFailure.tool === getToolName(part)),
    ) ??
      false);
  const conversationMessages = messages.filter(
    (message) =>
      message.role === 'user' ||
      message.parts.some(
        (part) =>
          (part.type === 'text' && part.text.trim().length > 0) ||
          part.type === 'reasoning' ||
          isToolUIPart(part),
      ),
  );
  const conversationTurns = groupConversationTurns(conversationMessages);
  const activeConversationTurn = conversationTurns.at(-1);
  const finalAnswerMessage = activeConversationTurn?.assistantMessages.findLast(
    (message) =>
      message.parts.some(
        (part) => part.type === 'text' && part.text.trim().length > 0,
      ),
  );
  const finalAnswerTexts =
    activityStatus === 'completed'
      ? (finalAnswerMessage?.parts
          .filter((part) => part.type === 'text')
          .map((part) => part.text)
          .filter((text) => text.trim().length > 0) ?? [])
      : [];
  const terminalFailureReason =
    activityStatus === 'failed'
      ? (activityFailure?.reason ?? error?.message ?? statusMessage)
      : undefined;
  const runDuration =
    runStartedAt === undefined || runFinishedAt === undefined
      ? undefined
      : Math.max(0, runFinishedAt - runStartedAt);
  const conversationSettled =
    terminalActivityStatus &&
    (activityStatus !== 'completed' ||
      (status === 'ready' && finalAnswerTexts.length > 0));
  const composerAvailable =
    !runPreparing &&
    (status === 'ready' || status === 'error') &&
    (!started || conversationSettled);
  const files = useMemo(
    () => workspaceFiles(workspaceSnapshot),
    [workspaceSnapshot],
  );
  const selectedFile = files.find((file) => file.id === selectedFileId);
  const selectedModelPart = useMemo(
    () =>
      selectedFile?.artifact === undefined
        ? undefined
        : isBundledPomodoroProject(selectedProjectId) &&
            selectedFile.artifact.metadata.kind === 'model-3mf'
          ? model
          : viewerModelForArtifact(
              selectedFile.artifact,
              workspaceSnapshot?.designBrief?.colorRegionPlan,
            ),
    [model, selectedFile, selectedProjectId, workspaceSnapshot],
  );
  const displayedModel = useMemo(
    () => modelWithPreviewLayout(model, previewLayout),
    [model, previewLayout],
  );
  const displayedSelectedModelPart = useMemo(
    () => modelWithPreviewLayout(selectedModelPart, previewLayout),
    [previewLayout, selectedModelPart],
  );
  const displayedOpfsModel = useMemo(
    () => modelWithPreviewLayout(opfsPreview?.model, previewLayout),
    [opfsPreview, previewLayout],
  );
  const activeLayoutModel =
    opfsPreview !== undefined
      ? opfsPreview.model
      : selectedFileId === 'model:preview'
        ? model
        : selectedFile?.category === 'model'
          ? (selectedModelPart ?? model)
          : undefined;
  const previewLayoutAvailable = supportsSeparatedPreview(activeLayoutModel);

  useEffect(() => {
    setPreviewLayout('assembled');
  }, [activeLayoutModel?.id]);

  useEffect(() => {
    const available = new Set([
      ...files.map((file) => file.id),
      'model:preview',
    ]);
    setSelectedFileIds((current) => {
      if ([...current].every((id) => available.has(id))) return current;
      return new Set([...current].filter((id) => available.has(id)));
    });
  }, [files]);

  const toggleFileSelection = (id: string) => {
    setSelectedFileIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const allFilesSelected =
    files.length > 0 && files.every((file) => selectedFileIds.has(file.id));
  const toggleAllFiles = () => {
    setSelectedFileIds((current) => {
      const next = new Set(current);
      for (const file of files) {
        if (allFilesSelected) next.delete(file.id);
        else next.add(file.id);
      }
      return next;
    });
  };
  const currentModelJobs = (): DownloadJob[] => {
    const artifacts = workspaceSnapshot?.artifacts ?? [];
    const combined = artifacts.find(
      ({ metadata }) => metadata.kind === 'model-3mf',
    );
    if (combined !== undefined) {
      return [
        {
          fileName: combined.metadata.fileName,
          mediaType: combined.metadata.mediaType,
          bytes: combined.bytes,
        },
      ];
    }
    const steps = artifacts.filter(({ metadata }) => metadata.kind === 'step');
    if (steps.length > 0) {
      return steps.map((part) => ({
        fileName: part.metadata.fileName,
        mediaType: part.metadata.mediaType,
        bytes: part.bytes,
      }));
    }
    return artifacts
      .filter(
        ({ metadata }) =>
          metadata.kind === 'stl' || metadata.kind === 'region-stl',
      )
      .map((part) => ({
        fileName: part.metadata.fileName,
        mediaType: part.metadata.mediaType,
        bytes: part.bytes,
      }));
  };
  const downloadCurrent = async (): Promise<void> => {
    const jobs: DownloadJob[] = [];
    if (selectedFileIds.has('model:preview')) {
      jobs.push(...currentModelJobs());
    }
    for (const id of selectedFileIds) {
      if (id === 'model:preview') continue;
      const file = files.find((candidate) => candidate.id === id);
      if (file === undefined) continue;
      if (file.artifact !== undefined) {
        jobs.push({
          fileName: file.artifact.metadata.fileName,
          mediaType: file.artifact.metadata.mediaType,
          bytes: file.artifact.bytes,
        });
      } else if (file.text !== undefined) {
        jobs.push({
          fileName: file.label,
          mediaType:
            file.category === 'source' ? 'text/x-python' : 'application/json',
          bytes: new TextEncoder().encode(file.text),
        });
      }
    }
    for (const path of selectedOpfsPaths) {
      const opfsFile = opfsFiles.find((candidate) => candidate.path === path);
      if (opfsFile === undefined) continue;
      try {
        const bytes = await readOpfsWorkspaceFile(path);
        if (bytes === undefined) continue;
        jobs.push({
          fileName: opfsFile.fileName,
          mediaType: mediaTypeForFileName(opfsFile.fileName),
          bytes,
        });
      } catch {
        // A storage entry may vanish mid-selection; skip it rather than fail.
      }
    }
    enqueueDownloads(jobs);
  };
  useImperativeHandle(
    ref,
    () => ({
      downloadCurrent,
    }),
    [downloadCurrent],
  );
  const downloadTargetSignature = [
    [...selectedFileIds].sort().join('|'),
    [...selectedOpfsPaths].sort().join('|'),
    files.map((file) => file.id).join('|'),
    opfsFiles.map((file) => file.path).join('|'),
    language,
  ].join('#');
  const downloadTarget: CadDownloadTarget = useMemo(
    () => {
      const names: string[] = [];
      if (selectedFileIds.has('model:preview')) {
        names.push(...currentModelJobs().map((job) => job.fileName));
      }
      for (const id of selectedFileIds) {
        if (id === 'model:preview') continue;
        const file = files.find((candidate) => candidate.id === id);
        if (file !== undefined) {
          names.push(file.artifact?.metadata.fileName ?? file.label);
        }
      }
      for (const path of selectedOpfsPaths) {
        const file = opfsFiles.find((candidate) => candidate.path === path);
        if (file !== undefined) names.push(file.fileName);
      }
      if (names.length === 0) return undefined;
      const unique = [...new Set(names)];
      if (unique.length > 1) {
        return {
          fileName: `${String(unique.length)} ${
            language === 'zh' ? '个文件' : 'files'
          }`,
          category: 'file',
        };
      }
      const onlyModel =
        selectedFileIds.size === 1 &&
        selectedFileIds.has('model:preview') &&
        selectedOpfsPaths.size === 0;
      const onlyStorage =
        selectedOpfsPaths.size > 0 && selectedFileIds.size === 0;
      return {
        fileName: unique[0] ?? '',
        category: onlyModel ? 'model' : onlyStorage ? 'storage' : 'file',
      };
    },
    // Key the memo on a stable signature so unrelated workbench churn does
    // not produce a fresh target object and re-render the app bar in a loop.
    [downloadTargetSignature],
  );
  useEffect(() => {
    onDownloadTargetChange?.(downloadTarget);
  }, [downloadTarget, onDownloadTargetChange]);
  const opfsProjects = Array.from(
    opfsFiles.reduce((projects, file) => {
      const group = projects.get(file.projectId) ?? [];
      group.push(file);
      projects.set(file.projectId, group);
      return projects;
    }, new Map<string, OpfsWorkspaceFile[]>()),
    ([projectId, files]) => ({ projectId, files }),
  ).sort((left, right) =>
    compareProjectsWithPomodoroLast(left.projectId, right.projectId),
  );
  const cleanupEligibleOpfsFiles = opfsFiles.filter(
    ({ projectId }) => !isBundledPomodoroProject(projectId),
  );
  const toggleOpfsProjectExpanded = (projectId: string) => {
    setExpandedOpfsProjects((current) => {
      const defaultProjectId = opfsProjects[0]?.projectId;
      const next = new Set(
        current ?? (defaultProjectId === undefined ? [] : [defaultProjectId]),
      );
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };
  const selectedOpfsBytes = opfsFiles
    .filter(({ path }) => selectedOpfsPaths.has(path))
    .reduce((total, file) => total + file.byteLength, 0);
  const allOpfsFilesSelected =
    cleanupEligibleOpfsFiles.length > 0 &&
    cleanupEligibleOpfsFiles.every(({ path }) => selectedOpfsPaths.has(path));
  const parameterGroups = Object.entries(
    (parameters?.parameters ?? []).reduce<
      Record<string, NonNullable<ParameterSet['parameters']>>
    >((groups, parameter) => {
      const group = parameter.group ?? 'General';
      (groups[group] ??= []).push(parameter);
      return groups;
    }, {}),
  );
  const workspaceStyle = {
    '--workspace-left': leftCollapsed ? '3.25rem' : `${String(leftWidth)}px`,
    '--workspace-right': rightCollapsed ? '3.25rem' : `${String(rightWidth)}px`,
    '--workspace-log-height': logCollapsed
      ? `${String(LOG_PANEL_COLLAPSED_HEIGHT)}px`
      : `${String(logHeight)}px`,
    '--workspace-log-resizer': logCollapsed ? '0px' : '0.5rem',
  } as CSSProperties;
  const runActive = started && !conversationSettled;
  const runOptionsLocked = runActive || runPreparing;
  const parameterEditingEnabled =
    parameters !== undefined &&
    (currentPhase === 'completed' ||
      currentPhase === 'failed' ||
      currentPhase === 'cancelled');
  const hasParameterChanges =
    parameters?.parameters.some(
      (parameter) => !Object.is(parameter.value, parameter.defaultValue),
    ) ?? false;
  const writebackQaFailed = workspaceSnapshot?.qaReport?.status === 'failed';
  const selectedProjectRecord = projectRecords.find(
    (record) => record.projectId === selectedProjectId,
  );
  const renderFallbackActivity = (failed: boolean) => {
    if (fallbackActivity === undefined) return null;
    if (fallbackActivity.tool !== undefined) {
      return (
        <div className={styles.toolCallSequence}>
          <div
            aria-live={failed ? 'assertive' : 'polite'}
            className={`${styles.toolCallBox} ${failed ? styles.failedTool : ''}`}
            role={failed ? 'alert' : 'status'}
          >
            <span className={styles.toolCallIcon} aria-hidden="true">
              <WrenchIcon />
            </span>
            <span className={styles.toolIdentity}>
              <strong>{toolPhase(fallbackActivity.tool)}</strong>
              <code>{fallbackActivity.tool}</code>
            </span>
            {failed ? <ToolStatusMark state="error" /> : <LoadingSpinner />}
          </div>
          <div
            className={`${styles.conversationProgress} ${styles.toolResultProgress} ${failed ? styles.conversationProgressError : ''}`}
          >
            <div>
              <small>{fallbackActivity.reason}</small>
            </div>
          </div>
        </div>
      );
    }
    return (
      <div
        aria-live={failed ? 'assertive' : 'polite'}
        className={`${styles.conversationActivity} ${failed ? styles.failedTool : ''}`}
        role={failed ? 'alert' : undefined}
      >
        {failed ? (
          <span className={styles.activityMarker} aria-hidden="true" />
        ) : (
          <LoadingSpinner />
        )}
        <div className={styles.toolIdentity}>
          <strong>{fallbackActivity.phase.replaceAll('_', ' ')}</strong>
        </div>
        <span className={styles.activityState}>
          {failed ? 'failed' : fallbackActivity.reason}
        </span>
        {failed ? (
          <small className={styles.toolError}>{fallbackActivity.reason}</small>
        ) : null}
      </div>
    );
  };
  const renderConversationProgress = () =>
    started && lastPendingToolCallId === undefined ? (
      <div
        aria-live={error === undefined ? 'polite' : 'assertive'}
        className={`${styles.conversationProgress} ${error === undefined ? '' : styles.conversationProgressError}`}
        data-status={activityStatus}
        role={error === undefined ? 'status' : 'alert'}
      >
        {conversationSettled ? (
          <span className={styles.activityMarker} aria-hidden="true" />
        ) : (
          <LoadingSpinner />
        )}
        <div>
          <small id="agent-status">{error?.message ?? statusMessage}</small>
        </div>
      </div>
    ) : null;
  const toolOutputBrief = (toolName: string, output: unknown): string => {
    const record = objectRecord(output);
    if (record === undefined) {
      return text('Tool execution completed.', '工具执行完成。');
    }
    switch (toolName) {
      case 'saveDesignBrief':
        return text(
          `Design brief accepted${typeof record.workflowKind === 'string' ? ` · ${record.workflowKind}` : ''}.`,
          `设计简报已接受${typeof record.workflowKind === 'string' ? ` · ${record.workflowKind}` : ''}。`,
        );
      case 'writeCadSource':
        return text(
          `CAD source accepted${typeof record.sourceHash === 'string' ? ` · ${record.sourceHash.slice(0, 10)}…` : ''}.`,
          `CAD 源码已接受${typeof record.sourceHash === 'string' ? ` · ${record.sourceHash.slice(0, 10)}…` : ''}。`,
        );
      case 'buildAndCheck': {
        const summary =
          typeof record.summary === 'string'
            ? record.summary
            : text(
                'Build and deterministic QA completed.',
                '构建和确定性 QA 已完成。',
              );
        const artifactCount = Array.isArray(record.artifactIds)
          ? record.artifactIds.length
          : 0;
        const retrying =
          record.status === 'failed'
            ? text(' The agent will revise and retry.', ' Agent 将修正后重试。')
            : '';
        return `${summary}${artifactCount > 0 ? text(` ${String(artifactCount)} artifact(s) recorded.`, ` 已记录 ${String(artifactCount)} 个产物。`) : ''}${retrying}`;
      }
      case 'requestVisualReview':
        return typeof record.summary === 'string'
          ? record.summary
          : text('Visual review completed.', '视觉审查已完成。');
      case 'finishCadRun': {
        const artifactCount = Array.isArray(record.artifactIds)
          ? record.artifactIds.length
          : 0;
        return text(
          `Run completed · ${String(artifactCount)} verified artifact(s).`,
          `运行完成 · ${String(artifactCount)} 个已验证产物。`,
        );
      }
      default:
        return text('Tool execution completed.', '工具执行完成。');
    }
  };
  const renderReasoningPart = (
    part: Extract<CadUiMessagePart, { type: 'reasoning' }>,
    key: string,
  ) => (
    <div
      aria-live={part.state === 'streaming' ? 'polite' : undefined}
      className={styles.reasoningPlain}
      key={key}
    >
      <ReasoningMarkdown text={part.text || 'Waiting for model output…'} />
    </div>
  );
  const renderAssistantPart = (
    message: CadUiMessage,
    part: CadUiMessagePart,
    index: number,
    includeText = true,
    reasoningParts: Array<
      Extract<CadUiMessagePart, { type: 'reasoning' }>
    > = [],
  ) => {
    if (isToolUIPart(part)) {
      const toolName = getToolName(part);
      const failedHere =
        message.id === lastAssistantMessageId &&
        activityFailure !== undefined &&
        (activityFailure.tool === undefined ||
          activityFailure.tool === toolName);
      const toolPending =
        part.state !== 'output-available' && part.state !== 'output-error';
      const interruptedHere =
        toolPending &&
        part.toolCallId === lastPendingToolCallId &&
        (activityStatus === 'failed' || activityStatus === 'cancelled');
      const outputRejected =
        part.state === 'output-available' && toolOutputFailed(part.output);
      const toolFailed =
        failedHere ||
        interruptedHere ||
        outputRejected ||
        part.state === 'output-error';
      const failureText = failedHere
        ? activityFailure.reason
        : part.state === 'output-error'
          ? part.errorText
          : activityStatus === 'cancelled'
            ? 'Run cancelled before this tool completed.'
            : 'Run ended before this tool completed.';
      const retryAttempt = retryAttempts.get(part.toolCallId);
      const executionBrief =
        part.state === 'output-error'
          ? `${text(
              `Attempt ${String(retryAttempt?.attempt ?? 1)}/${String(retryAttempt?.limit ?? MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS)} failed: `,
              `第 ${String(retryAttempt?.attempt ?? 1)}/${String(retryAttempt?.limit ?? MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS)} 次失败：`,
            )}${part.errorText}${
              retryAttempt?.halted === true
                ? text(' Automatic retries stopped.', ' 自动重试已停止。')
                : text(' Retrying automatically…', ' 正在自动重试…')
            }`
          : interruptedHere || failedHere
            ? failureText
            : part.state === 'output-available'
              ? toolOutputBrief(toolName, part.output)
              : statusMessage;
      return (
        <div className={styles.toolCallSequence} key={part.toolCallId}>
          {reasoningParts.map((reasoningPart, reasoningIndex) =>
            renderReasoningPart(
              reasoningPart,
              `${part.toolCallId}-reasoning-${String(reasoningIndex)}`,
            ),
          )}
          <div
            className={`${styles.toolCallBox} ${toolFailed ? styles.failedTool : ''}`}
            role={toolFailed ? 'alert' : 'status'}
          >
            <span className={styles.toolCallIcon} aria-hidden="true">
              <WrenchIcon />
            </span>
            <span className={styles.toolIdentity}>
              <strong>{toolPhase(toolName)}</strong>
              <code>{toolName}</code>
            </span>
            {toolPending && !interruptedHere ? (
              <LoadingSpinner />
            ) : toolFailed ? (
              <ToolStatusMark state="error" />
            ) : (
              <ToolStatusMark state="success" />
            )}
          </div>
          <div
            aria-live={toolFailed ? 'assertive' : 'polite'}
            className={`${styles.conversationProgress} ${styles.toolResultProgress} ${toolFailed ? styles.conversationProgressError : ''}`}
            role={toolFailed ? 'alert' : 'status'}
          >
            <div>
              <small>{executionBrief}</small>
            </div>
          </div>
        </div>
      );
    }
    if (part.type === 'text') {
      return includeText ? <p key={index}>{part.text}</p> : null;
    }
    if (part.type === 'reasoning') {
      return renderReasoningPart(part, `reasoning-${String(index)}`);
    }
    return null;
  };
  const renderAssistantMessageParts = (
    message: CadUiMessage,
    includeText = true,
  ) => {
    const rendered: ReactNode[] = [];
    let pendingReasoning: Array<
      Extract<CadUiMessagePart, { type: 'reasoning' }>
    > = [];

    message.parts.forEach((part, index) => {
      if (part.type === 'reasoning') {
        pendingReasoning.push(part);
        return;
      }
      if (isToolUIPart(part)) {
        rendered.push(
          renderAssistantPart(
            message,
            part,
            index,
            includeText,
            pendingReasoning,
          ),
        );
        pendingReasoning = [];
        return;
      }
      const renderedPart = renderAssistantPart(
        message,
        part,
        index,
        includeText,
      );
      if (renderedPart !== null) rendered.push(renderedPart);
    });

    pendingReasoning.forEach((part, index) => {
      rendered.push(
        renderReasoningPart(
          part,
          `${message.id}-pending-reasoning-${String(index)}`,
        ),
      );
    });

    return rendered;
  };
  const renderResearchResult = (packet: ResearchPacket | undefined) => {
    if (packet === undefined) return null;
    const sourcesById = new Map(
      packet.sources.map((source) => [source.id, source]),
    );
    const statusLabel =
      packet.status === 'complete'
        ? text('Complete', '完整')
        : packet.status === 'partial'
          ? text('Partial', '部分完成')
          : text('Unavailable', '不可用');
    const confidenceLabel = {
      high: text('High confidence', '高置信度'),
      medium: text('Medium confidence', '中置信度'),
      low: text('Low confidence', '低置信度'),
    } as const;
    const sourceTypeLabel = {
      manufacturer: text('Manufacturer', '制造商'),
      datasheet: text('Datasheet', '数据手册'),
      distributor: text('Distributor', '分销商'),
      community: text('Community', '社区资料'),
      other: text('Other', '其他'),
    } as const;

    return (
      <div
        className={styles.researchResult}
        data-status={packet.status}
        role={packet.status === 'failed' ? 'alert' : 'status'}
      >
        <div className={styles.researchResultHeading}>
          <strong>{text('Research result', '调研结果')}</strong>
          <span>
            {statusLabel} ·{' '}
            {text(
              `${String(packet.findings.length)} findings · ${String(packet.sources.length)} sources`,
              `${String(packet.findings.length)} 条结论 · ${String(packet.sources.length)} 个来源`,
            )}
          </span>
          <small>{text('Advisory only', '仅供设计参考')}</small>
        </div>

        {packet.findings.length === 0 ? (
          <p className={styles.researchEmpty}>
            {text(
              'No usable findings were returned. Modeling can continue without research hints.',
              '未返回可用结论，建模可在没有调研提示的情况下继续。',
            )}
          </p>
        ) : (
          <ol className={styles.researchFindings}>
            {packet.findings.map((finding, findingIndex) => (
              <li key={`${finding.topic}-${String(findingIndex)}`}>
                <div className={styles.researchFindingHeading}>
                  <strong>{finding.topic}</strong>
                  <span data-confidence={finding.confidence}>
                    {confidenceLabel[finding.confidence]}
                  </span>
                </div>
                <p>{finding.summary}</p>
                {finding.value === undefined &&
                finding.originalExpression === undefined ? null : (
                  <dl className={styles.researchMeasurement}>
                    {finding.value === undefined ? null : (
                      <div>
                        <dt>{text('Normalized', '规范值')}</dt>
                        <dd>
                          {String(finding.value)}
                          {finding.unit === undefined ? '' : ` ${finding.unit}`}
                        </dd>
                      </div>
                    )}
                    {finding.originalExpression === undefined ? null : (
                      <div>
                        <dt>{text('Source value', '来源原值')}</dt>
                        <dd>{finding.originalExpression}</dd>
                      </div>
                    )}
                  </dl>
                )}
                {finding.caveat === undefined ? null : (
                  <p className={styles.researchCaveat}>
                    <strong>{text('Caveat', '注意')}</strong>
                    {finding.caveat}
                  </p>
                )}
                <div className={styles.researchCitations}>
                  <span>{text('Sources', '来源')}</span>
                  {finding.sourceIds.map((sourceId) => {
                    const source = sourcesById.get(sourceId);
                    if (source === undefined) return null;
                    const href = externalHttpUrl(source.url);
                    return href === undefined ? (
                      <span key={sourceId}>{source.title}</span>
                    ) : (
                      <a
                        href={href}
                        key={sourceId}
                        rel="noopener noreferrer"
                        target="_blank"
                      >
                        {source.title}
                      </a>
                    );
                  })}
                </div>
              </li>
            ))}
          </ol>
        )}

        {packet.warnings.length === 0 ? null : (
          <div className={styles.researchWarnings}>
            <strong>{text('Warnings', '风险提示')}</strong>
            <ul>
              {packet.warnings.map((warning, warningIndex) => (
                <li key={`${warning}-${String(warningIndex)}`}>{warning}</li>
              ))}
            </ul>
          </div>
        )}

        {packet.sources.length === 0 ? null : (
          <details className={styles.researchSourceList}>
            <summary>
              {text(
                `All sources · ${String(packet.sources.length)}`,
                `全部来源 · ${String(packet.sources.length)}`,
              )}
            </summary>
            <ul>
              {packet.sources.map((source) => {
                const href = externalHttpUrl(source.url);
                return (
                  <li key={source.id}>
                    <div>
                      {href === undefined ? (
                        <strong>{source.title}</strong>
                      ) : (
                        <a
                          href={href}
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          {source.title}
                        </a>
                      )}
                      <small>{sourceTypeLabel[source.sourceType]}</small>
                    </div>
                    {source.summary === undefined ? null : (
                      <p>{source.summary}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </details>
        )}
      </div>
    );
  };
  const renderResearchStage = (stage: ResearchStage) => {
    const sourceCount = stage.packet?.sources.length ?? 0;
    return (
      <div className={styles.toolCallSequence}>
        <div
          className={`${styles.toolCallBox} ${stage.status === 'failed' ? styles.failedTool : ''}`}
          data-stage="research"
          role={stage.status === 'running' ? 'status' : undefined}
        >
          <span className={styles.toolCallIcon} aria-hidden="true">
            <ToolbarIcon name="research" />
          </span>
          <span className={styles.toolIdentity}>
            <strong>{text('research', '资料调研')}</strong>
            <code>
              {stage.status === 'completed'
                ? text(
                    `${String(sourceCount)} sources collected`,
                    `已整理 ${String(sourceCount)} 个来源`,
                  )
                : stage.status === 'running'
                  ? stage.attempt !== undefined &&
                    stage.attempt > 1 &&
                    stage.total !== undefined
                    ? text(
                        `searching web references · retry ${String(stage.attempt)}/${String(stage.total)}`,
                        `正在检索网络资料 · 重试 ${String(stage.attempt)}/${String(stage.total)}`,
                      )
                    : text('searching web references', '正在检索网络资料')
                  : stage.status === 'failed'
                    ? text('research unavailable', '资料调研不可用')
                    : text('web research skipped', '已跳过网络调研')}
            </code>
          </span>
          {stage.status === 'running' ? (
            <LoadingSpinner />
          ) : stage.status === 'failed' ? (
            <ToolStatusMark state="error" />
          ) : (
            <ToolStatusMark state="success" />
          )}
        </div>
        {renderResearchResult(stage.packet)}
      </div>
    );
  };
  const renderUserMessage = (message: CadUiMessage) => (
    <li data-role="user" key={`user-${message.id}`}>
      <span className={styles.messageRole}>{text('You', '你')}</span>
      <div className={styles.messageBubble}>
        {message.parts.map((part, index) => {
          if (part.type === 'text') return <p key={index}>{part.text}</p>;
          if (part.type === 'file' && part.mediaType.startsWith('image/')) {
            return (
              <figure className={styles.messageAttachment} key={index}>
                <img
                  alt={part.filename ?? text('Attached image', '附件图片')}
                  decoding="async"
                  src={part.url}
                />
                {part.filename === undefined ? null : (
                  <figcaption>{part.filename}</figcaption>
                )}
              </figure>
            );
          }
          return null;
        })}
      </div>
    </li>
  );
  const renderConversationTurn = (
    turn: ConversationTurn,
    turnIndex: number,
  ) => {
    const isCurrent = turnIndex === conversationTurns.length - 1;
    const settled = !isCurrent || conversationSettled;
    const turnFinalAnswerMessage = turn.assistantMessages.findLast((message) =>
      message.parts.some(
        (part) => part.type === 'text' && part.text.trim().length > 0,
      ),
    );
    const turnFinalAnswerTexts =
      turnFinalAnswerMessage?.parts
        .filter((part) => part.type === 'text')
        .map((part) => part.text)
        .filter((value) => value.trim().length > 0) ?? [];
    const researchStage =
      turn.user?.metadata?.research ??
      (isCurrent && research !== undefined
        ? ({ status: 'completed', packet: research } satisfies ResearchStage)
        : undefined);
    const assistantVisible =
      turn.assistantMessages.length > 0 || (isCurrent && started);
    return [
      ...(turn.user === undefined ? [] : [renderUserMessage(turn.user)]),
      ...(assistantVisible
        ? [
            <li data-role="assistant" key={`assistant-${turn.id}`}>
              <span className={styles.messageRole}>
                {text('Agent', 'Agent')}
              </span>
              <div className={styles.messageBubble}>
                {settled ? (
                  <>
                    <details className={styles.completedRunTrace}>
                      <summary>
                        <span>
                          {isCurrent
                            ? `${text('Execution trace', '执行过程')} · ${t('elapsed')}${language === 'zh' ? '：' : ': '}${formatRunDuration(
                                runDuration,
                                {
                                  notRecorded: t('notRecorded'),
                                  seconds: t('seconds'),
                                  minutes: t('minutes'),
                                },
                              )}`
                            : text('Completed turn', '已完成的一轮')}
                        </span>
                        <span
                          className={styles.traceChevronClosed}
                          aria-hidden="true"
                        >
                          ›
                        </span>
                        <span
                          className={styles.traceChevronOpen}
                          aria-hidden="true"
                        >
                          ⌄
                        </span>
                      </summary>
                      <div className={styles.completedRunTraceBody}>
                        {researchStage === undefined
                          ? null
                          : renderResearchStage(researchStage)}
                        {turn.assistantMessages.map((message) =>
                          renderAssistantMessageParts(message, false),
                        )}
                        {isCurrent &&
                        fallbackActivity !== undefined &&
                        !fallbackCoveredByTool
                          ? renderFallbackActivity(
                              activityFailure !== undefined,
                            )
                          : null}
                        {isCurrent ? renderConversationProgress() : null}
                      </div>
                    </details>
                    <hr className={styles.answerDivider} />
                    <div className={styles.finalAnswer}>
                      {isCurrent && terminalFailureReason !== undefined ? (
                        <>
                          <p>
                            {text(
                              'The Agent call failed. Details:',
                              'Agent 调用失败，原因如下：',
                            )}
                          </p>
                          <pre className={styles.failureReason} role="alert">
                            <code>{terminalFailureReason}</code>
                          </pre>
                        </>
                      ) : turnFinalAnswerTexts.length > 0 ? (
                        turnFinalAnswerTexts.map((value, index) => (
                          <p key={index}>{value}</p>
                        ))
                      ) : isCurrent ? (
                        <p>{error?.message ?? statusMessage}</p>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className={styles.conversationQueue}>
                    {researchStage === undefined
                      ? null
                      : renderResearchStage(researchStage)}
                    {turn.assistantMessages.map((message) =>
                      renderAssistantMessageParts(message),
                    )}
                    {fallbackActivity !== undefined && !fallbackCoveredByTool
                      ? renderFallbackActivity(activityFailure !== undefined)
                      : null}
                    {renderConversationProgress()}
                  </div>
                )}
              </div>
            </li>,
          ]
        : []),
    ];
  };
  const renderPendingTurn = (turn: PendingConversationTurn) => [
    <li data-role="user" key={`pending-user-${turn.id}`}>
      <span className={styles.messageRole}>{text('You', '你')}</span>
      <div className={styles.messageBubble}>
        <p>{turn.text}</p>
        {turn.attachments.map((attachment) => (
          <figure
            className={styles.messageAttachment}
            key={attachment.fileName}
          >
            <img alt={attachment.fileName} src={attachment.url} />
            <figcaption>{attachment.fileName}</figcaption>
          </figure>
        ))}
      </div>
    </li>,
    <li data-role="assistant" key={`pending-assistant-${turn.id}`}>
      <span className={styles.messageRole}>{text('Agent', 'Agent')}</span>
      <div className={styles.messageBubble}>
        <div className={styles.conversationQueue}>
          {renderResearchStage(turn.research)}
        </div>
      </div>
    </li>,
  ];
  const renderOpfsProject = (project: {
    projectId: string;
    files: OpfsWorkspaceFile[];
  }) => {
    const { projectId, files } = project;
    const protectedProject = isBundledPomodoroProject(projectId);
    const projectBytes = files.reduce(
      (total, file) => total + file.byteLength,
      0,
    );
    const projectPaths = files.map(({ path }) => path);
    const projectSelected =
      projectPaths.length > 0 &&
      projectPaths.every((path) => selectedOpfsPaths.has(path));
    const projectExpanded =
      expandedOpfsProjects?.has(projectId) ??
      projectId === opfsProjects[0]?.projectId;
    const fileListId = `storage-project-${encodeURIComponent(projectId)}`;
    return (
      <section
        aria-label={projectId}
        className={styles.opfsGroup}
        data-expanded={projectExpanded}
        key={projectId}
      >
        <div className={styles.storageGroupHeading}>
          <label className={styles.projectCheckbox} title={projectId}>
            <input
              aria-label={text(
                `Select all files in ${projectId}`,
                `选中 ${projectId} 的全部文件`,
              )}
              checked={projectSelected}
              disabled={protectedProject}
              onChange={() => toggleOpfsProject(projectId)}
              type="checkbox"
            />
          </label>
          <button
            aria-controls={fileListId}
            aria-expanded={projectExpanded}
            className={styles.projectSummary}
            onClick={() => toggleOpfsProjectExpanded(projectId)}
            type="button"
          >
            <span className={styles.projectDisclosureMark} aria-hidden="true">
              <svg fill="none" focusable="false" viewBox="0 0 16 16">
                <path d="m5.25 6.5 2.75 3 2.75-3" />
              </svg>
            </span>
            <span className={styles.projectIdentity}>
              <strong>{projectId}</strong>
              <small>
                {protectedProject
                  ? `${bundledPomodoroProjectName(language)} · ${text(
                      'Built-in · protected',
                      '内置 · 不可删除',
                    )}`
                  : projectId}{' '}
                · {files.length} {text('files', '个文件')} ·{' '}
                {formatByteLength(projectBytes)}
              </small>
            </span>
          </button>
          <button
            className={styles.storageProjectCleanup}
            disabled={protectedProject || files.length === 0}
            onClick={() => selectOpfsProjectForCleanup(projectId)}
            title={
              protectedProject
                ? text(
                    'Built-in projects cannot be deleted',
                    '内置项目不可删除',
                  )
                : undefined
            }
            type="button"
          >
            {text('Clean', '清理')}
          </button>
        </div>
        <ul
          className={styles.opfsFileList}
          hidden={!projectExpanded}
          id={fileListId}
        >
          {files.map((file) => (
            <li key={file.path}>
              <label className={styles.opfsFileCheckbox} title={file.path}>
                <input
                  checked={selectedOpfsPaths.has(file.path)}
                  disabled={protectedProject}
                  onChange={() => toggleOpfsSelection(file.path)}
                  type="checkbox"
                />
              </label>
              <button
                className={styles.storageFileIdentity}
                onClick={() => void previewOpfsFile(file)}
                type="button"
              >
                <strong>{file.fileName}</strong>
                <small>
                  {file.runId === undefined
                    ? file.category
                    : `${file.category} · ${file.runId}`}
                </small>
              </button>
              <span className={styles.storageFileSize}>
                {formatByteLength(file.byteLength)}
              </span>
            </li>
          ))}
        </ul>
      </section>
    );
  };
  return (
    <div
      className={styles.workspace}
      data-resizing={resizingLog ? 'log' : resizingSide}
      ref={workspaceRef}
      style={workspaceStyle}
    >
      <aside
        aria-label="Conversation and generated files"
        className={`${styles.leftPanel} ${leftCollapsed ? styles.collapsedPanel : ''}`}
        data-menu-open={historyOpen || undefined}
      >
        <header className={styles.panelHeader}>
          <div className={styles.panelTitle}>
            <span className={styles.projectMark} aria-hidden="true" />
            <div className={styles.panelTitleSelect} ref={executionMenuRef}>
              <button
                aria-expanded={historyOpen}
                aria-haspopup="listbox"
                className={styles.panelTitleButton}
                onClick={() => setHistoryOpen((open) => !open)}
                type="button"
              >
                <strong>
                  {executionDisplayTitle(
                    projectDisplayName(
                      selectedProjectId,
                      selectedProjectRecord?.title ??
                        workspaceSnapshot?.projectName ??
                        (language === 'zh' ? '新建外壳' : 'New enclosure'),
                    ),
                    language === 'zh' ? '未命名项目' : 'Untitled project',
                    30,
                  )}
                </strong>
                <span aria-hidden="true" className={styles.panelTitleChevron}>
                  <svg fill="none" focusable="false" viewBox="0 0 16 16">
                    <path d="m5.25 6.25 2.75-2 2.75 2M5.25 9.75l2.75 2 2.75-2" />
                  </svg>
                </span>
              </button>
              {historyOpen ? (
                <div
                  aria-label="Projects"
                  className={styles.executionMenu}
                  role="listbox"
                >
                  <div className={styles.executionMenuHeading}>
                    <strong>{text('Projects', '项目')}</strong>
                    <span>{projectRecords.length}</span>
                  </div>
                  {projectRecords.length === 0 ? (
                    <p className={styles.executionMenuEmpty}>
                      {text(
                        'CAD projects will appear here.',
                        'CAD 项目会显示在这里。',
                      )}
                    </p>
                  ) : (
                    projectRecords.map((record) => (
                      <button
                        aria-selected={record.projectId === selectedProjectId}
                        className={styles.executionMenuItem}
                        key={record.projectId}
                        onClick={() => void selectProject(record.projectId)}
                        role="option"
                        type="button"
                      >
                        <span>
                          {executionDisplayTitle(
                            projectDisplayName(record.projectId, record.title),
                            language === 'zh'
                              ? '未命名项目'
                              : 'Untitled project',
                          )}
                          {isBundledPomodoroProject(record.projectId) ? (
                            <small className={styles.bundledProjectBadge}>
                              {text('Built-in', '内置')}
                            </small>
                          ) : null}
                        </span>
                        <time dateTime={record.updatedAt}>
                          {formatExecutionTime(record.updatedAt, language)}
                        </time>
                      </button>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          </div>
          <div className={styles.panelControls}>
            <span className={styles.panelStatus}>
              {pendingConversationTurn?.research.status === 'running'
                ? text('research', '资料调研')
                : phaseLabel(contextRef.current, language)}
            </span>
            <button
              aria-expanded={!leftCollapsed}
              aria-label={
                leftCollapsed
                  ? text('Open left panel', '打开左侧面板')
                  : text('Collapse left panel', '折叠左侧面板')
              }
              className={styles.panelCollapseButton}
              onClick={() => setLeftCollapsed((collapsed) => !collapsed)}
              type="button"
            >
              {leftCollapsed ? '›' : '‹'}
            </button>
          </div>
        </header>

        {leftCollapsed ? null : (
          <>
            <div
              aria-label={text('Left panel view', '左侧面板视图')}
              className={styles.leftTabs}
              role="tablist"
            >
              <button
                aria-controls="chat-panel"
                aria-selected={leftView === 'chat'}
                id="chat-tab"
                onKeyDown={navigateLeftTabs}
                onClick={() => setLeftView('chat')}
                role="tab"
                tabIndex={leftView === 'chat' ? 0 : -1}
                type="button"
              >
                {text('Chat', '对话')}
              </button>
              <button
                aria-controls="files-panel"
                aria-selected={leftView === 'files'}
                id="files-tab"
                onKeyDown={navigateLeftTabs}
                onClick={() => setLeftView('files')}
                role="tab"
                tabIndex={leftView === 'files' ? 0 : -1}
                type="button"
              >
                {text('Files', '文件')}
              </button>
            </div>

            {leftView === 'chat' ? (
              <div
                aria-labelledby="chat-tab"
                className={styles.chatPanel}
                id="chat-panel"
                role="tabpanel"
              >
                <section
                  className={styles.conversation}
                  aria-labelledby="conversation-heading"
                  ref={conversationRef}
                >
                  <h2 className={styles.srOnly} id="conversation-heading">
                    Conversation
                  </h2>
                  {conversationMessages.length === 0 &&
                  pendingConversationTurn === undefined &&
                  !started ? (
                    <div className={styles.emptyState}>
                      <strong>
                        {text(
                          'Start with the enclosure brief.',
                          '从外壳需求开始。',
                        )}
                      </strong>
                      <span>
                        {text(
                          'Describe the part as you would in ChatGPT. Sending the message starts the CAD run immediately.',
                          '像在 ChatGPT 中一样描述零件。发送消息后会立即开始 CAD 执行。',
                        )}
                      </span>
                    </div>
                  ) : (
                    <ol className={styles.messageList}>
                      {conversationTurns.flatMap(renderConversationTurn)}
                      {pendingConversationTurn === undefined
                        ? null
                        : renderPendingTurn(pendingConversationTurn)}
                    </ol>
                  )}
                </section>

                <section
                  className={styles.composer}
                  aria-labelledby="agent-request-heading"
                >
                  <form
                    className={styles.composerForm}
                    onSubmit={(event) => void submit(event)}
                  >
                    <div className={styles.composerShell}>
                      <label
                        className={styles.srOnly}
                        htmlFor="cad-request"
                        id="agent-request-heading"
                      >
                        Hardware enclosure request
                      </label>
                      <textarea
                        aria-busy={busy}
                        data-ai-sdk-status={status}
                        id="cad-request"
                        disabled={!composerAvailable}
                        maxLength={8_000}
                        onChange={(event) => setPrompt(event.target.value)}
                        onDragOver={(event) => event.preventDefault()}
                        onDrop={handleImageDrop}
                        onPaste={handleImagePaste}
                        onKeyDown={handleComposerKeyDown}
                        placeholder={text(
                          'Describe the part or change you want…',
                          '描述你想设计或修改的零件…',
                        )}
                        required
                        ref={composerInputRef}
                        rows={1}
                        value={prompt}
                      />

                      {runOptionsPanel === undefined ? null : (
                        <div
                          aria-label={`${runOptionsPanel} run settings`}
                          className={styles.runOptionsPopover}
                          id="run-options-popover"
                          ref={runOptionsPopoverRef}
                          role="group"
                        >
                          <div className={styles.runOptionsHeading}>
                            <strong>
                              {runOptionsPanel === 'workflow'
                                ? text('Workflow', '工作流')
                                : runOptionsPanel === 'research'
                                  ? text('Web references', '网络参考')
                                  : runOptionsPanel === 'review'
                                    ? text('Visual review', '视觉审查')
                                    : text('Model', '模型')}
                            </strong>
                            <button
                              aria-label="Close run settings"
                              onClick={() => setRunOptionsPanel(undefined)}
                              type="button"
                            >
                              ×
                            </button>
                          </div>

                          {runOptionsPanel === 'workflow' ? (
                            <fieldset
                              className={styles.workflowChoice}
                              disabled={runOptionsLocked}
                            >
                              <legend className={styles.srOnly}>
                                Workflow
                              </legend>
                              {(
                                ['auto', 'single-color', 'multi-color'] as const
                              ).map((value) => (
                                <label key={value}>
                                  <input
                                    checked={preference === value}
                                    name="workflow"
                                    onChange={() => setPreference(value)}
                                    type="radio"
                                  />
                                  {value === 'auto'
                                    ? text('Auto', '自动')
                                    : value === 'single-color'
                                      ? text('Single color', '单色')
                                      : text('Multi color', '多色')}
                                </label>
                              ))}
                            </fieldset>
                          ) : runOptionsPanel === 'model' ? (
                            <div className={styles.modelProfilePanel}>
                              <strong>
                                {text('Model profile', '模型配置')}
                              </strong>
                              <select
                                aria-label="Model profile"
                                className={styles.modelProfileSelect}
                                disabled={runOptionsLocked}
                                onChange={(event) =>
                                  setSelectedModelProfileId(
                                    event.target.value || undefined,
                                  )
                                }
                                value={selectedModelProfileId ?? ''}
                              >
                                <option value="">
                                  {text('Environment default', '环境默认')}
                                </option>
                                {(modelSettings?.profiles ?? []).map(
                                  (profile) => (
                                    <option
                                      disabled={
                                        !profile.enabled ||
                                        profile.validation.status !== 'valid'
                                      }
                                      key={profile.id}
                                      value={profile.id}
                                    >
                                      {profile.displayName}
                                      {profile.validation.status !== 'valid'
                                        ? ' · needs validation'
                                        : ''}
                                    </option>
                                  ),
                                )}
                              </select>
                              <div className={styles.actions}>
                                <button
                                  className={styles.addModelButton}
                                  onClick={() => void addModelProfile()}
                                  type="button"
                                >
                                  {text('Add model', '添加模型')}
                                </button>
                                {(modelSettings?.profiles ?? []).map(
                                  (profile) => (
                                    <span key={`actions-${profile.id}`}>
                                      <button
                                        className={styles.secondary}
                                        onClick={() =>
                                          void toggleModelProfile(profile)
                                        }
                                        type="button"
                                      >
                                        {profile.enabled
                                          ? text(
                                              `Disable ${profile.displayName}`,
                                              `停用 ${profile.displayName}`,
                                            )
                                          : text(
                                              `Enable ${profile.displayName}`,
                                              `启用 ${profile.displayName}`,
                                            )}
                                      </button>
                                      <button
                                        className={styles.secondary}
                                        onClick={() =>
                                          void editModelProfile(profile)
                                        }
                                        type="button"
                                      >
                                        {text('Edit', '编辑')}
                                      </button>
                                      <button
                                        className={styles.secondary}
                                        onClick={() =>
                                          void setDefaultModelProfile(profile)
                                        }
                                        type="button"
                                      >
                                        {text('Set default', '设为默认')}
                                      </button>
                                      <button
                                        className={styles.secondary}
                                        onClick={() =>
                                          void validateServerModelProfile(
                                            profile,
                                          )
                                        }
                                        type="button"
                                      >
                                        {text('Validate', '验证')}
                                      </button>
                                      <button
                                        className={styles.secondary}
                                        onClick={() =>
                                          void deleteModelProfile(profile)
                                        }
                                        type="button"
                                      >
                                        {text('Delete', '删除')}
                                      </button>
                                    </span>
                                  ),
                                )}
                              </div>
                              <small>
                                {text(
                                  'Keys remain server-side. A run freezes provider, model ID, capabilities and profile revision.',
                                  '密钥始终保留在服务端。每次运行都会锁定服务商、模型 ID、能力配置和配置档案版本。',
                                )}
                              </small>
                            </div>
                          ) : (
                            <label className={styles.optionToggle}>
                              <input
                                checked={
                                  runOptionsPanel === 'research'
                                    ? searchEnabled
                                    : visualReviewApproved
                                }
                                disabled={runOptionsLocked}
                                onChange={(event) => {
                                  if (runOptionsPanel === 'research') {
                                    setSearchEnabled(event.target.checked);
                                  } else {
                                    setVisualReviewApproved(
                                      event.target.checked,
                                    );
                                  }
                                }}
                                type="checkbox"
                              />
                              <span>
                                <strong>
                                  {runOptionsPanel === 'research'
                                    ? text(
                                        'Use web research before modeling',
                                        '建模前使用网络研究',
                                      )
                                    : text(
                                        'Pause for a browser visual review',
                                        '暂停并进行浏览器视觉审查',
                                      )}
                                </strong>
                                <small>
                                  {runOptionsPanel === 'research'
                                    ? text(
                                        'Useful when the model needs dimensions or external references.',
                                        '模型需要尺寸或外部参考时很有用。',
                                      )
                                    : text(
                                        'The run waits for approval or a repair request before finishing.',
                                        '执行会等待批准或修复请求后再完成。',
                                      )}
                                </small>
                              </span>
                            </label>
                          )}
                        </div>
                      )}

                      {imageAttachments.length > 0 ? (
                        <div
                          aria-label="Attached reference images"
                          className={styles.attachmentStrip}
                        >
                          {imageAttachments.map((attachment, index) => (
                            <button
                              className={styles.attachmentChip}
                              key={`${attachment.fileName}-${String(index)}`}
                              onClick={() =>
                                setImageAttachments((current) =>
                                  current.filter(
                                    (_, itemIndex) => itemIndex !== index,
                                  ),
                                )
                              }
                              title="Remove image"
                              type="button"
                            >
                              <img alt="" src={imagePreviewUrl(attachment)} />
                              <span>{attachment.fileName} ×</span>
                            </button>
                          ))}
                        </div>
                      ) : null}

                      <div className={styles.composerFooter}>
                        <div
                          aria-label={text('Run options', '执行选项')}
                          className={styles.composerTools}
                          ref={runOptionsTriggerRef}
                        >
                          <button
                            aria-label={text('Start a new project', '新建项目')}
                            className={styles.composerTool}
                            data-tooltip={text('New project', '新项目')}
                            disabled={!started}
                            onClick={() => void beginFreshRun()}
                            type="button"
                          >
                            <ToolbarIcon name="new-run" />
                          </button>
                          <button
                            aria-controls="run-options-popover"
                            aria-expanded={runOptionsPanel === 'workflow'}
                            aria-label={`Workflow: ${preference}`}
                            className={styles.composerTool}
                            data-active={preference !== 'auto'}
                            data-tooltip={`Workflow · ${preference}`}
                            onClick={() =>
                              setRunOptionsPanel((current) =>
                                current === 'workflow' ? undefined : 'workflow',
                              )
                            }
                            type="button"
                          >
                            <ToolbarIcon name="workflow" />
                          </button>
                          <button
                            aria-controls="run-options-popover"
                            aria-expanded={runOptionsPanel === 'research'}
                            aria-label={text(
                              'Configure web references',
                              '配置网络参考',
                            )}
                            className={styles.composerTool}
                            data-active={searchEnabled}
                            data-tooltip={text('Web references', '网络参考')}
                            onClick={() =>
                              setRunOptionsPanel((current) =>
                                current === 'research' ? undefined : 'research',
                              )
                            }
                            type="button"
                          >
                            <ToolbarIcon name="research" />
                          </button>
                          <button
                            aria-controls="run-options-popover"
                            aria-expanded={runOptionsPanel === 'review'}
                            aria-label={text(
                              'Configure visual review',
                              '配置视觉审查',
                            )}
                            className={styles.composerTool}
                            data-active={visualReviewApproved}
                            data-tooltip={text('Visual review', '视觉审查')}
                            onClick={() =>
                              setRunOptionsPanel((current) =>
                                current === 'review' ? undefined : 'review',
                              )
                            }
                            type="button"
                          >
                            <ToolbarIcon name="review" />
                          </button>
                          <button
                            aria-controls="run-options-popover"
                            aria-expanded={runOptionsPanel === 'model'}
                            aria-label={text(
                              'Configure model profile',
                              '配置模型',
                            )}
                            className={styles.composerTool}
                            data-active={selectedModelProfileId !== undefined}
                            data-tooltip={text('Model profile', '模型配置')}
                            onClick={() =>
                              setRunOptionsPanel((current) =>
                                current === 'model' ? undefined : 'model',
                              )
                            }
                            type="button"
                          >
                            <GearIcon />
                          </button>
                          <label
                            className={styles.composerTool}
                            data-tooltip={text(
                              'Attach reference images',
                              '附加参考图',
                            )}
                          >
                            <span className={styles.srOnly}>
                              {text('Attach reference images', '附加参考图')}
                            </span>
                            <input
                              accept="image/png,image/jpeg,image/webp,image/gif"
                              className={styles.srOnly}
                              multiple
                              onChange={(event) =>
                                void handleImageSelection(event.target.files)
                              }
                              type="file"
                            />
                            <span aria-hidden="true">▧</span>
                          </label>
                        </div>

                        <button
                          aria-label={
                            runActive
                              ? text('Stop current run', '停止当前执行')
                              : text('Send message', '发送消息')
                          }
                          className={styles.sendButton}
                          data-state={runActive ? 'stop' : 'send'}
                          disabled={
                            !runActive &&
                            (!composerAvailable || prompt.trim().length === 0)
                          }
                          onClick={runActive ? () => void cancel() : undefined}
                          title={
                            runActive
                              ? text('Stop current run', '停止当前执行')
                              : text(
                                  'Send message (Enter)',
                                  '发送消息（Enter）',
                                )
                          }
                          type={runActive ? 'button' : 'submit'}
                        >
                          <ToolbarIcon name={runActive ? 'stop' : 'send'} />
                        </button>
                      </div>
                    </div>
                  </form>
                </section>
              </div>
            ) : (
              <div
                aria-labelledby="files-tab"
                className={styles.fileWorkspace}
                id="files-panel"
                role="tabpanel"
              >
                <section
                  className={styles.fileSection}
                  aria-labelledby="current-model-heading"
                >
                  <div className={styles.sectionHeading}>
                    <h2 id="current-model-heading">
                      {text('Current model preview', '当前模型预览')}
                    </h2>
                  </div>
                  <ul className={styles.fileTree}>
                    <li>
                      <label
                        className={styles.fileTreeCheckbox}
                        title={text('Select for download', '勾选以下载')}
                      >
                        <input
                          aria-label={text(
                            'Select current model for download',
                            '勾选当前模型以下载',
                          )}
                          checked={selectedFileIds.has('model:preview')}
                          onChange={() => toggleFileSelection('model:preview')}
                          type="checkbox"
                        />
                      </label>
                      <button
                        aria-current={selectedFileId === 'model:preview'}
                        onClick={() => setSelectedFileId('model:preview')}
                        title={text(
                          'Preview in assembly coordinates by default. Download provides the verified STEP file, or one STEP per printable body.',
                          '默认按真实装配坐标预览。下载内容为已验证 STEP；分体模型会下载每个可打印实体各自的 STEP。',
                        )}
                        type="button"
                      >
                        <span className={styles.fileIcon}>3D</span>
                        <span>
                          {text('Current model preview', '当前模型预览')}
                        </span>
                      </button>
                    </li>
                  </ul>
                </section>
                <section
                  className={styles.fileSection}
                  aria-labelledby="files-heading"
                >
                  <div className={styles.sectionHeading}>
                    <h2 id="files-heading">
                      {text('This generation', '本次生成')}
                    </h2>
                    <div className={styles.fileHeadingActions}>
                      <span>{files.length}</span>
                      {files.length === 0 ? null : (
                        <button onClick={toggleAllFiles} type="button">
                          {allFilesSelected
                            ? text('Clear selection', '清除选择')
                            : text('Select all', '全选')}
                        </button>
                      )}
                    </div>
                  </div>
                  {files.length === 0 ? (
                    <p className={styles.fileEmpty}>
                      {text(
                        'Files appear after the brief and source are saved.',
                        '需求和源文件保存后，文件会显示在这里。',
                      )}
                    </p>
                  ) : (
                    <ul className={styles.fileTree}>
                      {files.map((file) => (
                        <li key={file.id}>
                          <label
                            className={styles.fileTreeCheckbox}
                            title={text('Select for download', '勾选以下载')}
                          >
                            <input
                              aria-label={text(
                                `Select ${file.label} for download`,
                                `勾选 ${file.label} 以下载`,
                              )}
                              checked={selectedFileIds.has(file.id)}
                              onChange={() => toggleFileSelection(file.id)}
                              type="checkbox"
                            />
                          </label>
                          <button
                            aria-current={selectedFileId === file.id}
                            onClick={() => setSelectedFileId(file.id)}
                            title={file.path}
                            type="button"
                          >
                            <span className={styles.fileIcon}>
                              {file.category === 'source'
                                ? 'PY'
                                : file.category === 'model'
                                  ? '3D'
                                  : '{}'}
                            </span>
                            <span>{file.label}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            )}
          </>
        )}
      </aside>

      {storageOpen ? (
        <aside
          aria-label={text('Browser storage', '浏览器存储')}
          className={styles.storageDrawer}
          data-open="true"
        >
          <section
            className={styles.storageSection}
            aria-labelledby="opfs-heading"
          >
            <div className={styles.storageDrawerHeader}>
              <div className={styles.sectionHeading}>
                <div>
                  <h2 id="opfs-heading">
                    {text('Browser storage', '浏览器存储')}
                  </h2>
                  <small>
                    {text(
                      'Models and execution records for this origin',
                      '此来源下的模型和执行记录',
                    )}
                  </small>
                </div>
                <button
                  aria-label={text('Refresh storage', '刷新存储')}
                  className={styles.storageRefreshButton}
                  disabled={opfsLoading}
                  onClick={() => void refreshOpfsFiles()}
                  title={text('Refresh storage', '刷新存储')}
                  type="button"
                >
                  {opfsLoading ? (
                    <LoadingSpinner />
                  ) : (
                    <svg fill="none" focusable="false" viewBox="0 0 20 20">
                      <path d="M15.4 6.4A6.25 6.25 0 1 0 16 12" />
                      <path d="M15.5 3.5v3.25h-3.25" />
                    </svg>
                  )}
                </button>
              </div>
              <button
                aria-label={t('closeStoragePanel')}
                className={styles.storageDrawerClose}
                onClick={() => onStorageOpenChange?.(false)}
                type="button"
              >
                <svg fill="none" focusable="false" viewBox="0 0 20 20">
                  <path d="m5.25 5.25 9.5 9.5M14.75 5.25l-9.5 9.5" />
                </svg>
              </button>
            </div>
            <div className={styles.storageViewport}>
              {opfsError === undefined ? null : (
                <p className={styles.storageError} role="alert">
                  {opfsError}
                </p>
              )}
              {opfsLoading && opfsFiles.length === 0 ? (
                <p className={styles.fileEmpty}>
                  {text('Reading OPFS files…', '正在读取 OPFS 文件…')}
                </p>
              ) : opfsFiles.length === 0 ? (
                <div className={styles.emptyState}>
                  <strong>
                    {text('No stored CAD files.', '没有已存储的 CAD 文件。')}
                  </strong>
                  <span>
                    {text(
                      'Generate a model to create local OPFS records.',
                      '生成模型后会创建本地 OPFS 记录。',
                    )}
                  </span>
                </div>
              ) : (
                <div className={styles.storageGroups}>
                  {opfsProjects.map((project) => renderOpfsProject(project))}
                </div>
              )}
            </div>
            <footer className={styles.storageActions}>
              <div className={styles.storageSelectionBar}>
                <span>
                  {selectedOpfsPaths.size === 0
                    ? text('Select files', '选择文件')
                    : `${String(selectedOpfsPaths.size)} selected · ${formatByteLength(selectedOpfsBytes)}`}
                </span>
                <button
                  disabled={
                    cleanupEligibleOpfsFiles.length === 0 || cleanupPending
                  }
                  onClick={toggleAllOpfsFiles}
                  type="button"
                >
                  {allOpfsFilesSelected
                    ? text('Clear selection', '清除选择')
                    : text('Select all', '全选')}
                </button>
              </div>
              {selectedOpfsPaths.size === 0 ? null : (
                <div className={styles.cleanupConfirm} ref={cleanupConfirmRef}>
                  <p className={styles.cleanupWarning}>
                    {text('Enter', '输入')} <strong>DELETE</strong>{' '}
                    {text(
                      'to confirm permanent deletion. Affected runs may no longer restore.',
                      '以确认永久删除。受影响的执行可能无法恢复。',
                    )}
                  </p>
                  <div>
                    <label
                      className={styles.srOnly}
                      htmlFor="cleanup-confirmation"
                    >
                      Enter DELETE to confirm permanent deletion
                    </label>
                    <input
                      autoComplete="off"
                      id="cleanup-confirmation"
                      onChange={(event) =>
                        setCleanupConfirmation(event.target.value)
                      }
                      placeholder="DELETE"
                      value={cleanupConfirmation}
                    />
                    <button
                      disabled={
                        cleanupPending || cleanupConfirmation !== 'DELETE'
                      }
                      onClick={() => void cleanupSelectedOpfsFiles()}
                      type="button"
                    >
                      {cleanupPending
                        ? text('Cleaning…', '清理中…')
                        : text('Clean selected', '清理所选文件')}
                    </button>
                  </div>
                </div>
              )}
            </footer>
          </section>
        </aside>
      ) : null}

      <div
        aria-disabled={leftCollapsed}
        aria-label="Resize conversation panel"
        aria-orientation="vertical"
        aria-valuemax={panelMaximum('left')}
        aria-valuemin={LEFT_PANEL_MIN}
        aria-valuenow={leftWidth}
        className={styles.panelResizer}
        data-side="left"
        onKeyDown={(event) => resizeWithKeyboard('left', event)}
        onPointerCancel={finishResize}
        onPointerDown={(event) => beginResize('left', event)}
        onPointerMove={continueResize}
        onPointerUp={finishResize}
        role="separator"
        tabIndex={leftCollapsed ? -1 : 0}
      >
        <span aria-hidden="true" />
      </div>

      <section
        className={styles.centerPanel}
        aria-labelledby="preview-heading"
        ref={centerPanelRef}
      >
        <header className={styles.canvasToolbar}>
          <div className={styles.canvasHeading}>
            <div className={styles.canvasHeadingCopy}>
              <h2 id="preview-heading">
                {opfsPreview?.file.fileName ??
                  selectedFile?.label ??
                  text('Model preview', '模型预览')}
              </h2>
              <span className={styles.canvasLabel}>
                {opfsPreview?.file.path ??
                  selectedFile?.path ??
                  text('Current verified model', '当前已验证模型')}
              </span>
            </div>
          </div>
          <div className={styles.canvasMeta}>
            <div
              className={styles.viewerSummarySlot}
              ref={setViewerToolbarTarget}
            />
            {opfsPreview === undefined ? null : (
              <button
                className={styles.clearPreview}
                onClick={() => setOpfsPreview(undefined)}
                type="button"
              >
                {text('Clear preview', '关闭预览')}
              </button>
            )}
            {previewLayoutAvailable ? (
              <div
                aria-label={text('Preview layout', '预览布局')}
                className={styles.previewLayoutToggle}
                role="group"
              >
                <button
                  aria-pressed={previewLayout === 'assembled'}
                  onClick={() => setPreviewLayout('assembled')}
                  type="button"
                >
                  {text('Assembled', '装配')}
                </button>
                <button
                  aria-pressed={previewLayout === 'separated'}
                  onClick={() => setPreviewLayout('separated')}
                  title={text(
                    'Viewer-only transforms; measurements are disabled.',
                    '仅改变查看器显示坐标；测量功能会禁用。',
                  )}
                  type="button"
                >
                  {text('Exploded', '爆炸')}
                </button>
              </div>
            ) : null}
            {rebuilding ? (
              <span className={styles.buildingState}>
                <LoadingSpinner />
                Rebuilding…
              </span>
            ) : null}
            <span className={styles.phase}>
              {workspaceSnapshot?.workflowKind ??
                contextRef.current?.workflowKind ??
                'unselected'}
            </span>
          </div>
        </header>

        <div className={styles.canvasBody}>
          {opfsPreview !== undefined ? (
            opfsPreview.model !== undefined ? (
              <CadViewerShell
                model={displayedOpfsModel ?? opfsPreview.model}
                toolbarTarget={viewerToolbarTarget}
              />
            ) : opfsPreview.text !== undefined ? (
              <pre className={styles.codePreview} tabIndex={0}>
                <code>{opfsPreview.text}</code>
              </pre>
            ) : null
          ) : selectedFileId === 'model:preview' ? (
            <CadViewerShell
              {...(displayedModel === undefined
                ? {}
                : { model: displayedModel })}
              toolbarTarget={viewerToolbarTarget}
            />
          ) : selectedFile?.category === 'model' ? (
            selectedModelPart !== undefined ? (
              <CadViewerShell
                model={displayedSelectedModelPart ?? selectedModelPart}
                toolbarTarget={viewerToolbarTarget}
              />
            ) : displayedModel !== undefined ? (
              <CadViewerShell
                model={displayedModel}
                toolbarTarget={viewerToolbarTarget}
              />
            ) : (
              <div className={styles.emptyCanvas}>
                <strong>
                  {text('Select a generated file.', '请选择一个生成的文件。')}
                </strong>
                <span>
                  {text(
                    'The selected model could not be loaded into the viewer.',
                    '所选模型无法载入 3D 查看器。',
                  )}
                </span>
              </div>
            )
          ) : selectedFile?.text !== undefined ? (
            <pre className={styles.codePreview} tabIndex={0}>
              <code>{selectedFile.text}</code>
            </pre>
          ) : (
            <div className={styles.emptyCanvas}>
              <strong>
                {text('Select a generated file.', '请选择一个生成的文件。')}
              </strong>
              <span>
                {text(
                  'Source and JSON open here; STL and GLB use the 3D viewer.',
                  '源文件和 JSON 会在此打开；STL 和 GLB 使用 3D 查看器。',
                )}
              </span>
            </div>
          )}

          {pendingReview === undefined ? null : (
            <aside className={styles.reviewPrompt} aria-live="assertive">
              <strong>Visual review requested</strong>
              <p>{pendingReview.input.reviewFocus}</p>
              <div className={styles.actions}>
                <button
                  onClick={() =>
                    decideVisualReview({
                      passed: true,
                      summary: 'The user approved the browser preview.',
                    })
                  }
                  type="button"
                >
                  Approve preview
                </button>
                <button
                  className={styles.secondary}
                  onClick={() =>
                    decideVisualReview({
                      passed: false,
                      summary: 'The user rejected the browser preview.',
                    })
                  }
                  type="button"
                >
                  Request repair
                </button>
              </div>
            </aside>
          )}

          {writebackPreview === undefined ? null : (
            <aside
              className={styles.diffPanel}
              aria-labelledby="source-diff-heading"
            >
              <div className={styles.sectionHeading}>
                <div>
                  <span className={styles.canvasLabel}>
                    Creates a new revision
                  </span>
                  <h2 id="source-diff-heading">Confirm source writeback</h2>
                </div>
                <button
                  onClick={() => setWritebackPreview(undefined)}
                  type="button"
                >
                  Close
                </button>
              </div>
              <pre>
                {writebackPreview.diff.map((line, index) => (
                  <code
                    data-kind={line.kind}
                    key={`${line.kind}-${String(index)}`}
                  >
                    {line.kind === 'added'
                      ? '+ '
                      : line.kind === 'removed'
                        ? '− '
                        : '  '}
                    {line.text}
                    {'\n'}
                  </code>
                ))}
              </pre>
              {writebackQaFailed ? (
                <div className={styles.writebackWarning} role="alert">
                  <strong>
                    {text(
                      'QA did not pass for these values.',
                      '这些数值未通过 QA。',
                    )}
                  </strong>
                  <span>
                    {text(
                      'Writing them into the source will create a new revision that may not build cleanly. Tick “Force write” to save them anyway.',
                      '写入源文件会创建新修订，可能无法干净构建。勾选“强制写入”仍可保存。',
                    )}
                  </span>
                  <label className={styles.forceWriteback}>
                    <input
                      checked={forceWriteback}
                      onChange={(event) =>
                        setForceWriteback(event.target.checked)
                      }
                      type="checkbox"
                    />
                    <span>{text('Force write', '强制写入')}</span>
                  </label>
                </div>
              ) : null}
              <div className={styles.actions}>
                <button
                  onClick={() => void confirmSourceWriteback()}
                  type="button"
                >
                  {writebackQaFailed
                    ? text('Force create source revision', '强制创建源码修订')
                    : text('Create source revision', '创建源码修订')}
                </button>
                <button
                  className={styles.secondary}
                  onClick={() => setWritebackPreview(undefined)}
                  type="button"
                >
                  Keep overrides only
                </button>
              </div>
            </aside>
          )}
        </div>

        <div
          aria-disabled={logCollapsed}
          aria-label="Resize activity log"
          aria-orientation="horizontal"
          aria-valuemax={logPanelMaximum()}
          aria-valuemin={LOG_PANEL_MIN}
          aria-valuenow={logHeight}
          className={styles.logResizer}
          onKeyDown={resizeLogWithKeyboard}
          onPointerCancel={finishLogResize}
          onPointerDown={beginLogResize}
          onPointerMove={continueLogResize}
          onPointerUp={finishLogResize}
          role="separator"
          tabIndex={logCollapsed ? -1 : 0}
        >
          <span aria-hidden="true" />
        </div>

        <section
          aria-labelledby="activity-log-heading"
          className={`${styles.activityLog} ${logCollapsed ? styles.activityLogCollapsed : ''}`}
        >
          <header className={styles.activityLogHeader}>
            <div>
              <strong id="activity-log-heading">
                {text('Activity log', '活动日志')}
              </strong>
              <small>
                {runtimeEvents.length === 0
                  ? text('waiting for runtime events', '等待运行时事件')
                  : text(
                      `${String(runtimeEvents.length)} runtime events`,
                      `${String(runtimeEvents.length)} 条运行时事件`,
                    )}
              </small>
            </div>
            <button
              aria-expanded={!logCollapsed}
              aria-label={
                logCollapsed ? 'Expand activity log' : 'Collapse activity log'
              }
              onClick={() => setLogCollapsed((collapsed) => !collapsed)}
              type="button"
            >
              {logCollapsed ? '⌃' : '⌄'}
            </button>
          </header>

          {logCollapsed ? null : (
            <div className={styles.activityLogBody}>
              {runtimeEvents.length === 0 ? (
                <p className={styles.runtimeEventsEmpty}>
                  {text(
                    'CAD bootstrap and build milestones will appear here.',
                    'CAD 启动和构建里程碑会显示在这里。',
                  )}
                </p>
              ) : (
                <ol className={styles.runtimeEvents}>
                  {runtimeEvents.map(({ id, occurredAt, event }) => (
                    <li
                      data-level={event.type === 'log' ? event.level : 'info'}
                      key={id}
                    >
                      <time dateTime={new Date(occurredAt).toISOString()}>
                        {new Intl.DateTimeFormat(
                          language === 'zh' ? 'zh-CN' : 'en-US',
                          {
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          },
                        ).format(occurredAt)}
                      </time>
                      <span>
                        {event.type === 'progress'
                          ? `${event.stage} · ${String(Math.round(event.progress * 100))}%`
                          : event.level}
                      </span>
                      <p>
                        {event.type === 'progress' ? event.message : event.line}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
              <div className={styles.platformNotices}>
                <p>{t('compatibilityNotice')}</p>
                <p>
                  {workspaceSnapshot?.workflowSelectionReason ??
                    text(
                      'Workflow is selected before briefing and then frozen.',
                      '工作流会在需求整理前选定，随后保持不变。',
                    )}
                </p>
              </div>
            </div>
          )}
        </section>
      </section>

      <div
        aria-disabled={rightCollapsed}
        aria-label="Resize parameter panel"
        aria-orientation="vertical"
        aria-valuemax={panelMaximum('right')}
        aria-valuemin={RIGHT_PANEL_MIN}
        aria-valuenow={rightWidth}
        className={styles.panelResizer}
        data-side="right"
        onKeyDown={(event) => resizeWithKeyboard('right', event)}
        onPointerCancel={finishResize}
        onPointerDown={(event) => beginResize('right', event)}
        onPointerMove={continueResize}
        onPointerUp={finishResize}
        role="separator"
        tabIndex={rightCollapsed ? -1 : 0}
      >
        <span aria-hidden="true" />
      </div>

      <aside
        aria-label="Model parameters and exports"
        className={`${styles.rightPanel} ${rightCollapsed ? styles.collapsedPanel : ''}`}
      >
        <header className={styles.panelHeader}>
          <div className={styles.panelTitle}>
            <button
              className={styles.panelCollapseButton}
              aria-expanded={!rightCollapsed}
              aria-label={
                rightCollapsed
                  ? 'Open parameter panel'
                  : 'Collapse parameter panel'
              }
              onClick={() => setRightCollapsed((collapsed) => !collapsed)}
              type="button"
            >
              {rightCollapsed ? '‹' : '›'}
            </button>
            <div>
              <strong>{text('Parameters', '参数')}</strong>
              <small>
                {parameters?.parameters.length ?? 0}{' '}
                {text('discovered literals', '个已发现的字面量')}
              </small>
            </div>
          </div>
        </header>

        {rightCollapsed ? null : (
          <>
            <div className={styles.parameterScroll}>
              {parameterScaleNotice === undefined ? null : (
                <div aria-live="polite" className={styles.parameterScaleNotice}>
                  <strong>
                    {text(
                      'Linked parameters were scaled',
                      '关联参数已等比缩放',
                    )}
                  </strong>
                  <span>
                    {text(
                      'Modeling has a hard constraint on these parameters, so scaling',
                      '建模对这些参数存在硬约束，调整',
                    )}{' '}
                    <code>{parameterScaleNotice.changedName}</code>{' '}
                    {text('also scaled', '时同时等比缩放')}{' '}
                    {parameterScaleNotice.scaledNames.map((name, index) => (
                      <code key={name}>
                        {name}
                        {index < parameterScaleNotice.scaledNames.length - 1
                          ? ', '
                          : ''}
                      </code>
                    ))}
                    .
                  </span>
                  <small>{parameterScaleNotice.source}</small>
                </div>
              )}
              {parameterBuildIssue === undefined ? null : (
                <div
                  aria-live="assertive"
                  className={styles.parameterBuildIssueBanner}
                  role="alert"
                >
                  <strong>
                    {parameterBuildIssue.kind === 'build-error'
                      ? text('Parameter rebuild failed', '参数重建失败')
                      : text(
                          'Parameter rebuild did not pass QA',
                          '参数重建未通过 QA',
                        )}
                  </strong>
                  <pre
                    className={styles.parameterBuildIssueDetail}
                    tabIndex={0}
                  >
                    {parameterBuildIssue.message}
                  </pre>
                  <div className={styles.parameterBuildIssueActions}>
                    <button
                      onClick={() =>
                        void navigator.clipboard
                          ?.writeText(parameterBuildIssue.message)
                          .catch(() => undefined)
                      }
                      type="button"
                    >
                      {text('Copy error', '复制错误')}
                    </button>
                    <button
                      onClick={() => setParameterBuildIssue(undefined)}
                      type="button"
                    >
                      {text('Dismiss', '关闭')}
                    </button>
                  </div>
                </div>
              )}
              {parameterGroups.length === 0 ? (
                <div className={styles.emptyState}>
                  <strong>
                    {text(
                      'No adjustable literals yet.',
                      '暂时没有可调字面量。',
                    )}
                  </strong>
                  <span>
                    {text(
                      'The Agent’s top-level uppercase constants will appear after it writes model.py.',
                      'Agent 写入 model.py 后，顶层大写常量会显示在这里。',
                    )}
                  </span>
                </div>
              ) : (
                parameterGroups.map(([group, groupParameters]) => (
                  <fieldset className={styles.parameterGroup} key={group}>
                    <legend>{group}</legend>
                    {groupParameters?.map((parameter) => (
                      <div
                        className={styles.parameterField}
                        key={parameter.name}
                      >
                        <label htmlFor={`parameter-${parameter.name}`}>
                          <span>{parameter.label}</span>
                          <code>{parameter.name}</code>
                        </label>
                        {parameter.type === 'boolean' ? (
                          <label className={styles.switchControl}>
                            <input
                              checked={Boolean(parameter.value)}
                              disabled={!parameterEditingEnabled}
                              id={`parameter-${parameter.name}`}
                              onChange={(event) =>
                                updateParameter(
                                  parameter.name,
                                  event.target.checked,
                                )
                              }
                              type="checkbox"
                            />
                            <span>
                              {parameter.value
                                ? text('Enabled', '已启用')
                                : text('Disabled', '已停用')}
                            </span>
                          </label>
                        ) : parameter.type === 'number' ? (
                          <>
                            <div className={styles.numberControl}>
                              <input
                                disabled={!parameterEditingEnabled}
                                id={`parameter-${parameter.name}`}
                                max={parameter.maximum}
                                min={parameter.minimum}
                                onChange={(event) => {
                                  const value = Number(event.target.value);
                                  if (Number.isFinite(value))
                                    updateParameter(parameter.name, value);
                                }}
                                step={parameter.step ?? 'any'}
                                type="number"
                                value={Number(parameter.value)}
                              />
                              <span>{parameter.unit ?? 'value'}</span>
                            </div>
                            {parameter.minimum !== undefined &&
                            parameter.maximum !== undefined ? (
                              <input
                                aria-label={`${parameter.label} slider`}
                                disabled={!parameterEditingEnabled}
                                max={parameter.maximum}
                                min={parameter.minimum}
                                onChange={(event) =>
                                  updateParameter(
                                    parameter.name,
                                    Number(event.target.value),
                                  )
                                }
                                step={parameter.step ?? 1}
                                type="range"
                                value={Number(parameter.value)}
                              />
                            ) : null}
                          </>
                        ) : (
                          <input
                            disabled={!parameterEditingEnabled}
                            id={`parameter-${parameter.name}`}
                            onChange={(event) =>
                              updateParameter(
                                parameter.name,
                                event.target.value,
                              )
                            }
                            type="text"
                            value={String(parameter.value)}
                          />
                        )}
                        <small>
                          {parameter.description ??
                            ([
                              parameter.minimum === undefined
                                ? undefined
                                : `min ${String(parameter.minimum)}`,
                              parameter.maximum === undefined
                                ? undefined
                                : `max ${String(parameter.maximum)}`,
                              parameter.step === undefined
                                ? undefined
                                : `step ${String(parameter.step)}`,
                            ]
                              .filter(Boolean)
                              .join(' · ') ||
                              'Top-level literal override')}
                        </small>
                        {parameterBuildIssue?.names.includes(parameter.name) ? (
                          <small
                            aria-live="polite"
                            className={styles.parameterBuildIssue}
                            role="alert"
                          >
                            {parameterBuildIssue.kind === 'build-error'
                              ? text(
                                  'This value caused a build error. The preview keeps the last valid model.',
                                  '此值导致构建错误，预览保留上次有效模型。',
                                )
                              : text(
                                  'This change rebuilt but did not pass QA.',
                                  '此改动已重建但未通过 QA。',
                                )}
                          </small>
                        ) : null}
                      </div>
                    ))}
                  </fieldset>
                ))
              )}

              {workspaceSnapshot?.designBrief?.colorRegionPlan?.regions
                .length ? (
                <section
                  className={styles.colorPlan}
                  aria-labelledby="color-plan-heading"
                >
                  <h2 id="color-plan-heading">
                    {text('Color regions', '颜色区域')}
                  </h2>
                  <ul>
                    {workspaceSnapshot.designBrief.colorRegionPlan.regions.map(
                      (region) => (
                        <li key={region.id}>
                          <span
                            style={
                              { '--region-color': region.hex } as CSSProperties
                            }
                            aria-hidden="true"
                          />
                          <div>
                            <strong>{region.name}</strong>
                            <small>
                              {region.hex} ·{' '}
                              {region.filament ?? region.colorName}
                            </small>
                          </div>
                        </li>
                      ),
                    )}
                  </ul>
                </section>
              ) : null}

              {workspaceSnapshot?.revisions.length ? (
                <details className={styles.revisions}>
                  <summary>
                    {text('Source revisions', '源文件修订')} ·{' '}
                    {workspaceSnapshot.revisions.length}
                  </summary>
                  <ol>
                    {[...workspaceSnapshot.revisions]
                      .reverse()
                      .map((revision, index) => (
                        <li key={revision.id}>
                          <div>
                            <strong>
                              {text('Revision', '修订')} {revision.revision}
                            </strong>
                            <small>
                              {new Date(revision.createdAt).toLocaleString()}
                            </small>
                          </div>
                          <button
                            disabled={index === 0}
                            onClick={() => void restoreRevision(revision.id)}
                            type="button"
                          >
                            {index === 0
                              ? text('Current', '当前')
                              : text('Restore', '恢复')}
                          </button>
                        </li>
                      ))}
                  </ol>
                </details>
              ) : null}

              {workspaceSnapshot?.artifacts.length ? (
                <section
                  className={styles.exports}
                  aria-labelledby="exports-heading"
                >
                  <h2 id="exports-heading">{text('Downloads', '下载')}</h2>
                  <ul>
                    {workspaceSnapshot.artifacts
                      .filter(
                        ({ metadata }) => metadata.kind !== 'model-source',
                      )
                      .map((artifact) => (
                        <li key={artifact.metadata.id}>
                          <div>
                            <strong>{artifact.metadata.fileName}</strong>
                            <small>
                              {artifact.metadata.kind} ·{' '}
                              {Math.ceil(artifact.metadata.byteLength / 1024)}{' '}
                              KB
                            </small>
                          </div>
                          <button
                            onClick={() =>
                              downloadBytes(
                                artifact.metadata.fileName,
                                artifact.metadata.mediaType,
                                artifact.bytes,
                              )
                            }
                            type="button"
                          >
                            {text('Download', '下载')}
                          </button>
                        </li>
                      ))}
                  </ul>
                </section>
              ) : null}
            </div>

            <footer className={styles.parameterActions}>
              <div className={styles.historyActions}>
                <button
                  disabled={
                    !parameterEditingEnabled ||
                    (parameters?.historyCursor ?? 0) === 0
                  }
                  onClick={undoParameters}
                  type="button"
                >
                  {text('Undo', '撤销')}
                </button>
                <button
                  disabled={
                    !parameterEditingEnabled ||
                    (parameters?.historyCursor ?? 0) >=
                      (parameters?.history.length ?? 0)
                  }
                  onClick={redoParameters}
                  type="button"
                >
                  {text('Redo', '重做')}
                </button>
                <button
                  disabled={!parameterEditingEnabled || !hasParameterChanges}
                  onClick={resetAllParameters}
                  type="button"
                >
                  {text('Reset', '重置')}
                </button>
              </div>
              <button
                disabled={
                  !parameterEditingEnabled || rebuilding || !hasParameterChanges
                }
                aria-disabled={
                  !parameterEditingEnabled || rebuilding || !hasParameterChanges
                }
                onClick={previewSourceWriteback}
                type="button"
              >
                {text('Write values to source', '将数值写入源文件')}
              </button>
              <p>
                {text(
                  'Back up the project ZIP before clearing Chrome site data.',
                  '清除 Chrome 站点数据前，请先备份项目 ZIP。',
                )}
              </p>
            </footer>
          </>
        )}
      </aside>

      {addModelDialogOpen ? (
        <div className={styles.modelDialogBackdrop}>
          <section
            aria-describedby="add-model-dialog-description"
            aria-labelledby="add-model-dialog-title"
            aria-modal="true"
            className={styles.modelDialog}
            role="dialog"
          >
            <header className={styles.modelDialogHeader}>
              <div>
                <span className={styles.modelDialogEyebrow}>
                  {text('Model profile', '模型配置')}
                </span>
                <h2 id="add-model-dialog-title">
                  {text('Add model', '添加模型')}
                </h2>
              </div>
              <button
                aria-label="Close add model dialog"
                className={styles.modelDialogClose}
                onClick={() => setAddModelDialogOpen(false)}
                type="button"
              >
                ×
              </button>
            </header>
            <p
              id="add-model-dialog-description"
              className={styles.modelDialogDescription}
            >
              {text(
                'Save a provider model ID locally, then validate it before using it in a run.',
                '在本地保存提供商模型 ID，验证通过后即可在执行中使用。',
              )}
            </p>
            <form
              className={styles.modelDialogForm}
              onSubmit={submitAddModelProfile}
            >
              <label>
                <span>{text('Model ID', '模型 ID')}</span>
                <input
                  autoFocus
                  autoComplete="off"
                  className={styles.modelDialogInput}
                  onChange={(event) =>
                    setAddModelDialogDraft((current) => ({
                      ...current,
                      modelId: event.target.value,
                    }))
                  }
                  placeholder="provider/model-name"
                  value={addModelDialogDraft.modelId}
                />
              </label>
              <label>
                <span>{text('Display name', '显示名称')}</span>
                <input
                  autoComplete="off"
                  className={styles.modelDialogInput}
                  onChange={(event) =>
                    setAddModelDialogDraft((current) => ({
                      ...current,
                      displayName: event.target.value,
                    }))
                  }
                  placeholder={text(
                    'A short name for this model',
                    '模型的简短名称',
                  )}
                  value={addModelDialogDraft.displayName}
                />
              </label>
              <label className={styles.modelDialogCheckRow}>
                <input
                  checked={addModelDialogDraft.imageInput}
                  onChange={(event) =>
                    setAddModelDialogDraft((current) => ({
                      ...current,
                      imageInput: event.target.checked,
                    }))
                  }
                  type="checkbox"
                />
                <span>
                  <strong>
                    {text('Supports image input', '支持图像输入')}
                  </strong>
                  <small>
                    {text(
                      'Enable only if this provider model accepts images.',
                      '仅当该提供商模型支持图像时启用。',
                    )}
                  </small>
                </span>
              </label>
              {addModelDialogError === undefined ? null : (
                <p className={styles.modelDialogError} role="alert">
                  {addModelDialogError}
                </p>
              )}
              <footer className={styles.modelDialogActions}>
                <button
                  className={styles.secondary}
                  onClick={() => setAddModelDialogOpen(false)}
                  type="button"
                >
                  {text('Cancel', '取消')}
                </button>
                <button
                  className={styles.addModelButton}
                  disabled={addModelDialogSaving}
                  type="submit"
                >
                  {addModelDialogSaving
                    ? text('Adding…', '添加中…')
                    : text('Add model', '添加模型')}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
