import {
  SCHEMA_VERSION,
  serializedCadErrorSchema,
  type CadErrorCategory,
  type CadErrorCode,
  type JsonValue,
  type SerializedCadError,
} from './schemas';

export type CadDomainErrorOptions = {
  category: CadErrorCategory;
  retryable: boolean;
  operation?: string;
  details?: Record<string, JsonValue>;
  cause?: unknown;
};

export class CadDomainError extends Error {
  readonly code: CadErrorCode;
  readonly category: CadErrorCategory;
  readonly retryable: boolean;
  readonly operation: string | undefined;
  readonly details: Record<string, JsonValue> | undefined;

  constructor(
    code: CadErrorCode,
    message: string,
    options: CadDomainErrorOptions,
  ) {
    super(message, { cause: options.cause });
    this.name = code;
    this.code = code;
    this.category = options.category;
    this.retryable = options.retryable;
    this.operation = options.operation;
    this.details = options.details;
  }

  serialize(): SerializedCadError {
    return serializedCadErrorSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      code: this.code,
      category: this.category,
      message: this.message,
      operation: this.operation,
      retryable: this.retryable,
      details: this.details,
    });
  }
}

export function serializeCadError(error: unknown): SerializedCadError {
  if (error instanceof CadDomainError) {
    return error.serialize();
  }

  return {
    schemaVersion: SCHEMA_VERSION,
    code: 'UnexpectedFailure',
    category: 'unknown',
    message: error instanceof Error ? error.message : 'Unexpected failure',
    retryable: false,
  };
}

export function deserializeCadError(error: unknown): CadDomainError {
  const parsed = serializedCadErrorSchema.parse(error);
  return new CadDomainError(parsed.code, parsed.message, {
    category: parsed.category,
    retryable: parsed.retryable,
    ...(parsed.operation === undefined ? {} : { operation: parsed.operation }),
    ...(parsed.details === undefined ? {} : { details: parsed.details }),
  });
}
