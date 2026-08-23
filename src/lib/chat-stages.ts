import type { ChatStage } from '../types';

export const RUN_STAGES_CUSTOM_TYPE = 'amagine3d.run-stages.v1';

export function startChatStage(
  current: readonly ChatStage[],
  next: ChatStage,
): ChatStage[] {
  const previous = current.at(-1);
  if (
    previous?.status === 'running' &&
    previous.label === next.label &&
    previous.stage === next.stage
  ) {
    return [...current];
  }
  return [
    ...current.map((stage) =>
      stage.status === 'running' ? { ...stage, status: 'completed' as const } : stage,
    ),
    next,
  ].slice(-100);
}

export function finishChatStages(
  current: readonly ChatStage[],
  status: 'cancelled' | 'completed' | 'failed',
): ChatStage[] {
  return current.map((stage) =>
    stage.status === 'running' ? { ...stage, status } : stage,
  );
}

export function restoredChatStages(value: unknown): ChatStage[] {
  if (!value || typeof value !== 'object') return [];
  const stages = (value as { stages?: unknown }).stages;
  if (!Array.isArray(stages)) return [];
  return stages.flatMap((stage): ChatStage[] => {
    if (!stage || typeof stage !== 'object') return [];
    const item = stage as Partial<ChatStage>;
    if (
      typeof item.id !== 'string' ||
      typeof item.label !== 'string' ||
      typeof item.occurredAt !== 'number' ||
      typeof item.stage !== 'string' ||
      !['cancelled', 'completed', 'failed', 'running'].includes(item.status ?? '')
    ) {
      return [];
    }
    return [item as ChatStage];
  });
}
