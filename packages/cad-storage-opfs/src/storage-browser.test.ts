import { describe, expect, it } from 'vitest';

import { MemoryFileStore } from './file-store';
import {
  listWorkspaceFiles,
  readWorkspaceFile,
  removeWorkspaceFiles,
} from './storage-browser';

describe('OPFS workspace file browser', () => {
  it('lists model and execution files without transaction internals', async () => {
    const files = new MemoryFileStore();
    await files.write('project-a/runs/run-1/model.step', new Uint8Array(12));
    await files.write('project-a/runs/run-1/run.json', new Uint8Array(8));
    await files.write(
      'project-a/runs/run-1/run.json.commit',
      new Uint8Array(4),
    );
    await files.write(
      'project-a/runs/run-1/run.json.tmp-generation',
      new Uint8Array(8),
    );
    await files.write('project-a/notes.txt', new Uint8Array(2));

    await expect(listWorkspaceFiles(files)).resolves.toEqual([
      {
        path: 'project-a/runs/run-1/model.step',
        fileName: 'model.step',
        projectId: 'project-a',
        runId: 'run-1',
        category: 'model',
        byteLength: 12,
      },
      {
        path: 'project-a/runs/run-1/run.json',
        fileName: 'run.json',
        projectId: 'project-a',
        runId: 'run-1',
        category: 'execution',
        byteLength: 8,
      },
    ]);
  });

  it('removes the selected logical file and its committed generations', async () => {
    const files = new MemoryFileStore();
    await files.write('project-a/runs/run-1/run.json', new Uint8Array(8));
    await files.write(
      'project-a/runs/run-1/run.json.commit',
      new Uint8Array(4),
    );
    await files.write(
      'project-a/runs/run-1/run.json.tmp-generation',
      new Uint8Array(8),
    );
    await files.write('project-a/runs/run-1/model.stl', new Uint8Array(12));

    await removeWorkspaceFiles(files, ['project-a/runs/run-1/run.json']);

    await expect(files.list()).resolves.toEqual([
      'project-a/runs/run-1/model.stl',
    ]);
  });

  it('refuses paths that were not exposed by the browser listing', async () => {
    const files = new MemoryFileStore();
    await files.write('project-a/internal.bin', new Uint8Array(1));

    await expect(
      removeWorkspaceFiles(files, ['project-a/internal.bin']),
    ).rejects.toThrow('refused unknown or internal path');
  });

  it('reads an exposed workspace file back for preview', async () => {
    const files = new MemoryFileStore();
    await files.write(
      'project-a/runs/run-1/run.json',
      new Uint8Array([1, 2, 3]),
    );

    await expect(
      readWorkspaceFile(files, 'project-a/runs/run-1/run.json'),
    ).resolves.toEqual(new Uint8Array([1, 2, 3]));
  });

  it('refuses to read hidden internal files', async () => {
    const files = new MemoryFileStore();
    await files.write('project-a/internal.bin', new Uint8Array(1));

    await expect(
      readWorkspaceFile(files, 'project-a/internal.bin'),
    ).rejects.toThrow('refused unknown or internal path');
  });
});
