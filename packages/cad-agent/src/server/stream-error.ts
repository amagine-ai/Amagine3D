import {
  InvalidToolInputError,
  RetryError,
  type StreamTextTransform,
  type ToolSet,
} from 'ai';
import { z } from 'zod';

class CadAgentStreamAbortError extends Error {
  constructor(
    readonly phase: string,
    readonly abortReason: string | undefined,
  ) {
    super(`CAD Agent ${phase} stream aborted.`);
    this.name = 'CadAgentStreamAbortError';
  }
}

function findZodError(error: unknown): z.ZodError | undefined {
  const seen = new Set<unknown>();
  let current = error;
  for (let depth = 0; depth < 8; depth += 1) {
    if (current instanceof z.ZodError) return current;
    if (
      typeof current !== 'object' ||
      current === null ||
      seen.has(current) ||
      !('cause' in current)
    ) {
      return undefined;
    }
    seen.add(current);
    current = current.cause;
  }
  return undefined;
}

function issuePath(path: PropertyKey[]): string {
  return path.reduce<string>((result, segment) => {
    if (typeof segment === 'number') return `${result}[${String(segment)}]`;
    const label = String(segment);
    return result.length === 0 ? label : `${result}.${label}`;
  }, '');
}

function formatNestedError(error: unknown): string {
  if (error instanceof Error) {
    const name = error.name.trim();
    const label =
      name.length === 0 || name === 'Error' ? 'Error' : `Error [${name}]`;
    return `${label}: ${error.message || 'Unknown error'}`.slice(0, 2_000);
  }
  if (typeof error === 'string') return error.slice(0, 2_000);
  return 'Unknown error';
}

export function formatCadAgentStreamError(error: unknown): string {
  if (error instanceof CadAgentStreamAbortError) {
    const phase = error.phase.replaceAll('_', ' ');
    return /timeout/iu.test(error.abortReason ?? '')
      ? `CAD Agent ${phase} timed out before the next tool call completed. Partial model output is shown above; start a new run to retry.`
      : `CAD Agent ${phase} was aborted before the next tool call completed.`;
  }

  if (InvalidToolInputError.isInstance(error)) {
    const validation = findZodError(error);
    if (validation !== undefined) {
      const summary = validation.issues
        .slice(0, 3)
        .map((issue) => {
          const path = issuePath(issue.path);
          return `${path.length === 0 ? 'input' : path}: ${issue.message}`;
        })
        .join('; ')
        .slice(0, 600);
      return `${error.toolName} input rejected: ${summary}`;
    }
    return `${error.toolName} input rejected by its schema.`;
  }

  if (RetryError.isInstance(error)) {
    return [
      'CAD Agent request failed.',
      'details:',
      `  reason: ${error.reason}`,
      `  lastError: ${formatNestedError(error.lastError)}`,
    ].join('\n');
  }

  return 'CAD Agent request failed.';
}

export function createCadAgentAbortErrorTransform<TOOLS extends ToolSet>(
  phase: string,
): StreamTextTransform<TOOLS> {
  return () =>
    new TransformStream({
      transform(part, controller) {
        if (part.type === 'abort') {
          controller.enqueue({
            type: 'error',
            error: new CadAgentStreamAbortError(phase, part.reason),
          });
          return;
        }
        controller.enqueue(part);
      },
    });
}
