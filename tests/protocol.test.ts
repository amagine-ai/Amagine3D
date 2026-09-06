import { strict as assert } from 'node:assert';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test } from 'node:test';

import { isChatRequest, parseModelSpec } from '../server/protocol.ts';

test('accepts a valid chat request', () => {
  assert.equal(
    isChatRequest({
      message: '创建一个 CAD 零件',
      sessionId: '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      taskType: 'cad',
    }),
    true,
  );
  assert.equal(
    isChatRequest({
      message: '搜索产品尺寸后建模',
      sessionId: '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      taskType: 'cad',
      webSearchEnabled: true,
    }),
    true,
  );
  assert.equal(
    isChatRequest({
      message: '解释一下 BRep 和 mesh 的区别',
      sessionId: '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      taskType: 'chat',
    }),
    true,
  );
});

test('accepts image attachments, including an image-only request', () => {
  assert.equal(
    isChatRequest({
      images: [
        {
          data: Buffer.from('small test image').toString('base64'),
          mimeType: 'image/png',
          name: 'part.png',
        },
      ],
      message: '',
      sessionId: '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      taskType: 'cad',
    }),
    true,
  );
});

test('rejects malformed or empty chat requests', () => {
  assert.equal(isChatRequest({ message: '', sessionId: crypto.randomUUID() }), false);
  assert.equal(isChatRequest({ message: 'hello', sessionId: 'not-a-uuid' }), false);
  assert.equal(
    isChatRequest({ message: 'hello', sessionId: crypto.randomUUID() }),
    false,
  );
  assert.equal(
    isChatRequest({
      message: 'hello',
      sessionId: crypto.randomUUID(),
      taskType: 'analysis',
    }),
    false,
  );
  assert.equal(isChatRequest(null), false);
  assert.equal(
    isChatRequest({
      message: 'hello',
      sessionId: crypto.randomUUID(),
      taskType: 'chat',
      webSearchEnabled: 'true',
    }),
    false,
  );
  assert.equal(
    isChatRequest({
      images: [{ data: 'not base64!', mimeType: 'image/png', name: 'part.png' }],
      message: '查看图片',
      sessionId: crypto.randomUUID(),
      taskType: 'chat',
    }),
    false,
  );
  assert.equal(
    isChatRequest({
      images: [
        {
          data: Buffer.from('image').toString('base64'),
          mimeType: 'image/svg+xml',
          name: 'part.svg',
        },
      ],
      message: '查看图片',
      sessionId: crypto.randomUUID(),
      taskType: 'chat',
    }),
    false,
  );
});

test('parses provider/model while preserving slashes in model id', () => {
  assert.deepEqual(parseModelSpec('openai/org/gpt-5.5'), {
    id: 'org/gpt-5.5',
    provider: 'openai',
  });
  assert.throws(() => parseModelSpec('gpt-5.5'), /provider\/model/);
});

test('ships one compact CAD skill for the Codex harness', async () => {
  const path = resolve(import.meta.dirname, '..', 'skills', 'text-a3d', 'SKILL.md');
  const skill = await readFile(path, 'utf8');
  assert.match(skill, /name: text-a3d/u);
  assert.match(skill, /a3d compile/u);
  assert.ok(skill.split(/\r?\n/u).length < 120);
});
