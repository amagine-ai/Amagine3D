import type {
  ChatStep,
  ChatTurn,
  ChatTurnTerminalStatus,
} from '../types';

export const CHAT_TURN_CUSTOM_TYPE = 'amagine3d.chat-turn.v2';

export interface ChatTurnCompletion {
  finishedAt: number;
  replyText: string;
  sourceStepId?: string;
  status: ChatTurnTerminalStatus;
}

export function emptyChatTurn(): ChatTurn {
  return { replyText: '', steps: [] };
}

export function startChatStep(
  current: ChatTurn,
  next: ChatStep,
): ChatTurn {
  return {
    ...current,
    steps: [
      ...current.steps.map((step) =>
        step.status === 'running'
          ? { ...step, status: 'completed' as const }
          : step,
      ),
      next,
    ],
  };
}

export function appendChatStepText(
  current: ChatTurn,
  stepId: string,
  content: string,
): ChatTurn {
  if (!content) return current;
  return {
    ...current,
    steps: current.steps.map((step) =>
      step.id === stepId
        ? {
            ...step,
            progressText: `${step.progressText ?? ''}${content}`,
          }
        : step,
    ),
  };
}

export function completeChatTurn(
  current: ChatTurn,
  completion: ChatTurnCompletion,
): ChatTurn {
  return {
    finishedAt: completion.finishedAt,
    replyText: completion.replyText,
    steps: current.steps.map((step) => {
      const finalized =
        step.status === 'running'
          ? { ...step, status: completion.status }
          : step;
      if (step.id !== completion.sourceStepId) return finalized;
      const { progressText: _progressText, ...withoutProgressText } = finalized;
      return withoutProgressText;
    }),
  };
}

export function restoreChatTurn(value: unknown): ChatTurn | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const item = value as Partial<ChatTurn>;
  if (
    !Array.isArray(item.steps) ||
    typeof item.replyText !== 'string' ||
    typeof item.finishedAt !== 'number' ||
    !Number.isFinite(item.finishedAt)
  ) {
    return undefined;
  }

  const steps = item.steps.flatMap((step): ChatStep[] => {
    if (!step || typeof step !== 'object') return [];
    const candidate = step as Partial<ChatStep>;
    if (
      typeof candidate.id !== 'string' ||
      typeof candidate.label !== 'string' ||
      typeof candidate.occurredAt !== 'number' ||
      !Number.isFinite(candidate.occurredAt) ||
      (candidate.progressText !== undefined &&
        typeof candidate.progressText !== 'string') ||
      typeof candidate.stage !== 'string' ||
      !['cancelled', 'completed', 'failed', 'running'].includes(
        candidate.status ?? '',
      )
    ) {
      return [];
    }
    return [candidate as ChatStep];
  });
  if (
    steps.length === 0 ||
    steps.length !== item.steps.length ||
    steps.some(({ status }) => status === 'running') ||
    item.finishedAt < steps[0]!.occurredAt
  ) {
    return undefined;
  }

  return {
    finishedAt: item.finishedAt,
    replyText: item.replyText,
    steps,
  };
}
