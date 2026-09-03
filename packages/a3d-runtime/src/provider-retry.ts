import type { InlineExtension } from '@earendil-works/pi-coding-agent';

const INVALID_ENCRYPTED_CONTENT_CODE = 'invalid_encrypted_content';
const SINGLE_RETRY_HINT =
  'Amagine3D detected a retryable provider error. Please retry your request once.';

export function isInvalidEncryptedContentError(
  errorMessage: unknown,
): errorMessage is string {
  return (
    typeof errorMessage === 'string' &&
    errorMessage.includes(INVALID_ENCRYPTED_CONTENT_CODE)
  );
}

/**
 * Promote one consecutive OpenAI Responses encrypted-context failure into PI's
 * existing abortable retry loop. The failed model request emitted no tool call,
 * so PI resumes from the last completed tool result without executing it again.
 */
export function createInvalidEncryptedContentRetryExtension(): InlineExtension {
  let retryPending = false;

  return {
    factory(pi) {
      pi.on('message_end', (event) => {
        const { message } = event;
        if (message.role !== 'assistant') return undefined;

        const isTargetError =
          message.api === 'openai-responses' &&
          message.provider === 'openai' &&
          message.stopReason === 'error' &&
          isInvalidEncryptedContentError(message.errorMessage);

        if (!isTargetError) {
          if (message.stopReason !== 'error') retryPending = false;
          return undefined;
        }

        // A second consecutive failure is terminal. A later successful model
        // response resets the guard, allowing one retry for a new occurrence.
        if (retryPending) {
          retryPending = false;
          return undefined;
        }
        retryPending = true;

        // The pinned PI retry classifier recognizes explicit provider retry
        // guidance. Keep the original provider error intact for diagnostics.
        return {
          message: {
            ...message,
            errorMessage: `${message.errorMessage}\n${SINGLE_RETRY_HINT}`,
          },
        };
      });
    },
    hidden: true,
    name: 'invalid-encrypted-content-retry',
  };
}
