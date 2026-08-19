import { describe, expect, it } from 'vitest';

import { CommittedJsonStore } from './committed-json';
import { CorruptStoredData } from './errors';
import { MemoryFileStore } from './file-store';
import { encodeText } from './hash';
import { OpfsProjectRepository } from './opfs-repository';
import { FIXED_NOW, makeProject } from './test-fixtures';

function repository(files: MemoryFileStore) {
  return new OpfsProjectRepository(files, {
    now: () => new Date(FIXED_NOW),
    createId: () => 'recovery',
  });
}

class TemporarilyLockedFileStore extends MemoryFileStore {
  lockedPath: string | undefined;

  override async remove(path: string): Promise<void> {
    if (path === this.lockedPath) {
      throw new DOMException(
        'The entry is temporarily locked.',
        'NoModificationAllowedError',
      );
    }
    await super.remove(path);
  }
}

class SwapFileLockingFileStore extends MemoryFileStore {
  private readonly activeWrites = new Set<string>();

  override async write(path: string, contents: Uint8Array): Promise<void> {
    if (this.activeWrites.has(path)) {
      throw new DOMException(
        'Failed to create swap file.',
        'InvalidStateError',
      );
    }
    this.activeWrites.add(path);
    try {
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
      await super.write(path, contents);
    } finally {
      this.activeWrites.delete(path);
    }
  }
}

class FailOnceFileStore extends MemoryFileStore {
  failPath: string | undefined;

  override async write(path: string, contents: Uint8Array): Promise<void> {
    if (path === this.failPath) {
      this.failPath = undefined;
      throw new Error('simulated write failure');
    }
    await super.write(path, contents);
  }
}

describe('OPFS crash recovery and migration', () => {
  it('serializes concurrent commits to the same JSON document', async () => {
    const files = new SwapFileLockingFileStore();
    let id = 0;
    const store = new CommittedJsonStore(files, {
      now: () => new Date(FIXED_NOW),
      createId: () => `concurrent-${String(++id)}`,
    });

    await expect(
      Promise.all([
        store.write('project-1/messages.json', { value: 1 }),
        store.write('project-1/messages.json', { value: 2 }),
      ]),
    ).resolves.toEqual([undefined, undefined]);
    expect(await files.read('project-1/messages.json')).toEqual(
      encodeText('{"value":2}\n'),
    );
  });

  it('continues the write queue after an earlier commit fails', async () => {
    const files = new FailOnceFileStore();
    const store = new CommittedJsonStore(files, {
      now: () => new Date(FIXED_NOW),
      createId: () => crypto.randomUUID(),
    });
    files.failPath = 'project-1/messages.json';

    await expect(
      store.write('project-1/messages.json', { value: 1 }),
    ).rejects.toThrow(/simulated write failure/u);
    await expect(
      store.write('project-1/messages.json', { value: 2 }),
    ).resolves.toBeUndefined();
    expect(await files.read('project-1/messages.json')).toEqual(
      encodeText('{"value":2}\n'),
    );
  });

  it('defers locked old-generation cleanup after a successful commit', async () => {
    const files = new TemporarilyLockedFileStore();
    let id = 0;
    const store = new CommittedJsonStore(files, {
      now: () => new Date(FIXED_NOW),
      createId: () => `generation-${String(++id)}`,
    });
    await store.write('project-1/messages.json', { value: 1 });
    const firstGeneration = (
      await files.list('project-1/messages.json.tmp-')
    )[0];
    files.lockedPath = firstGeneration;

    await expect(
      store.write('project-1/messages.json', { value: 2 }),
    ).resolves.toBeUndefined();
    expect(await files.read('project-1/messages.json')).toEqual(
      encodeText('{"value":2}\n'),
    );
    expect(await files.read(firstGeneration ?? '')).toBeDefined();
  });

  it('recovers when a crash happens after the commit marker but before canonical write', async () => {
    const files = new MemoryFileStore();
    files.beforeWrite = (path) => {
      if (path === 'project-1/project.json') {
        throw new Error('simulated page crash');
      }
    };
    await expect(
      repository(files).createProject({ project: makeProject() }),
    ).rejects.toThrow(/simulated page crash/u);

    files.beforeWrite = undefined;
    const recovered = repository(files);
    expect(await recovered.listProjects()).toEqual([makeProject()]);
    expect(recovered.getRecoveryDiagnostics()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'canonical-restored' }),
      ]),
    );
  });

  it('restores a partially overwritten canonical JSON from its committed generation', async () => {
    const files = new MemoryFileStore();
    await repository(files).createProject({ project: makeProject() });
    await files.write(
      'project-1/project.json',
      encodeText('{"schemaVersion":'),
    );

    const recovered = repository(files);
    expect(await recovered.getProject('project-1')).toEqual(makeProject());
    expect(
      recovered.getRecoveryDiagnostics().map(({ code }) => code),
    ).toContain('canonical-restored');
    expect(
      (await files.list('project-1/corrupt/')).some((path) =>
        path.endsWith('-project.json'),
      ),
    ).toBe(true);
  });

  it('migrates a legacy version-zero project and records the migration', async () => {
    const files = new MemoryFileStore();
    const legacy = makeProject();
    const v0 = {
      id: legacy.id,
      name: legacy.name,
      createdAt: legacy.createdAt,
      updatedAt: legacy.updatedAt,
      revision: legacy.revision,
    };
    await files.write(
      'project-1/project.json',
      encodeText(`${JSON.stringify(v0)}\n`),
    );
    const migrated = repository(files);

    expect(await migrated.getProject('project-1')).toEqual(legacy);
    expect(migrated.getRecoveryDiagnostics()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'migration-applied' }),
      ]),
    );
  });

  it('quarantines irrecoverable JSON with a diagnostic error', async () => {
    const files = new MemoryFileStore();
    await files.write('project-1/project.json', encodeText('not json'));
    const damaged = repository(files);

    await expect(damaged.getProject('project-1')).rejects.toBeInstanceOf(
      CorruptStoredData,
    );
    expect(await files.read('project-1/project.json')).toBeUndefined();
    expect(await files.list('project-1/corrupt/')).toHaveLength(1);
    expect(damaged.getRecoveryDiagnostics()[0]?.code).toBe(
      'corrupt-file-quarantined',
    );
  });
});
