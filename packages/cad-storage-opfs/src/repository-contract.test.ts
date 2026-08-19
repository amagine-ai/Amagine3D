import { describe, expect, it } from 'vitest';

import type { Artifact } from '@amagine3d/cad-protocol';

import { StorageConflict } from './errors';
import { MemoryFileStore, OpfsFileStore } from './file-store';
import { encodeText, sha256 } from './hash';
import { InMemoryProjectRepository } from './in-memory-repository';
import { createMockOpfsRoot } from './mock-opfs.test-helper';
import { OpfsProjectRepository } from './opfs-repository';
import {
  FIXED_NOW,
  makeEvent,
  makeProject,
  makeRevision,
  makeStoredRun,
} from './test-fixtures';
import type { BinaryArtifact, ProjectRepository } from './types';

type RepositoryHarness = {
  repository: ProjectRepository;
  refresh: () => ProjectRepository;
};

function repositoryContract(
  name: string,
  createHarness: () => RepositoryHarness,
): void {
  describe(`${name} ProjectRepository contract`, () => {
    it('creates, reads, updates, and lists a project and revision', async () => {
      const { repository } = createHarness();
      const revision = await makeRevision();
      await repository.createProject({
        project: makeProject(),
        revision,
        messages: {
          schemaVersion: 1,
          messages: [{ role: 'user', text: 'box' }],
        },
      });

      expect(await repository.listProjects()).toEqual([makeProject()]);
      expect(await repository.getRevision('project-1', revision.id)).toEqual(
        revision,
      );
      expect(await repository.getMessages('project-1')).toEqual({
        schemaVersion: 1,
        messages: [{ role: 'user', text: 'box' }],
      });
      if (revision.parameters === null) {
        throw new Error('Fixture parameters are missing.');
      }
      const changedParameters = {
        ...revision.parameters,
        parameters: revision.parameters.parameters.map((parameter) => ({
          ...parameter,
          value: 48,
        })),
      };
      await repository.saveParameters('project-1', changedParameters);
      expect(await repository.getParameters('project-1')).toEqual(
        changedParameters,
      );

      const updated = makeProject({
        revision: 1,
        updatedAt: '2026-08-13T09:00:00.000Z',
      });
      await repository.updateProject(updated, 0);
      expect(await repository.getProject('project-1')).toEqual(updated);
      await expect(repository.updateProject(updated, 0)).rejects.toBeInstanceOf(
        StorageConflict,
      );
    });

    it('recovers all data after a repository refresh', async () => {
      const harness = createHarness();
      const revision = await makeRevision();
      await harness.repository.createProject({
        project: makeProject({ currentRunId: 'run-1' }),
        revision,
      });
      const run = await makeStoredRun();
      await harness.repository.saveRun('project-1', run);
      await harness.repository.saveRunMessages('project-1', 'run-1', {
        schemaVersion: 1,
        messages: [{ role: 'user', text: 'run-scoped box' }],
      });

      const refreshed = harness.refresh();
      expect(await refreshed.getProject('project-1')).toEqual(
        makeProject({ currentRunId: 'run-1' }),
      );
      expect(await refreshed.getRevision('project-1', revision.id)).toEqual(
        revision,
      );
      expect(await refreshed.getParameters('project-1')).toEqual(
        revision.parameters,
      );
      const restoredRun = await refreshed.getRun('project-1', 'run-1');
      expect(await refreshed.getRunMessages('project-1', 'run-1')).toEqual({
        schemaVersion: 1,
        messages: [{ role: 'user', text: 'run-scoped box' }],
      });
      expect(restoredRun?.research?.sources).toEqual(run.research?.sources);
      expect(restoredRun?.artifacts[0]?.metadata.sha256).toBe(
        run.artifacts[0]?.metadata.sha256,
      );
    });

    it('allows active event append and makes a terminal run immutable', async () => {
      const { repository } = createHarness();
      await repository.createProject({ project: makeProject() });
      const active = await makeStoredRun('active', []);
      await repository.saveRun('project-1', active);
      await repository.appendEvent('project-1', 'run-1', makeEvent(0));
      expect(await repository.getEvents('project-1', 'run-1')).toEqual([
        makeEvent(0),
      ]);

      const completed = await makeStoredRun('succeeded', [makeEvent(0)]);
      await repository.saveRun('project-1', completed);
      await expect(
        repository.saveRun('project-1', completed),
      ).rejects.toBeInstanceOf(StorageConflict);
      await expect(
        repository.appendEvent('project-1', 'run-1', makeEvent(1)),
      ).rejects.toBeInstanceOf(StorageConflict);
    });

    it('rejects non-contiguous event logs', async () => {
      const { repository } = createHarness();
      await repository.createProject({ project: makeProject() });
      await expect(
        repository.saveRun(
          'project-1',
          await makeStoredRun('active', [makeEvent(1)]),
        ),
      ).rejects.toBeInstanceOf(StorageConflict);
    });

    it('rejects two artifacts that resolve to the same project path', async () => {
      const { repository } = createHarness();
      await repository.createProject({ project: makeProject() });
      const run = await makeStoredRun('active', []);
      const original = run.artifacts[0];
      if (original === undefined) {
        throw new Error('Fixture artifact is missing.');
      }
      run.artifacts.push({
        metadata: { ...original.metadata, id: 'artifact-copy' },
        bytes: original.bytes.slice(),
      });
      run.run.artifactIds.push('artifact-copy');

      await expect(repository.saveRun('project-1', run)).rejects.toBeInstanceOf(
        StorageConflict,
      );
    });

    it('stores multi-body stl and step artifacts under independent paths', async () => {
      const { repository } = createHarness();
      await repository.createProject({ project: makeProject() });
      const run = await makeStoredRun('active', []);
      const artifact = async (
        id: string,
        kind: Artifact['kind'],
        fileName: string,
        content: string,
      ): Promise<BinaryArtifact> => {
        const bytes = encodeText(content);
        return {
          metadata: {
            schemaVersion: 1,
            id,
            runId: 'run-1',
            kind,
            fileName,
            mediaType: kind === 'step' ? 'model/step' : 'model/stl',
            byteLength: bytes.byteLength,
            sha256: await sha256(bytes),
            createdAt: FIXED_NOW,
          },
          bytes,
        };
      };
      run.run.artifactIds = ['stl-body-a', 'stl-body-b', 'step-body-a'];
      run.artifacts = [
        await artifact(
          'stl-body-a',
          'stl',
          'model-body-a.stl',
          'solid bodyA\nendsolid bodyA\n',
        ),
        await artifact(
          'stl-body-b',
          'stl',
          'model-body-b.stl',
          'solid bodyB\nendsolid bodyB\n',
        ),
        await artifact(
          'step-body-a',
          'step',
          'model-body-a.step',
          'STEP bodyA',
        ),
      ];

      await expect(
        repository.saveRun('project-1', run),
      ).resolves.toBeUndefined();
      const restored = await repository.getRun('project-1', 'run-1');
      expect(
        restored?.artifacts.map(({ metadata }) => metadata.id).sort(),
      ).toEqual(['step-body-a', 'stl-body-a', 'stl-body-b']);
      const bodyA = restored?.artifacts.find(
        ({ metadata }) => metadata.id === 'stl-body-a',
      );
      const bodyB = restored?.artifacts.find(
        ({ metadata }) => metadata.id === 'stl-body-b',
      );
      const stepA = restored?.artifacts.find(
        ({ metadata }) => metadata.id === 'step-body-a',
      );
      expect(bodyA?.bytes).toEqual(encodeText('solid bodyA\nendsolid bodyA\n'));
      expect(bodyB?.bytes).toEqual(encodeText('solid bodyB\nendsolid bodyB\n'));
      expect(stepA?.bytes).toEqual(encodeText('STEP bodyA'));
    });
  });
}

repositoryContract('in-memory fake', () => {
  const files = new MemoryFileStore();
  const makeRepository = () =>
    new InMemoryProjectRepository(
      {
        now: () => new Date(FIXED_NOW),
        createId: () => 'memory',
      },
      files,
    );
  return { repository: makeRepository(), refresh: makeRepository };
});

repositoryContract('OPFS adapter', () => {
  const root = createMockOpfsRoot();
  const makeRepository = () =>
    new OpfsProjectRepository(new OpfsFileStore(root), {
      now: () => new Date(FIXED_NOW),
      createId: () => 'opfs',
    });
  return { repository: makeRepository(), refresh: makeRepository };
});
