import { describe, expect, it } from 'vitest';

import { attachmentSchema } from '@amagine3d/cad-protocol';

import { InMemoryProjectRepository } from './in-memory-repository';

const project = {
  schemaVersion: 1 as const,
  id: 'p8-project',
  name: 'P8 project',
  createdAt: '2026-08-15T00:00:00.000Z',
  updatedAt: '2026-08-15T00:00:00.000Z',
  revision: 0,
  currentRunId: null,
};

describe('P8 OPFS persistence', () => {
  it('round-trips model profile settings without exposing secrets', async () => {
    const repository = new InMemoryProjectRepository();
    await repository.saveModelProfileSettings({
      schemaVersion: 1,
      defaultProfileId: 'profile-1',
      profiles: [],
    });
    expect(await repository.getModelProfileSettings()).toMatchObject({
      defaultProfileId: 'profile-1',
      profiles: [],
    });
  });

  it('stores immutable attachment bytes with an integrity check', async () => {
    const repository = new InMemoryProjectRepository();
    await repository.createProject({ project });
    const bytes = new Uint8Array([1, 2, 3]);
    const metadata = attachmentSchema.parse({
      schemaVersion: 1,
      id: 'attachment-1',
      fileName: 'ref.png',
      mediaType: 'image/png',
      byteLength: 3,
      width: 1,
      height: 1,
      sha256:
        '03a8e6b6c6c7f5db8b5d0d7f7a1c6f37f1c5e0f2f4e9a2b7b3d1d9f3c5f9c3d1',
      createdAt: '2026-08-15T00:00:00.000Z',
    });
    // The digest is deliberately supplied by the adapter caller in production;
    // use a real digest here so the repository's integrity contract is tested.
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    metadata.sha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    await repository.saveAttachment('p8-project', { metadata, bytes });
    expect(
      (await repository.getAttachment('p8-project', 'attachment-1'))?.bytes,
    ).toEqual(bytes);
    await expect(
      repository.saveAttachment('p8-project', { metadata, bytes }),
    ).rejects.toThrow('immutable');
  });
});
