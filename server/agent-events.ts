import type { AgentSessionEvent } from '@amagine3d/a3d-runtime';

export type AssistantMessageOutcome =
  | { status: 'error'; message: string }
  | { status: 'success' };

export function assistantMessageOutcome(
  event: AgentSessionEvent,
): AssistantMessageOutcome | undefined {
  if (event.type === 'message_end' && event.message.role === 'assistant') {
    if (
      event.message.stopReason === 'error' ||
      event.message.stopReason === 'aborted'
    ) {
      return {
        status: 'error',
        message:
          event.message.errorMessage ||
          (event.message.stopReason === 'aborted'
            ? 'Model request was aborted.'
            : 'Model request failed.'),
      };
    }
    return { status: 'success' };
  }

  if (
    event.type === 'message_update' &&
    event.assistantMessageEvent.type === 'error'
  ) {
    return {
      status: 'error',
      message:
        event.assistantMessageEvent.error.errorMessage ||
        'Model request failed.',
    };
  }

  return undefined;
}
