import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { sanitizeInternalPromptHistory } from '@amagine3d/a3d-runtime';

test('resumed PI context keeps user content but removes internal run prompts', () => {
  const messages = [
    {
      content: [
        { text: '请建立一个外壳。\n\n<visual_validation_required>内部</visual_validation_required>', type: 'text' },
        { data: 'image', mimeType: 'image/png', type: 'image' },
      ],
      role: 'user',
      timestamp: 1,
    },
    {
      content: [
        {
          text: '<first_build_reminder>内部提醒</first_build_reminder>',
          type: 'text',
        },
      ],
      role: 'user',
      timestamp: 2,
    },
    {
      content: [{ text: '正常回复', type: 'text' }],
      role: 'assistant',
      timestamp: 3,
    },
  ] as unknown as Parameters<typeof sanitizeInternalPromptHistory>[0];

  const sanitized = sanitizeInternalPromptHistory(messages);
  assert.equal(sanitized.length, 2);
  const userMessage = sanitized[0];
  assert.equal(userMessage?.role, 'user');
  if (userMessage?.role !== 'user') throw new Error('Expected user message.');
  assert.deepEqual(userMessage.content, [
    { text: '请建立一个外壳。', type: 'text' },
    { data: 'image', mimeType: 'image/png', type: 'image' },
  ]);
  assert.equal(sanitized[1]?.role, 'assistant');
});
