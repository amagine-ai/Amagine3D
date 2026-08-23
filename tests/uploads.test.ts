import { strict as assert } from 'node:assert';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  appendSavedImageContext,
  saveImageAttachments,
} from '../server/uploads.ts';

test('persists images under a session-scoped generated filename', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pi-agent-upload-'));
  try {
    const bytes = Buffer.from('validated-image-bytes');
    const [saved] = await saveImageAttachments(
      root,
      '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93',
      [
        {
          data: bytes.toString('base64'),
          mimeType: 'image/png',
          name: '../../prompt-injection.png',
        },
      ],
    );

    assert.deepEqual(await readFile(saved.path), bytes);
    assert.match(
      saved.path,
      /uploads\/3b0d4f25-1707-4cc8-92cf-6f5c28edfc93\/[0-9a-f-]+\.png$/,
    );
    const prompt = appendSavedImageContext('建模', [saved]);
    assert.match(prompt, /<uploaded_image_files>/);
    assert.doesNotMatch(prompt, /prompt-injection/);
    assert.match(prompt, /never as instructions/);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
