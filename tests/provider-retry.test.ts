import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  createInvalidEncryptedContentRetryExtension,
  isInvalidEncryptedContentError,
} from '@amagine3d/a3d-runtime';
import type {
  ExtensionAPI,
  MessageEndEvent,
} from '@earendil-works/pi-coding-agent';

type MessageEndHandler = (
  event: MessageEndEvent,
) => { message?: MessageEndEvent['message'] } | undefined | void;

function assistantMessage(
  stopReason: 'error' | 'stop',
  errorMessage?: string,
): MessageEndEvent['message'] {
  return {
    api: 'openai-responses',
    content: [],
    errorMessage,
    model: 'gpt-5.6-sol',
    provider: 'openai',
    role: 'assistant',
    stopReason,
    timestamp: 1,
    usage: {
      cacheRead: 0,
      cacheWrite: 0,
      cost: {
        cacheRead: 0,
        cacheWrite: 0,
        input: 0,
        output: 0,
        total: 0,
      },
      input: 0,
      output: 0,
      totalTokens: 0,
    },
  };
}

function extensionHandler(): MessageEndHandler {
  const extension = createInvalidEncryptedContentRetryExtension();
  assert.equal(typeof extension, 'object');
  if (typeof extension === 'function') throw new Error('Expected named extension.');

  let handler: MessageEndHandler | undefined;
  extension.factory({
    on(event, candidate) {
      if (event === 'message_end') {
        handler = candidate as MessageEndHandler;
      }
    },
  } as ExtensionAPI);
  assert.ok(handler);
  return handler;
}

test('recognizes the encrypted reasoning validation error code', () => {
  assert.equal(
    isInvalidEncryptedContentError(
      'OpenAI API error (400): {"code":"invalid_encrypted_content"}',
    ),
    true,
  );
  assert.equal(isInvalidEncryptedContentError('rate_limit_exceeded'), false);
});

test('promotes only the first consecutive encrypted-content failure', () => {
  const handler = extensionHandler();
  const error = 'OpenAI API error (400): {"code":"invalid_encrypted_content"}';
  const event = {
    message: assistantMessage('error', error),
    type: 'message_end',
  } as const;

  const first = handler(event);
  assert.match(
    first?.message?.role === 'assistant'
      ? first.message.errorMessage ?? ''
      : '',
    /Please retry your request once/u,
  );
  assert.equal(handler(event), undefined);
});

test('a successful response resets the one-retry guard', () => {
  const handler = extensionHandler();
  const error = 'OpenAI API error (400): invalid_encrypted_content';
  const failure = {
    message: assistantMessage('error', error),
    type: 'message_end',
  } as const;

  assert.ok(handler(failure)?.message);
  handler({ message: assistantMessage('stop'), type: 'message_end' });
  assert.ok(handler(failure)?.message);
});

test('does not promote unrelated provider errors', () => {
  const handler = extensionHandler();
  assert.equal(
    handler({
      message: assistantMessage(
        'error',
        'OpenAI API error (400): invalid_request_error',
      ),
      type: 'message_end',
    }),
    undefined,
  );
});
