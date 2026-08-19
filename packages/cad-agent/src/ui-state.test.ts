import type { UIMessage } from 'ai';
import { describe, expect, it } from 'vitest';

import { cadAgentToolErrorState, shouldContinueCadAgent } from './ui-state';

function toolMessage(
  id: string,
  state: 'output-available' | 'output-error',
  errorText = `invalid brief ${id}`,
): UIMessage {
  return {
    id: `message-${id}`,
    role: 'assistant',
    parts: [
      {
        type: 'tool-saveDesignBrief',
        toolCallId: `call-${id}`,
        state,
        input: {},
        ...(state === 'output-error'
          ? { errorText }
          : { output: { accepted: true } }),
      },
    ],
  } as UIMessage;
}

describe('CAD Agent UI tool error state', () => {
  it('allows bounded automatic correction and halts after three errors', () => {
    const first = [toolMessage('1', 'output-error')];
    const second = [...first, toolMessage('2', 'output-error')];
    const third = [...second, toolMessage('3', 'output-error')];

    expect(shouldContinueCadAgent({ messages: first })).toBe(true);
    expect(shouldContinueCadAgent({ messages: second })).toBe(true);
    expect(shouldContinueCadAgent({ messages: third })).toBe(false);
    expect(cadAgentToolErrorState(third)).toMatchObject({
      consecutiveErrors: 3,
      halted: true,
      toolName: 'saveDesignBrief',
    });
  });

  it('resets the consecutive error count after a successful tool output', () => {
    const messages = [
      toolMessage('1', 'output-error'),
      toolMessage('2', 'output-available'),
    ];

    expect(cadAgentToolErrorState(messages)).toBeUndefined();
    expect(shouldContinueCadAgent({ messages })).toBe(true);
  });

  it('halts immediately when a CAD runtime bootstrap deadline expires', () => {
    const messages = [
      toolMessage(
        'bootstrap-timeout',
        'output-error',
        'CAD runtime bootstrap exceeded its 600000 ms deadline.',
      ),
    ];

    expect(shouldContinueCadAgent({ messages })).toBe(false);
    expect(cadAgentToolErrorState(messages)).toMatchObject({
      consecutiveErrors: 1,
      halted: true,
    });
  });

  it('does not carry a failed run into a new user turn', () => {
    const failedRun = [
      toolMessage('old-run-error', 'output-error'),
      { id: 'user-retry', role: 'user', parts: [] },
      toolMessage('new-run-error', 'output-error'),
    ] as UIMessage[];

    expect(cadAgentToolErrorState(failedRun)).toMatchObject({
      toolCallId: 'call-new-run-error',
      consecutiveErrors: 1,
      halted: false,
    });
  });

  it('clears a previous run error once a fresh user turn starts', () => {
    const messages = [
      toolMessage('old-run-error', 'output-error'),
      { id: 'user-retry', role: 'user', parts: [] },
    ] as UIMessage[];

    expect(cadAgentToolErrorState(messages)).toBeUndefined();
    expect(shouldContinueCadAgent({ messages })).toBe(false);
  });
});
