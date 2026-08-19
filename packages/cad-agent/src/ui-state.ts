import {
  getToolName,
  isToolUIPart,
  lastAssistantMessageIsCompleteWithToolCalls,
  type UIMessage,
} from 'ai';

export const MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS = 3;

export type CadAgentToolErrorState = {
  toolCallId: string;
  toolName: string;
  errorText: string;
  consecutiveErrors: number;
  halted: boolean;
};

function automaticRetryLimit(errorText: string): number {
  // A bootstrap timeout is a host/runtime failure. Asking the model to call
  // buildAndCheck again cannot repair it and restarts the entire cold Worker.
  return errorText.includes('CAD runtime bootstrap exceeded')
    ? 1
    : MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS;
}

export function cadAgentToolErrorState(
  messages: UIMessage[],
): CadAgentToolErrorState | undefined {
  let latest:
    Omit<CadAgentToolErrorState, 'consecutiveErrors' | 'halted'> | undefined;
  let consecutiveErrors = 0;

  for (
    let messageIndex = messages.length - 1;
    messageIndex >= 0;
    messageIndex -= 1
  ) {
    const message = messages[messageIndex];
    if (message === undefined) continue;
    if (message.role === 'user') break;
    if (message.role !== 'assistant') continue;
    for (
      let partIndex = message.parts.length - 1;
      partIndex >= 0;
      partIndex -= 1
    ) {
      const part = message.parts[partIndex];
      if (part === undefined || !isToolUIPart(part)) continue;
      if (part.state === 'output-available') {
        const retryLimit =
          latest === undefined
            ? MAX_CONSECUTIVE_TOOL_OUTPUT_ERRORS
            : automaticRetryLimit(latest.errorText);
        return latest === undefined
          ? undefined
          : {
              ...latest,
              consecutiveErrors,
              halted: consecutiveErrors >= retryLimit,
            };
      }
      if (part.state !== 'output-error') continue;
      latest ??= {
        toolCallId: part.toolCallId,
        toolName: getToolName(part),
        errorText: part.errorText,
      };
      consecutiveErrors += 1;
    }
  }

  return latest === undefined
    ? undefined
    : {
        ...latest,
        consecutiveErrors,
        halted: consecutiveErrors >= automaticRetryLimit(latest.errorText),
      };
}

export function shouldContinueCadAgent({
  messages,
}: {
  messages: UIMessage[];
}): boolean {
  if (!lastAssistantMessageIsCompleteWithToolCalls({ messages })) return false;
  return cadAgentToolErrorState(messages)?.halted !== true;
}
