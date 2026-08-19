import { describe, expect, it } from 'vitest';

import { validateProjectArchive } from './archive';
import {
  ArchiveIntegrityMismatch,
  CorruptStoredData,
  StorageConflict,
  UnsafeArchivePath,
} from './errors';
import { MemoryFileStore } from './file-store';
import { encodeText, sha256 } from './hash';
import { OpfsProjectRepository } from './opfs-repository';
import {
  FIXED_NOW,
  MODEL_SOURCE,
  RESEARCH_PACKET,
  makeProject,
  makeRevision,
  makeStoredRun,
} from './test-fixtures';
import { decodeZip, encodeZip } from './zip';

function makeRepository(files = new MemoryFileStore()) {
  return new OpfsProjectRepository(files, {
    now: () => new Date(FIXED_NOW),
    createId: () => 'archive',
  });
}

function replaceAscii(
  input: Uint8Array,
  search: string,
  replacement: string,
): Uint8Array {
  const needle = encodeText(search);
  const value = encodeText(replacement);
  if (needle.byteLength !== value.byteLength) {
    throw new Error('Test replacement must preserve ZIP offsets.');
  }
  const output = input.slice();
  const matches: number[] = [];
  for (
    let offset = 0;
    offset <= output.byteLength - needle.byteLength;
    offset += 1
  ) {
    if (needle.every((byte, index) => output[offset + index] === byte)) {
      matches.push(offset);
      offset += needle.byteLength - 1;
    }
  }
  if (matches.length < 2) {
    throw new Error('Test ZIP did not contain the expected path.');
  }
  for (const offset of matches.slice(-2)) {
    output.set(value, offset);
  }
  return output;
}

describe('project ZIP import and export', () => {
  it('round-trips source, parameters, research, run, and artifact hashes', async () => {
    const source = makeRepository();
    const revision = await makeRevision();
    const run = await makeStoredRun();
    const stepBytes = encodeText('ISO-10303-21;\nEND-ISO-10303-21;\n');
    const stepArtifact = {
      schemaVersion: 1 as const,
      id: 'artifact-step',
      runId: 'run-1',
      kind: 'step' as const,
      fileName: 'model.step',
      mediaType: 'model/step',
      byteLength: stepBytes.byteLength,
      sha256: await sha256(stepBytes),
      createdAt: FIXED_NOW,
    };
    run.artifacts.push({ metadata: stepArtifact, bytes: stepBytes });
    run.run.artifactIds.push(stepArtifact.id);
    await source.createProject({
      project: makeProject({ currentRunId: 'run-1' }),
      revision,
    });
    await source.saveRun('project-1', run);

    const archive = await source.exportProject('project-1');
    expect(decodeZip(archive).has('project-1/runs/run-1/model.step')).toBe(
      true,
    );
    const target = makeRepository();
    expect(await target.preflightImport(archive)).toMatchObject({
      manifestProjectId: 'project-1',
      duplicateProject: false,
    });
    await target.importProject(archive);

    const importedRevision = await target.getRevision(
      'project-1',
      'revision-0',
    );
    const importedRun = await target.getRun('project-1', 'run-1');
    expect(importedRevision?.modelSource).toBe(MODEL_SOURCE);
    expect(importedRevision?.parameters).toEqual(revision.parameters);
    expect(importedRun?.research).toEqual(RESEARCH_PACKET);
    expect(importedRun?.run).toEqual(run.run);
    expect(importedRun?.artifacts[0]?.metadata.sha256).toBe(
      run.artifacts[0]?.metadata.sha256,
    );
    expect(importedRun?.artifacts[0]?.bytes).toEqual(run.artifacts[0]?.bytes);
    expect(
      importedRun?.artifacts.find(({ metadata }) => metadata.kind === 'step')
        ?.bytes,
    ).toEqual(stepBytes);
  });

  it('rejects a duplicate project without overwriting it', async () => {
    const source = makeRepository();
    await source.createProject({ project: makeProject() });
    const archive = await source.exportProject('project-1');

    expect((await source.preflightImport(archive)).duplicateProject).toBe(true);
    await expect(source.importProject(archive)).rejects.toBeInstanceOf(
      StorageConflict,
    );
    expect(await source.getProject('project-1')).toEqual(makeProject());
  });

  it('rejects malicious path traversal before import writes', async () => {
    const source = makeRepository();
    await source.createProject({ project: makeProject() });
    const archive = await source.exportProject('project-1');
    const malicious = replaceAscii(
      archive,
      'project-1/project.json',
      'project-1/../evil.json',
    );

    const targetFiles = new MemoryFileStore();
    await expect(
      makeRepository(targetFiles).importProject(malicious),
    ).rejects.toBeInstanceOf(UnsafeArchivePath);
    expect(await targetFiles.list()).toEqual([]);
  });

  it('rejects a manifest checksum mismatch', async () => {
    const source = makeRepository();
    await source.createProject({ project: makeProject() });
    const decoded = decodeZip(await source.exportProject('project-1'));
    decoded.set('project-1/project.json', encodeText('{"tampered":true}\n'));
    const tampered = encodeZip(
      [...decoded.entries()].map(([path, bytes]) => ({ path, bytes })),
    );

    await expect(validateProjectArchive(tampered)).rejects.toBeInstanceOf(
      ArchiveIntegrityMismatch,
    );
  });

  it('rolls back when project identity disagrees with the manifest', async () => {
    const source = makeRepository();
    await source.createProject({ project: makeProject() });
    const decoded = decodeZip(await source.exportProject('project-1'));
    const manifestBytes = decoded.get('manifest.json');
    if (manifestBytes === undefined) {
      throw new Error('Fixture manifest is missing.');
    }
    const manifest = JSON.parse(
      new TextDecoder().decode(manifestBytes),
    ) as Record<string, unknown>;
    manifest.projectName = 'Different project';
    decoded.set('manifest.json', encodeText(`${JSON.stringify(manifest)}\n`));
    const mismatched = encodeZip(
      [...decoded.entries()].map(([path, bytes]) => ({ path, bytes })),
    );
    const targetFiles = new MemoryFileStore();

    await expect(
      makeRepository(targetFiles).importProject(mismatched),
    ).rejects.toBeInstanceOf(CorruptStoredData);
    expect(await targetFiles.list()).toEqual([]);
  });
});
