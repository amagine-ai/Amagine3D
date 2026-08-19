import { designBriefSchema } from '@amagine3d/cad-protocol';
import {
  InvalidToolInputError,
  RetryError,
  type TextStreamPart,
  type ToolSet,
} from 'ai';
import { describe, expect, it } from 'vitest';

import {
  createCadAgentAbortErrorTransform,
  formatCadAgentStreamError,
} from './stream-error';

describe('formatCadAgentStreamError', () => {
  it('returns bounded field-level validation details without echoing tool input', () => {
    const parsed = designBriefSchema.safeParse({
      schemaVersion: 1,
      runId: 'run-1',
      workflowKind: 'single-color',
      userConstraints: [],
      agentAssumptions: [],
      researchHints: [],
      features: ['enclosure'],
      verificationTargets: [
        {
          name: 'body count',
          value: 2,
          unit: 'this explanation is intentionally much longer than forty characters',
          source: 'agent',
        },
      ],
      derivationNotes: [],
    });
    if (parsed.success) throw new Error('Expected invalid design brief.');
    const error = new InvalidToolInputError({
      toolName: 'saveDesignBrief',
      toolInput: 'SECRET_RAW_TOOL_INPUT',
      cause: new Error('validation wrapper', { cause: parsed.error }),
    });

    const message = formatCadAgentStreamError(error);

    expect(message).toContain('saveDesignBrief input rejected');
    expect(message).toContain('verificationTargets[0].unit');
    expect(message).not.toContain('SECRET_RAW_TOOL_INPUT');
  });

  it('redacts unexpected server errors', () => {
    expect(
      formatCadAgentStreamError(new Error('internal host and credential data')),
    ).toBe('CAD Agent request failed.');
  });

  it('includes the last provider error for an exhausted AI SDK retry', () => {
    const lastError = new Error(
      'Cannot connect to API: Client network socket disconnected before secure TLS connection was established',
    );
    lastError.name = 'AI_APICallError';
    const error = new RetryError({
      message: 'Failed after 3 attempts.',
      reason: 'maxRetriesExceeded',
      errors: [lastError],
    });

    expect(formatCadAgentStreamError(error)).toBe(
      [
        'CAD Agent request failed.',
        'details:',
        '  reason: maxRetriesExceeded',
        '  lastError: Error [AI_APICallError]: Cannot connect to API: Client network socket disconnected before secure TLS connection was established',
      ].join('\n'),
    );
  });

  it('turns a timeout abort into a safe UI stream error', async () => {
    const transform = createCadAgentAbortErrorTransform<ToolSet>('coding')({
      tools: {},
      stopStream: () => undefined,
    });
    const stream = new ReadableStream<TextStreamPart<ToolSet>>({
      start(controller) {
        controller.enqueue({
          type: 'abort',
          reason: 'TimeoutError: internal provider details',
        });
        controller.close();
      },
    }).pipeThrough(transform);

    const reader = stream.getReader();
    const chunks: TextStreamPart<ToolSet>[] = [];
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      chunks.push(result.value);
    }

    expect(chunks).toHaveLength(1);
    const chunk = chunks[0];
    expect(chunk?.type).toBe('error');
    if (chunk?.type !== 'error') throw new Error('Expected error chunk.');
    expect(formatCadAgentStreamError(chunk.error)).toBe(
      'CAD Agent coding timed out before the next tool call completed. Partial model output is shown above; start a new run to retry.',
    );
  });
});
