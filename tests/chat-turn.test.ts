import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  appendChatStepText,
  completeChatTurn,
  emptyChatTurn,
  restoreChatTurn,
  startChatStep,
} from '../src/lib/chat-turn.ts';
import type { ChatStep } from '../src/types.ts';

function step(id: string, label: string): ChatStep {
  return { id, label, occurredAt: 1, stage: id, status: 'running' };
}

test('chat turns retain ordered steps and append text to the owning step', () => {
  const first = startChatStep(emptyChatTurn(), step('one', '读取需求'));
  const second = startChatStep(first, step('two', '生成模型'));
  const withProgress = appendChatStepText(second, 'two', '主体已经生成。');
  assert.deepEqual(
    withProgress.steps.map(({ label, progressText, status }) => ({
      label,
      progressText,
      status,
    })),
    [
      {
        label: '读取需求',
        progressText: undefined,
        status: 'completed',
      },
      {
        label: '生成模型',
        progressText: '主体已经生成。',
        status: 'running',
      },
    ],
  );

  const completed = completeChatTurn(withProgress, {
    finishedAt: 10,
    replyText: '模型已经生成。',
    sourceStepId: 'two',
    status: 'completed',
  });
  assert.deepEqual(
    completed.steps.map(({ progressText, status }) => ({
      progressText,
      status,
    })),
    [
      { progressText: undefined, status: 'completed' },
      { progressText: undefined, status: 'completed' },
    ],
  );
  assert.equal(completed.replyText, '模型已经生成。');
  assert.equal(completed.finishedAt, 10);
});

test('restored chat turns reject malformed or unfinished persisted data', () => {
  const turn = {
    finishedAt: 10,
    replyText: '完成',
    steps: [{ ...step('one', '完成'), status: 'completed' as const }],
  };
  assert.deepEqual(restoreChatTurn(turn), turn);
  assert.equal(restoreChatTurn({ ...turn, finishedAt: undefined }), undefined);
  assert.equal(
    restoreChatTurn({ ...turn, steps: [step('one', '仍在运行')] }),
    undefined,
  );
  assert.equal(
    restoreChatTurn({ ...turn, steps: [{ label: 'missing fields' }] }),
    undefined,
  );
});
