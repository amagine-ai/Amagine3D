import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  auditCadVisualValidation,
  requiresCadVisualValidation,
  visualValidationRepairInstruction,
} from '../server/visual-audit.ts';

function assistantTool(
  name: string,
  args: Record<string, unknown>,
  id = `call-${name}`,
) {
  return {
    content: [{ arguments: args, id, name, type: 'toolCall' }],
    role: 'assistant',
  };
}

function imageReadResult(id = 'call-read') {
  return {
    content: [
      { text: 'Read image file [image/png]', type: 'text' },
      { data: 'base64', mimeType: 'image/png', type: 'image' },
    ],
    isError: false,
    role: 'toolResult',
    toolCallId: id,
    toolName: 'read',
  };
}

function bashResult(id = 'call-bash') {
  return {
    content: [{ text: 'rendered', type: 'text' }],
    isError: false,
    role: 'toolResult',
    toolCallId: id,
    toolName: 'bash',
  };
}

test('requires visual validation for referenced and appearance-sensitive CAD', () => {
  assert.equal(requiresCadVisualValidation('根据图片生成一个模型文件', 1), true);
  assert.equal(requiresCadVisualValidation('建模并复刻这个外观', 0), true);
  assert.equal(requiresCadVisualValidation('做一个多色 3MF', 0), true);
  assert.equal(requiresCadVisualValidation('做一个多色模型', 0), true);
  assert.equal(requiresCadVisualValidation('分析这张普通照片', 1), false);
  assert.equal(
    requiresCadVisualValidation('创建一个 CAD 支架，不用视觉检查', 1),
    false,
  );
  assert.equal(
    requiresCadVisualValidation('创建一个 CAD 支架，不用做本地验收', 1),
    false,
  );
  assert.equal(
    auditCadVisualValidation([
      assistantTool('bash', {
        command: 'python skills/text-a3d/render_preview.py part.stl',
      }),
      bashResult(),
      assistantTool('read', {
        path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part_views.png',
      }),
      imageReadResult(),
      assistantTool('write', {
        content: 'new source',
        path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part.py',
      }),
    ]).pass,
    false,
  );
  const referenceAudit = auditCadVisualValidation(
    [
      assistantTool(
        'bash',
        { command: 'python skills/text-a3d/reference_analyze.py ref.png' },
        'call-analyze',
      ),
      bashResult('call-analyze'),
      assistantTool(
        'bash',
        {
          command:
            'python skills/text-a3d/render_preview.py part.stl --out part_views.png',
        },
        'call-render',
      ),
      bashResult('call-render'),
      assistantTool('read', {
        path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part_views.png',
      }),
      imageReadResult(),
    ],
    { requireReferenceAnalysis: true },
  );
  assert.deepEqual(referenceAudit, {
    pass: true,
    previewRead: true,
    referenceAnalyzed: true,
    renderCalled: true,
  });
});

test('visual audit requires preview render followed by preview read', () => {
  assert.deepEqual(auditCadVisualValidation([]), {
    pass: false,
    previewRead: false,
    referenceAnalyzed: false,
    renderCalled: false,
  });
  assert.deepEqual(
    auditCadVisualValidation([
      assistantTool('read', { path: '/tmp/reference.png' }),
      imageReadResult(),
      assistantTool('bash', {
        command: 'python skills/text-a3d/render_preview.py part.stl',
      }),
      bashResult(),
    ]),
    {
      pass: false,
      previewRead: false,
      referenceAnalyzed: false,
      renderCalled: true,
    },
  );
  assert.deepEqual(
    auditCadVisualValidation([
      assistantTool('bash', {
        command:
          'python skills/text-a3d/render_preview.py part.stl --out part_views.png',
      }),
      bashResult(),
      assistantTool('read', {
        path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part_views.png',
      }),
      imageReadResult(),
    ]),
    {
      pass: true,
      previewRead: true,
      referenceAnalyzed: false,
      renderCalled: true,
    },
  );
  assert.equal(
    auditCadVisualValidation([
      assistantTool('bash', {
        command: 'python skills/text-a3d/render_preview.py part.stl',
      }),
      bashResult(),
      assistantTool('read', {
        path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part_views.png',
      }),
      {
        content: [{ text: 'file not found', type: 'text' }],
        isError: true,
        role: 'toolResult',
        toolCallId: 'call-read',
        toolName: 'read',
      },
    ]).pass,
    false,
  );
  assert.equal(
    auditCadVisualValidation(
      [
        assistantTool('bash', {
          command:
            'python skills/text-a3d/render_preview.py part.stl --out part_views.png',
        }),
        bashResult(),
        assistantTool('read', {
          path: '/workspace/sessions/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93/part_views.png',
        }),
        imageReadResult(),
      ],
      { requireReferenceAnalysis: true },
    ).pass,
    false,
  );
});

test('visual repair instruction names missing evidence and attempt budget', () => {
  const instruction = visualValidationRepairInstruction(
    {
      pass: false,
      previewRead: false,
      referenceAnalyzed: false,
      renderCalled: false,
    },
    {
      attempt: 2,
      maxAttempts: 3,
      requireReferenceAnalysis: true,
    },
  );

  assert.match(instruction, /attempt 2\/3/);
  assert.match(instruction, /reference_analyze\.py/);
  assert.match(instruction, /render_preview\.py/);
  assert.match(instruction, /read tool/);

  const readOnlyRepair = visualValidationRepairInstruction(
    {
      pass: false,
      previewRead: false,
      referenceAnalyzed: true,
      renderCalled: true,
    },
    {
      attempt: 1,
      maxAttempts: 3,
      requireReferenceAnalysis: true,
    },
  );
  assert.doesNotMatch(readOnlyRepair, /run the selected skill's reference_analyze/);
  assert.doesNotMatch(readOnlyRepair, /render a fresh preview/);
  assert.match(readOnlyRepair, /read tool/);
});
