import type { ThreadEvent } from '@openai/codex-sdk';

export type CadCompileProgressStatus =
  | 'fail'
  | 'pass'
  | 'running'
  | 'timeout';

export interface CadCompileProgress {
  commandId: string;
  elapsedMs?: number;
  stage: string;
  status: CadCompileProgressStatus;
}

interface CommandProgressState {
  lastOutput: string;
  pending: string;
  seen: Set<string>;
}

const COMPILE_COMMAND = /\ba3d\s+compile\b/u;
const MAX_PROGRESS_LINE_LENGTH = 2_048;
const MAX_PENDING_LENGTH = 4_096;
const STAGE = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/u;
const STATUSES = new Set<CadCompileProgressStatus>([
  'fail',
  'pass',
  'running',
  'timeout',
]);

function progressFromLine(
  commandId: string,
  line: string,
): CadCompileProgress | undefined {
  if (!line || line.length > MAX_PROGRESS_LINE_LENGTH) return undefined;
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return undefined;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const item = value as Record<string, unknown>;
  if (
    item.schema !== 'cad-compile-progress/v1' ||
    typeof item.stage !== 'string' ||
    !STAGE.test(item.stage) ||
    typeof item.status !== 'string' ||
    !STATUSES.has(item.status as CadCompileProgressStatus)
  ) {
    return undefined;
  }
  if (
    item.elapsedMs !== undefined &&
    (typeof item.elapsedMs !== 'number' ||
      !Number.isFinite(item.elapsedMs) ||
      item.elapsedMs < 0)
  ) {
    return undefined;
  }
  return {
    commandId,
    ...(item.elapsedMs === undefined ? {} : { elapsedMs: item.elapsedMs }),
    stage: item.stage,
    status: item.status as CadCompileProgressStatus,
  };
}

export class CompileProgressExtractor {
  private readonly commands = new Map<string, CommandProgressState>();

  extract(event: ThreadEvent): CadCompileProgress[] {
    if (
      !(
        event.type === 'item.started' ||
        event.type === 'item.updated' ||
        event.type === 'item.completed'
      ) ||
      event.item.type !== 'command_execution'
    ) {
      return [];
    }

    const { item } = event;
    if (!COMPILE_COMMAND.test(item.command)) {
      if (event.type === 'item.completed') this.commands.delete(item.id);
      return [];
    }

    const state = this.commands.get(item.id) ?? {
      lastOutput: '',
      pending: '',
      seen: new Set<string>(),
    };
    this.commands.set(item.id, state);

    let chunk = '';
    if (item.aggregated_output !== state.lastOutput) {
      if (item.aggregated_output.startsWith(state.lastOutput)) {
        chunk = item.aggregated_output.slice(state.lastOutput.length);
        state.lastOutput = item.aggregated_output;
      } else if (!state.lastOutput.startsWith(item.aggregated_output)) {
        chunk = item.aggregated_output;
        state.lastOutput = item.aggregated_output;
      }
    }

    const flush = event.type === 'item.completed';
    const combined = `${state.pending}${chunk}`;
    const lines = combined.split(/\r?\n/u);
    state.pending = flush ? '' : (lines.pop() ?? '');
    if (state.pending.length > MAX_PENDING_LENGTH) state.pending = '';

    const progress: CadCompileProgress[] = [];
    for (const line of lines) {
      const parsed = progressFromLine(item.id, line);
      if (!parsed) continue;
      const key = JSON.stringify([
        parsed.stage,
        parsed.status,
        parsed.elapsedMs ?? null,
      ]);
      if (state.seen.has(key)) continue;
      state.seen.add(key);
      progress.push(parsed);
    }

    if (flush) this.commands.delete(item.id);
    return progress;
  }
}
