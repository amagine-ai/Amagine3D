import type { AgentSessionEvent } from '@amagine3d/a3d-runtime';

export const FIRST_BUILD_REMINDER = [
  '<first_build_reminder>',
  '本轮 CAD 任务尚未观察到首次正式编译。请减少继续阅读和规划，尽快调用 cad_compile 工具编译当前设计，以便用真实几何反馈继续完善模型。',
  '这只是一次软提醒：不是阶段切换，不要求生成粗糙占位模型，也不会放宽最终 QA 或视觉审计。',
  '</first_build_reminder>',
].join('\n');

export class FirstBuildReminder {
  private readonly activeCompileCalls = new Set<string>();
  private buildObserved = false;
  private readonly deadlineAt: number;
  private finished = false;
  private reminded = false;
  private timer: NodeJS.Timeout | undefined;

  constructor(
    private readonly delayMs: number,
    private readonly remind: () => Promise<void> | void,
  ) {
    this.deadlineAt = Date.now() + delayMs;
    this.schedule(delayMs);
  }

  observe(event: AgentSessionEvent): void {
    if (isBuildExecutionStart(event)) {
      this.activeCompileCalls.add(event.toolCallId);
      this.clearTimer();
      return;
    }
    if (
      event.type === 'tool_execution_end' &&
      event.toolName === 'cad_compile' &&
      this.activeCompileCalls.delete(event.toolCallId)
    ) {
      const details =
        event.result && typeof event.result === 'object'
          ? (event.result as { details?: unknown }).details
          : undefined;
      if (
        details &&
        typeof details === 'object' &&
        (details as { schema?: unknown }).schema ===
          'evidence-cad-compile-result/v1'
      ) {
        this.buildObserved = true;
        this.clearTimer();
      } else if (this.activeCompileCalls.size === 0) {
        this.schedule(Math.max(0, this.deadlineAt - Date.now()));
      }
    }
  }

  finish(): void {
    if (this.finished) return;
    this.finished = true;
    this.clearTimer();
  }

  private clearTimer(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  private schedule(delayMs: number): void {
    if (
      this.delayMs <= 0 ||
      this.finished ||
      this.buildObserved ||
      this.reminded ||
      this.activeCompileCalls.size > 0
    ) {
      return;
    }
    this.clearTimer();
    this.timer = setTimeout(() => void this.remindOnce(), delayMs);
  }

  private async remindOnce(): Promise<void> {
    this.timer = undefined;
    if (this.finished || this.buildObserved || this.reminded) return;
    this.reminded = true;
    try {
      await this.remind();
    } catch {
      // A best-effort steering reminder must never fail the CAD run.
    }
  }
}

export function isBuildExecutionStart(
  event: AgentSessionEvent,
): event is Extract<AgentSessionEvent, { type: 'tool_execution_start' }> & {
  toolName: 'cad_compile';
} {
  return (
    event.type === 'tool_execution_start' && event.toolName === 'cad_compile'
  );
}
