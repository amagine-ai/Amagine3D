import { createHash } from 'node:crypto';
import { strict as assert } from 'node:assert';
import { mkdtemp, realpath, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';

import {
  auditCadVisualValidation,
  CadVisualAuditTrail,
  type VisualAuditOptions,
  visualValidationInstruction,
  visualValidationRepairInstruction,
} from '../server/visual-audit.ts';
import { writeUnifiedBuildFixture } from './unified-build-fixture.ts';

const SKILLS_ROOT = resolve(import.meta.dirname, '..', 'skills');
const RENDER_SCRIPT = join(SKILLS_ROOT, 'text-a3d', 'render_preview.py');
const REFERENCE_SCRIPT = join(SKILLS_ROOT, 'text-a3d', 'reference_analyze.py');
const SHAPE_SCRIPT = join(SKILLS_ROOT, 'text-a3d', 'shape_consistency.py');

function digest(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex');
}

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
    content: [{ text: 'ok', type: 'text' }],
    isError: false,
    role: 'toolResult',
    toolCallId: id,
    toolName: 'bash',
  };
}

function cadCompileResult(
  renderReport: string,
  pass = true,
  id = 'call-cad-compile',
) {
  return {
    content: [{ text: pass ? 'CAD compile passed' : 'CAD compile failed', type: 'text' }],
    details: {
      artifacts: pass
        ? { renderEvidence: { path: renderReport } }
        : {},
      pass,
      schema: 'evidence-cad-compile-result/v1',
      status: pass ? 'awaiting-visual-review' : 'failed',
    },
    isError: false,
    role: 'toolResult',
    toolCallId: id,
    toolName: 'cad_compile',
  };
}

function captureMessages(messages: readonly unknown[], workspaceRoot: string) {
  const trail = new CadVisualAuditTrail(workspaceRoot);
  for (const message of messages) trail.record(message);
  return trail.entries;
}

async function auditMessages(
  messages: readonly unknown[],
  options: VisualAuditOptions,
) {
  return await auditCadVisualValidation(
    captureMessages(messages, options.workspaceRoot),
    options,
  );
}

interface EvidenceFixture {
  display: string;
  messages: unknown[];
  options: VisualAuditOptions;
  preview: string;
  renderReport: string;
  root: string;
}

async function evidenceFixture(): Promise<EvidenceFixture> {
  const root = await realpath(
    await mkdtemp(join(tmpdir(), 'amagine-visual-evidence-')),
  );
  const turnStartedAtMs = Date.now() - 1_000;
  const build = await writeUnifiedBuildFixture({
    backend: 'brep-part',
    name: 'part',
    root,
  });
  const display = build.displayPath;
  const preview = join(root, 'part_views.png');
  const renderReport = join(root, 'part_render.json');
  const displayPayload = Buffer.from('glTF-display');
  const previewPayload = Buffer.from('fresh-png-evidence');
  await writeFile(preview, previewPayload);
  await writeFile(
    renderReport,
    JSON.stringify({
      meshes: [{ path: display, sha256: digest(displayPayload) }],
      preview: { path: preview, sha256: digest(previewPayload) },
      schema: 'evidence-render/v2',
    }),
  );
  const messages = [
    assistantTool(
      'cad_compile',
      {
        intent: 'intent.json',
        marker: '.turn-marker',
        output_dir: '.',
        scene: 'scene.json',
        source: 'model.py',
      },
      'call-cad-compile',
    ),
    cadCompileResult(renderReport),
    assistantTool('read', { path: preview }, 'call-read'),
    imageReadResult('call-read'),
  ];
  return {
    display,
    messages,
    options: { skillsRoot: SKILLS_ROOT, turnStartedAtMs, workspaceRoot: root },
    preview,
    renderReport,
    root,
  };
}

const failedAudit = {
  buildBound: false,
  pass: false,
  previewRead: false,
  referenceAnalyzed: false,
  renderCalled: false,
  renderReportValid: false,
};

test('visual validation instructions require a hash-bound render report', () => {
  assert.equal(visualValidationInstruction(false), '');
  const instruction = visualValidationInstruction(true);
  assert.match(instruction, /mandatory visual gate/u);
  assert.match(instruction, /call cad_compile/u);
  assert.match(instruction, /status=awaiting-visual-review/u);
  assert.match(instruction, /Use the read tool/u);
  assert.doesNotMatch(instruction, /If cad_compile is unavailable/u);
  assert.doesNotMatch(instruction, /reference_analyze\.py/u);
  assert.match(
    visualValidationInstruction(true, true),
    /reference_analyze\.py/u,
  );
  const referencedInstruction = visualValidationInstruction(true, true);
  assert.ok(
    referencedInstruction.indexOf('reference_analyze.py') <
      referencedInstruction.indexOf('call cad_compile'),
  );
});

test('accepts successful cad_compile render evidence only after reading its exact preview', async () => {
  const fixture = await evidenceFixture();
  try {
    const compileMessages = [
      assistantTool(
        'cad_compile',
        {
          intent: 'intent.json',
          marker: '.turn-marker',
          output_dir: '.',
          scene: 'scene.json',
          source: 'model.py',
        },
        'call-cad-compile',
      ),
      cadCompileResult(fixture.renderReport),
    ];
    const beforeRead = await auditMessages(
      compileMessages,
      fixture.options,
    );
    assert.equal(beforeRead.renderCalled, true);
    assert.equal(beforeRead.renderReportValid, true);
    assert.equal(beforeRead.buildBound, true);
    assert.equal(beforeRead.previewRead, false);
    assert.equal(beforeRead.pass, false);

    assert.deepEqual(
      await auditMessages(
        [
          ...compileMessages,
          assistantTool('read', { path: fixture.preview }, 'call-read'),
          imageReadResult('call-read'),
        ],
        fixture.options,
      ),
      {
        buildBound: true,
        pass: true,
        previewRead: true,
        referenceAnalyzed: false,
        renderCalled: true,
        renderReportValid: true,
      },
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('does not accept failed cad_compile output as render evidence', async () => {
  const fixture = await evidenceFixture();
  try {
    assert.deepEqual(
      await auditMessages(
        [
          assistantTool(
            'cad_compile',
            {
              intent: 'intent.json',
              marker: '.turn-marker',
              output_dir: '.',
              scene: 'scene.json',
              source: 'model.py',
            },
            'call-cad-compile',
          ),
          cadCompileResult(fixture.renderReport, false),
          assistantTool('read', { path: fixture.preview }, 'call-read'),
          imageReadResult('call-read'),
        ],
        fixture.options,
      ),
      failedAudit,
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('accepts only a fresh render report, exact preview read, and latest build display binding', async () => {
  const fixture = await evidenceFixture();
  try {
    assert.deepEqual(
      await auditMessages(fixture.messages, fixture.options),
      {
        buildBound: true,
        pass: true,
        previewRead: true,
        referenceAnalyzed: false,
        renderCalled: true,
        renderReportValid: true,
      },
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('direct render commands cannot replace a successful cad_compile result', async () => {
  const fixture = await evidenceFixture();
  try {
    assert.deepEqual(
      await auditMessages(
        [
          assistantTool(
            'bash',
            {
              command: `true # python render_preview.py "${fixture.display}" --report "${fixture.renderReport}"`,
            },
            'call-render',
          ),
          bashResult('call-render'),
          assistantTool('read', { path: fixture.preview }, 'call-read'),
          imageReadResult('call-read'),
        ],
        fixture.options,
      ),
      failedAudit,
    );
    assert.deepEqual(
      await auditMessages(
        [
          assistantTool(
            'bash',
            {
              command: `python "${RENDER_SCRIPT}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}"`,
            },
            'call-render',
          ),
          bashResult('call-render'),
          assistantTool('read', { path: fixture.preview }, 'call-read'),
          imageReadResult('call-read'),
        ],
        fixture.options,
      ),
      failedAudit,
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('rejects fake scripts, interpreter wrappers, flags, and shell composition', async () => {
  const fixture = await evidenceFixture();
  try {
    const fakeScript = join(fixture.root, 'render_preview.py');
    const fakeInterpreter = join(fixture.root, 'python');
    await writeFile(fakeScript, 'raise SystemExit(0)\n');
    await writeFile(fakeInterpreter, '#!/bin/sh\nexit 0\n');
    const commands = [
      `python "${fakeScript}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}"`,
      `"${fakeInterpreter}" "${RENDER_SCRIPT}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}"`,
      `python -u "${RENDER_SCRIPT}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}"`,
      `python "${RENDER_SCRIPT}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}"\ntrue`,
      `python "${RENDER_SCRIPT}" "${fixture.display}" --out "${fixture.preview}" --report "${fixture.renderReport}" \`true\``,
    ];
    for (const command of commands) {
      const messages = [
        assistantTool('bash', { command }, 'call-render'),
        bashResult('call-render'),
        assistantTool('read', { path: fixture.preview }, 'call-read'),
        imageReadResult('call-read'),
      ];
      const audit = await auditMessages(messages, fixture.options);
      assert.equal(audit.renderCalled, false, command);
      assert.equal(audit.pass, false, command);
    }
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('old reports and previews cannot pass in a new turn', async () => {
  const fixture = await evidenceFixture();
  try {
    const audit = await auditMessages(fixture.messages, {
      ...fixture.options,
      turnStartedAtMs: Date.now() + 10_000,
    });
    assert.equal(audit.renderCalled, true);
    assert.equal(audit.renderReportValid, false);
    assert.equal(audit.previewRead, false);
    assert.equal(audit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('a fresh render cannot reuse a build report from an earlier turn', async () => {
  const fixture = await evidenceFixture();
  try {
    const old = new Date(Date.now() - 10_000);
    await utimes(join(fixture.root, 'part_report.json'), old, old);
    const turnStartedAtMs = Date.now() - 100;
    const previewPayload = Buffer.from('new-render-of-an-old-build');
    await writeFile(fixture.preview, previewPayload);
    await writeFile(
      fixture.renderReport,
      JSON.stringify({
        meshes: [
          {
            path: fixture.display,
            sha256: digest(Buffer.from('glTF-display')),
          },
        ],
        preview: {
          path: fixture.preview,
          sha256: digest(previewPayload),
        },
        schema: 'evidence-render/v2',
      }),
    );
    const audit = await auditMessages(fixture.messages, {
      ...fixture.options,
      turnStartedAtMs,
    });
    assert.equal(audit.renderReportValid, true);
    assert.equal(audit.previewRead, true);
    assert.equal(audit.buildBound, false);
    assert.equal(audit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('a fresh report cannot relabel an old build artifact as current', async () => {
  const fixture = await evidenceFixture();
  try {
    const old = new Date(Date.now() - 10_000);
    await utimes(fixture.display, old, old);
    const turnStartedAtMs = Date.now() - 100;
    const current = new Date();
    await utimes(join(fixture.root, 'part_report.json'), current, current);
    await utimes(fixture.preview, current, current);
    await utimes(fixture.renderReport, current, current);
    const audit = await auditMessages(fixture.messages, {
      ...fixture.options,
      turnStartedAtMs,
    });
    assert.equal(audit.renderReportValid, true);
    assert.equal(audit.previewRead, true);
    assert.equal(audit.buildBound, false);
    assert.equal(audit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('preview path and hash must match both evidence report and read call', async () => {
  const fixture = await evidenceFixture();
  try {
    const otherPreview = join(fixture.root, 'other.png');
    await writeFile(otherPreview, 'another image');
    const wrongRead = structuredClone(fixture.messages) as Array<{
      content?: Array<{ arguments?: { path?: string } }>;
    }>;
    wrongRead[2]!.content![0]!.arguments!.path = otherPreview;
    const readAudit = await auditMessages(wrongRead, fixture.options);
    assert.equal(readAudit.renderReportValid, true);
    assert.equal(readAudit.previewRead, false);
    assert.equal(readAudit.pass, false);

    await writeFile(fixture.preview, 'tampered after render');
    const hashAudit = await auditMessages(
      fixture.messages,
      fixture.options,
    );
    assert.equal(hashAudit.renderReportValid, false);
    assert.equal(hashAudit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('read-time snapshot rejects a preview and report replaced after the read', async () => {
  const fixture = await evidenceFixture();
  try {
    const trail = new CadVisualAuditTrail(fixture.root);
    for (const message of fixture.messages) trail.record(message);

    const replacement = Buffer.from('replacement-preview-after-read');
    await writeFile(fixture.preview, replacement);
    await writeFile(
      fixture.renderReport,
      JSON.stringify({
        meshes: [
          {
            path: fixture.display,
            sha256: digest(Buffer.from('glTF-display')),
          },
        ],
        preview: {
          path: fixture.preview,
          sha256: digest(replacement),
        },
        schema: 'evidence-render/v2',
      }),
    );

    const audit = await auditCadVisualValidation(
      trail.entries,
      fixture.options,
    );
    assert.equal(audit.renderReportValid, true);
    assert.equal(audit.previewRead, false);
    assert.equal(audit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('render meshes must bind the newest passing build display path and hash', async () => {
  const fixture = await evidenceFixture();
  try {
    await writeUnifiedBuildFixture({
      backend: 'brep-part',
      name: 'newer',
      root: fixture.root,
    });
    const audit = await auditMessages(
      fixture.messages,
      fixture.options,
    );
    assert.equal(audit.renderReportValid, true);
    assert.equal(audit.previewRead, true);
    assert.equal(audit.buildBound, false);
    assert.equal(audit.pass, false);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('reference CAD also requires a strict successful reference_analyze invocation', async () => {
  const fixture = await evidenceFixture();
  try {
    const reference = join(fixture.root, 'reference.png');
    const referencePayload = Buffer.from('uploaded-reference-image');
    const referenceReport = join(fixture.root, 'reference_analysis.json');
    await writeFile(reference, referencePayload);
    const withoutReference = await auditMessages(
      fixture.messages,
      {
        ...fixture.options,
        referenceImages: [
          { path: reference, sha256: digest(referencePayload) },
        ],
        requireReferenceAnalysis: true,
      },
    );
    assert.equal(withoutReference.pass, false);
    assert.equal(withoutReference.referenceAnalyzed, false);

    await writeFile(
      referenceReport,
      JSON.stringify({
        image: { sha256: digest(referencePayload) },
        schema: 'evidence-reference-analysis/v1',
        source: { path: reference, sha256: digest(referencePayload) },
      }),
    );
    const withReference = [
      assistantTool(
        'bash',
        {
          command: `python "${REFERENCE_SCRIPT}" "${reference}" --out "${referenceReport}"`,
        },
        'call-reference',
      ),
      bashResult('call-reference'),
      ...fixture.messages,
    ];
    const referenceOptions = {
      ...fixture.options,
      referenceImages: [
        { path: reference, sha256: digest(referencePayload) },
      ],
      requireReferenceAnalysis: true,
    };
    const lateAnalysis = await auditMessages(withReference, referenceOptions);
    assert.equal(lateAnalysis.referenceAnalyzed, false);
    assert.equal(lateAnalysis.pass, false);

    const orderedAt = Date.now();
    await utimes(
      referenceReport,
      new Date(orderedAt - 400),
      new Date(orderedAt - 400),
    );
    await utimes(
      join(fixture.root, 'part_report.json'),
      new Date(orderedAt - 300),
      new Date(orderedAt - 300),
    );
    await utimes(
      fixture.preview,
      new Date(orderedAt - 200),
      new Date(orderedAt - 200),
    );
    await utimes(
      fixture.renderReport,
      new Date(orderedAt - 100),
      new Date(orderedAt - 100),
    );
    const audit = await auditMessages(withReference, referenceOptions);
    assert.equal(audit.referenceAnalyzed, true);
    assert.equal(audit.pass, true);

    const missingReportFlag = structuredClone(withReference) as Array<{
      content?: Array<{ arguments?: { command?: string } }>;
    }>;
    missingReportFlag[0]!.content![0]!.arguments!.command =
      `python "${REFERENCE_SCRIPT}" "${reference}"`;
    assert.equal(
      (
        await auditMessages(missingReportFlag, {
          ...fixture.options,
          referenceImages: [
            { path: reference, sha256: digest(referencePayload) },
          ],
          requireReferenceAnalysis: true,
        })
      ).referenceAnalyzed,
      false,
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('a malformed earlier reference report does not poison a corrected repair', async () => {
  const fixture = await evidenceFixture();
  try {
    const reference = join(fixture.root, 'reference.png');
    const referencePayload = Buffer.from('uploaded-reference-image');
    const badReport = join(fixture.root, 'bad-reference.json');
    const goodReport = join(fixture.root, 'good-reference.json');
    await writeFile(reference, referencePayload);
    await writeFile(badReport, '{}');
    await writeFile(
      goodReport,
      JSON.stringify({
        image: { sha256: digest(referencePayload) },
        schema: 'evidence-reference-analysis/v1',
        source: { path: reference, sha256: digest(referencePayload) },
      }),
    );
    const orderedAt = Date.now();
    await utimes(
      goodReport,
      new Date(orderedAt - 400),
      new Date(orderedAt - 400),
    );
    await utimes(
      join(fixture.root, 'part_report.json'),
      new Date(orderedAt - 300),
      new Date(orderedAt - 300),
    );
    await utimes(
      fixture.preview,
      new Date(orderedAt - 200),
      new Date(orderedAt - 200),
    );
    await utimes(
      fixture.renderReport,
      new Date(orderedAt - 100),
      new Date(orderedAt - 100),
    );
    const messages = [
      assistantTool(
        'bash',
        {
          command: `python "${REFERENCE_SCRIPT}" "${reference}" --out "${badReport}"`,
        },
        'call-reference-bad',
      ),
      bashResult('call-reference-bad'),
      assistantTool(
        'bash',
        {
          command: `python "${REFERENCE_SCRIPT}" "${reference}" --out "${goodReport}"`,
        },
        'call-reference-good',
      ),
      bashResult('call-reference-good'),
      ...fixture.messages,
    ];
    const audit = await auditMessages(messages, {
      ...fixture.options,
      referenceImages: [
        { path: reference, sha256: digest(referencePayload) },
      ],
      requireReferenceAnalysis: true,
    });
    assert.equal(audit.referenceAnalyzed, true);
    assert.equal(audit.pass, true);
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('any post-compile mutation or bash command invalidates compile evidence', async () => {
  const fixture = await evidenceFixture();
  try {
    assert.equal(
      (
        await auditMessages(
          [
            ...fixture.messages,
            assistantTool('write', {
              content: 'post-review bytes',
              path: join(fixture.root, 'notes.txt'),
            }),
          ],
          fixture.options,
        )
      ).pass,
      false,
    );
    assert.equal(
      (
        await auditMessages(
          [
            ...fixture.messages,
            assistantTool(
              'bash',
              { command: 'sed -i.bak s/old/new/ model.obj' },
              'call-shell-mutation',
            ),
            bashResult('call-shell-mutation'),
          ],
          fixture.options,
        )
      ).pass,
      false,
    );
    assert.equal(
      (
        await auditMessages(
          [
            ...fixture.messages,
            assistantTool(
              'bash',
              {
                command: `python "${SHAPE_SCRIPT}" --manifest scene.json`,
              },
              'call-shape',
            ),
            bashResult('call-shape'),
          ],
          fixture.options,
        )
      ).pass,
      false,
    );
  } finally {
    await rm(fixture.root, { force: true, recursive: true });
  }
});

test('visual repair instruction names file evidence and attempt budget', () => {
  const instruction = visualValidationRepairInstruction(failedAudit, {
    attempt: 2,
    maxAttempts: 3,
    requireReferenceAnalysis: true,
  });
  assert.match(instruction, /attempt 2\/3/u);
  assert.match(instruction, /reference_analyze\.py/u);
  assert.match(instruction, /evidence-render\/v2/u);
  assert.match(instruction, /evidence-a3d-build\/v1/u);
  assert.match(instruction, /read tool/u);
  assert.match(instruction, /then rebuild the final CAD/u);
});
