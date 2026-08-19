import type { ViewerErrorCode, ViewerFailure } from './types';

export class ViewerDomainError extends Error {
  readonly code: ViewerErrorCode;
  readonly recoverable: boolean;

  constructor(code: ViewerErrorCode, message: string, recoverable: boolean) {
    super(message);
    this.name = 'ViewerDomainError';
    this.code = code;
    this.recoverable = recoverable;
  }

  toFailure(): ViewerFailure {
    return {
      code: this.code,
      message: this.message,
      recoverable: this.recoverable,
    };
  }
}

export function toViewerFailure(error: unknown): ViewerFailure {
  if (error instanceof ViewerDomainError) return error.toFailure();
  return {
    code: 'LoadFailed',
    message:
      error instanceof Error
        ? `The model could not be loaded. ${error.message}`
        : 'The model could not be loaded. Try loading it again.',
    recoverable: true,
  };
}
