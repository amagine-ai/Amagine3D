import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  finishChatStages,
  restoredChatStages,
  startChatStage,
} from '../src/lib/chat-stages.ts';
import type { ChatStage } from '../src/types.ts';

function stage(id: string, label: string): ChatStage {
  return { id, label, occurredAt: 1, stage: id, status: 'running' };
}

test('chat stages retain completed work and identify the current stage', () => {
  const first = startChatStage([], stage('one', '读取需求'));
  const second = startChatStage(first, stage('two', '生成模型'));
  assert.deepEqual(
    second.map(({ label, status }) => ({ label, status })),
    [
      { label: '读取需求', status: 'completed' },
      { label: '生成模型', status: 'running' },
    ],
  );
  assert.deepEqual(
    finishChatStages(second, 'failed').map(({ status }) => status),
    ['completed', 'failed'],
  );
});

test('restored chat stages reject malformed persisted data', () => {
  assert.deepEqual(restoredChatStages({ stages: [stage('one', '完成')] }), [
    stage('one', '完成'),
  ]);
  assert.deepEqual(restoredChatStages({ stages: [{ label: 'missing fields' }] }), []);
});
