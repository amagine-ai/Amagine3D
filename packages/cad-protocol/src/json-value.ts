import { jsonValueSchema, type JsonValue } from './schemas';

export function toJsonValue(value: unknown): JsonValue {
  let serialized: string | undefined;
  try {
    serialized = JSON.stringify(value);
  } catch (cause) {
    throw new TypeError('Value cannot be serialized as JSON.', { cause });
  }

  if (serialized === undefined) {
    throw new TypeError('Value cannot be serialized as JSON.');
  }

  const parsed: unknown = JSON.parse(serialized);
  return jsonValueSchema.parse(parsed);
}
