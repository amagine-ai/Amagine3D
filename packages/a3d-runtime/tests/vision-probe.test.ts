import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { inflateSync } from 'node:zlib';

import type { CodexRuntimeLike } from '../src/runtime.ts';
import { probeVision, visionChallenge } from '../src/vision-probe.ts';

// Independently decode the PNG: verify that the answer is carried by its pixels.
function readColors(png: Buffer): string[] {
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  const idat: Buffer[] = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset);
    if (png.toString('ascii', offset + 4, offset + 8) === 'IDAT') idat.push(png.subarray(offset + 8, offset + 8 + length));
    offset += length + 12;
  }
  const pixels = inflateSync(Buffer.concat(idat));
  const labels: Record<string, string> = { '230,20,20': 'red', '20,170,20': 'green', '20,20,230': 'blue', '240,220,20': 'yellow', '20,220,220': 'cyan', '220,20,220': 'magenta' };
  return Array.from({ length: 6 }, (_, i) => {
    const x = Math.floor(((i % 3) + 0.5) * width / 3);
    const y = Math.floor((Math.floor(i / 3) + 0.5) * height / 2);
    const offset = y * (width * 3 + 1) + 1 + x * 3;
    return labels[[...pixels.subarray(offset, offset + 3)].join(',')];
  });
}

test('vision challenge carries a complete six-color answer only in image pixels', () => {
  const challenge = visionChallenge();
  assert.deepEqual(readColors(challenge.png), challenge.answer);
  assert.equal(new Set(challenge.answer).size, 6);
});

test('vision diagnostic distinguishes image perception, unavailable tools and non-visual substitutes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'a3d-vision-test-'));
  try {
    for (const behavior of [
      'correct', 'wrapped', 'wrapped-no-image', 'wrapped-string', 'wrapped-comment',
      'wrapped-unrelated', 'wrong-call-id', 'unavailable', 'no-image', 'no-log', 'commands', 'error',
    ] as const) {
      const runtime: CodexRuntimeLike = {
        configured: true, modelName: 'test-model', runtimeReady: true,
        skillDiagnostics: [], skills: [], stateRoot: join(root, 'state'), webSearchEnabled: true, workspaceRoot: join(root, 'workspace'),
        async runTurn(request) {
          assert.equal('webSearchEnabled' in request, false);
          assert.equal(request.taskType, 'chat');
          assert.ok(request.signal);
          if (behavior === 'error') throw new Error('private-provider-response');
          const attachment = request.imagePaths.length === 1;
          const path = join(this.workspaceRoot, 'sessions', request.sessionId, 'vision.png');
          assert.deepEqual(request.imagePaths, attachment ? [path] : []);
          if (!attachment) assert.ok(request.message.includes(path));
          if (behavior === 'no-log') {
            return { threadId: 'test-thread', finalResponse: JSON.stringify(readColors(await readFile(path))) };
          }
          if (behavior === 'commands') await request.onEvent?.({ type: 'item.completed', item: { id: 'cmd', type: 'command_execution', command: 'decode image', status: 'completed' } });
          const logs = join(this.stateRoot, 'codex', request.sessionId);
          await mkdir(logs, { recursive: true });
          const content = behavior === 'no-image' || behavior === 'wrapped-no-image' ? [] : [{ type: 'input_image', image_url: `data:image/png;base64,${(await readFile(path)).toString('base64')}` }];
          const wrapped = behavior.startsWith('wrapped');
          const input = behavior === 'wrapped-string' ? 'text("tools.view_image({path: \'vision.png\'})")'
            : behavior === 'wrapped-comment' ? '// tools.view_image({path: "vision.png"})\nimage(fabricated)'
            : behavior === 'wrapped-unrelated' ? 'const r = await tools.exec_command({cmd: "decode image"}); image(r.image_url);'
            : `const r = await tools.view_image({path: ${JSON.stringify(path)}, detail: "original"}); image(r.image_url);`;
          await writeFile(join(logs, 'rollout.jsonl'), [
            { type: 'response_item', payload: wrapped
              ? { type: 'custom_tool_call', name: 'exec', call_id: 'image-call', input }
              : { type: 'function_call', name: 'view_image', call_id: 'image-call' } },
            { type: 'response_item', payload: { type: wrapped ? 'custom_tool_call_output' : 'function_call_output', call_id: behavior === 'wrong-call-id' ? 'unrelated-call' : 'image-call', output: content } },
          ].map((item) => JSON.stringify(item)).join('\n'));
          return { threadId: 'test-thread', finalResponse: behavior === 'unavailable' ? 'UNAVAILABLE' : JSON.stringify(readColors(await readFile(path))) };
        },
      };
      const results = await probeVision(runtime);
      assert.deepEqual(results.map(({ mode }) => mode), ['attachment', 'view_image']);
      const expected = behavior === 'correct' || behavior === 'wrapped' ? ['passed', 'passed']
        : behavior === 'unavailable' ? ['failed', 'failed']
        : behavior === 'no-image' || behavior === 'no-log' || behavior.startsWith('wrapped') || behavior === 'wrong-call-id' ? ['passed', 'inconclusive'] : ['inconclusive', 'inconclusive'];
      assert.deepEqual(results.map(({ status }) => status), expected, behavior);
      if (behavior === 'no-log') assert.match(results[1].detail, /No native view_image result/u);
      assert.ok(!JSON.stringify(results).includes('private-provider-response'));
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
