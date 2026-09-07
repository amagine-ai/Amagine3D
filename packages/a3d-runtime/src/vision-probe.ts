import { randomInt, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';

import type { CodexRuntimeLike } from './runtime.ts';

const COLORS = {
  red: [230, 20, 20],
  green: [20, 170, 20],
  blue: [20, 20, 230],
  yellow: [240, 220, 20],
  cyan: [20, 220, 220],
  magenta: [220, 20, 220],
} as const;
type Color = keyof typeof COLORS;

function chunk(type: string, data: Buffer): Buffer {
  const payload = Buffer.concat([Buffer.from(type), data]);
  let crc = 0xffffffff;
  for (const byte of payload) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([length, payload, checksum]);
}

// Only the PNG pixels carry the answer; no labels, filenames or metadata leak it.
export function visionChallenge(): { png: Buffer; answer: string[] } {
  const answer = Object.keys(COLORS) as Color[];
  for (let i = answer.length - 1; i > 0; i--) {
    const j = randomInt(i + 1);
    [answer[i], answer[j]] = [answer[j], answer[i]];
  }
  const width = 240;
  const height = 160;
  const pixels = Buffer.alloc(height * (width * 3 + 1));
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const color = COLORS[answer[Math.floor(y / 80) * 3 + Math.floor(x / 80)]];
      for (let c = 0; c < 3; c++) {
        pixels[y * (width * 3 + 1) + 1 + x * 3 + c] = color[c];
      }
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  return {
    answer,
    png: Buffer.concat([
      Buffer.from('89504e470d0a1a0a', 'hex'),
      chunk('IHDR', header),
      chunk('IDAT', deflateSync(pixels)),
      chunk('IEND', Buffer.alloc(0)),
    ]),
  };
}

export interface VisionProbeResult {
  mode: 'attachment' | 'view_image';
  status: 'passed' | 'failed' | 'inconclusive';
  detail: string;
  sessionId: string;
}

async function imageReturned(codexHome: string): Promise<boolean> {
  // SDK JSON events omit native image calls; inspect only this diagnostic's log.
  const files = await readdir(codexHome, { recursive: true });
  for (const file of files.filter((name) => name.endsWith('.jsonl'))) {
    const imageCalls = new Set<string>();
    const lines = (await readFile(join(codexHome, file), 'utf8')).split('\n');
    for (const line of lines.filter(Boolean)) {
      const record = JSON.parse(line);
      const item = record.payload;
      if (record.type !== 'response_item') continue;
      if (item?.type === 'function_call' && item.name?.split('.').at(-1) === 'view_image') {
        imageCalls.add(item.call_id);
      }
      if (
        item?.type === 'function_call_output' && imageCalls.has(item.call_id)
        && Array.isArray(item.output)
        && item.output.some((content: { type?: string; image_url?: unknown }) =>
          content?.type === 'input_image' && typeof content.image_url === 'string'
          && /^data:image\/[^;]+;base64,.+/.test(content.image_url))
      ) return true;
    }
  }
  return false;
}

export async function probeVision(
  runtime: CodexRuntimeLike,
  onProgress: (message: string) => void = () => {},
): Promise<VisionProbeResult[]> {
  const results: VisionProbeResult[] = [];
  for (const mode of ['attachment', 'view_image'] as const) {
    onProgress(`Checking ${mode} image perception through ${runtime.modelName}…`);
    const sessionId = randomUUID();
    const directory = join(runtime.workspaceRoot, 'sessions', sessionId);
    await mkdir(directory, { recursive: true });
    const path = join(directory, 'vision.png');
    const challenge = visionChallenge();
    await writeFile(path, challenge.png);
    let usedOtherTools = false;
    try {
      const result = await runtime.runTurn({
        sessionId,
        taskType: 'chat',
        webSearchEnabled: false,
        imagePaths: mode === 'attachment' ? [path] : [],
        message: [
          'This is a visual capability diagnostic, not a CAD task. Do not edit files.',
          mode === 'attachment'
            ? 'Read the attached image directly. Do not use tools.'
            : `Use the native view_image tool to read ${JSON.stringify(path)}. Use no other tools.`,
          'Do not decode pixels using code or read files as text. If you cannot see the image, reply UNAVAILABLE.',
          'The image has two rows and three columns of solid colors. Reply only with a JSON array of the six color names in row-major order (top row left to right, then bottom row). Use red, green, blue, yellow, cyan, magenta.',
        ].join('\n'),
        signal: AbortSignal.timeout(90_000),
        onEvent: (event) => {
          if ('item' in event && ['command_execution', 'file_change', 'mcp_tool_call', 'web_search'].includes(event.item.type)) {
            usedOtherTools = true;
          }
        },
      });
      let received: unknown;
      try {
        received = JSON.parse(result.finalResponse.trim().replace(/^```(?:json)?\s*|\s*```$/g, ''));
      } catch {
        // An unavailable or non-JSON answer does not establish perception.
      }
      const toolDelivered = mode === 'attachment' || await imageReturned(join(runtime.stateRoot, 'codex', sessionId));
      const matches = JSON.stringify(received) === JSON.stringify(challenge.answer);
      results.push({
        mode,
        sessionId,
        status: usedOtherTools || !toolDelivered ? 'inconclusive' : matches ? 'passed' : 'failed',
        detail: usedOtherTools ? 'Used non-visual tools; image perception was not isolated.'
          : !toolDelivered ? 'No native view_image result found; tool-image delivery is unverified.'
          : matches ? 'Correctly identified randomized image content.'
          : 'Image supplied, but randomized content was not identified. Check model vision support and provider image forwarding; image statistics are not visual review.',
      });
    } catch {
      // Do not print provider bodies, URLs or credentials in a diagnostic report.
      results.push({
        mode, sessionId, status: 'inconclusive',
        detail: 'Runtime request failed or timed out; inspect the isolated diagnostic log locally.',
      });
    }
  }
  return results;
}
