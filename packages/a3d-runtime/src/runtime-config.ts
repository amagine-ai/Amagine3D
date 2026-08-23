import { ModelRuntime } from '@earendil-works/pi-coding-agent';

const API_TYPES = [
  'anthropic-messages',
  'openai-completions',
  'openai-responses',
  'azure-openai-responses',
  'openai-codex-responses',
  'mistral-conversations',
  'google-generative-ai',
  'google-vertex',
  'bedrock-converse-stream',
] as const;

const THINKING_LEVELS = [
  'off',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] as const;

type ApiType = (typeof API_TYPES)[number];
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];
type PiModel = NonNullable<ReturnType<ModelRuntime['getModel']>>;
type InputModality = PiModel['input'][number];

export interface ModelSpec {
  id: string;
  provider: string;
}

export function parseModelSpec(value: string): ModelSpec {
  const separator = value.indexOf('/');
  if (separator <= 0 || separator === value.length - 1) {
    throw new Error('LLM_MODEL must use provider/model format.');
  }
  return {
    provider: value.slice(0, separator),
    id: value.slice(separator + 1),
  };
}

export function optionalApiType(value: string | undefined): ApiType | undefined {
  if (!value?.trim()) return undefined;
  const normalized = value.trim();
  if ((API_TYPES as readonly string[]).includes(normalized)) {
    return normalized as ApiType;
  }
  throw new Error(`Unsupported LLM_API_TYPE: ${normalized}`);
}

export function thinkingLevel(value: string | undefined): ThinkingLevel {
  const normalized = value?.trim() || 'medium';
  if ((THINKING_LEVELS as readonly string[]).includes(normalized)) {
    return normalized as ThinkingLevel;
  }
  throw new Error(`Unsupported LLM_THINKING_LEVEL: ${normalized}`);
}

export function positiveInteger(
  name: string,
  value: string | undefined,
  fallback: number,
): number {
  if (!value?.trim()) return fallback;
  const parsed = Number(value);
  if (Number.isSafeInteger(parsed) && parsed > 0) return parsed;
  throw new Error(`${name} must be a positive integer. Received: ${value}`);
}

export function booleanValue(
  name: string,
  value: string | undefined,
  fallback: boolean,
): boolean {
  if (!value?.trim()) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;
  throw new Error(`${name} must be true or false. Received: ${value}`);
}

export function inputModalities(value: string | undefined): InputModality[] {
  const values = (value?.trim() || 'text,image')
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  if (
    values.length === 0 ||
    values.some((item) => item !== 'text' && item !== 'image')
  ) {
    throw new Error(
      `LLM_INPUT_MODALITIES accepts comma-separated text and image values. Received: ${value}`,
    );
  }
  return [...new Set(values)] as InputModality[];
}
