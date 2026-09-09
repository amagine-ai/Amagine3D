import { strict as assert } from 'node:assert';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { scanArtifacts } from '../server/artifacts.ts';
import { discoverModelBuilds } from '../server/model-builds.ts';
import {
  fixtureDigest,
  writeUnifiedBuildFixture,
} from './unified-build-fixture.ts';

test('discovers every plate and rejects missing, duplicated, or stale plate evidence', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-multi-plate-builds-'));
  try {
    const fixture = await writeUnifiedBuildFixture({ backend: 'brep-assembly', name: 'pair', root,
      parts: ['a', 'b'], plateParts: [['a'], ['b']] });
    const discover = () => scanArtifacts(root).then((files) => discoverModelBuilds(root, files));
    const [build] = await discover();
    assert.equal(build?.primaryPreviewPath, 'pair-plate-01.3mf');
    assert.deepEqual(build?.printPlates, [
      { id: '01', stlPath: 'pair-plate-01.stl', threeMfPath: 'pair-plate-01.3mf' },
      { id: '02', stlPath: 'pair-plate-02.stl', threeMfPath: 'pair-plate-02.3mf' },
    ]);
    assert.equal(build?.topLevelArtifactPaths.length, 5);
    for (const modify of [
      (report: any) => { report.backendData.printPlates[1].parts = ['a']; },
      (report: any) => { report.backendData.printPlates[1].geometry.volumeMm3 = 20; },
      (report: any) => { report.backendData.printPlates[1].id = '03'; },
      (report: any) => { report.backendData.printPlates = [null, {}]; },
      (report: any) => { delete report.artifacts['plate:02:3mf']; },
      (report: any) => { report.artifacts['plate:02:3mf'].path = report.artifacts['3mf'].path; },
    ]) {
      const report = structuredClone(fixture.report);
      modify(report);
      await writeFile(fixture.reportPath, JSON.stringify(report));
      assert.deepEqual(await discover(), []);
    }
    await writeFile(fixture.reportPath, JSON.stringify(fixture.report));
    await writeFile(join(root, 'pair-plate-02.3mf'), 'stale second plate');
    assert.deepEqual(await discover(), []);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('uses the artifact matrix to choose the unified print root', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-assembly-builds-'));
  try {
    await writeUnifiedBuildFixture({
      backend: 'brep-assembly',
      name: 'plain-case',
      root,
    });
    await writeUnifiedBuildFixture({
      backend: 'brep-color-regions',
      name: 'color-case',
      root,
    });
    const builds = await discoverModelBuilds(root, await scanArtifacts(root));
    const byId = new Map(builds.map((build) => [build.modelId, build]));
    assert.equal(byId.get('plain-case')?.primaryPreviewPath, 'plain-case.stl');
    assert.equal(byId.get('color-case')?.primaryPreviewPath, 'color-case.3mf');
    assert.equal(
      byId.get('color-case')?.displayPreviewPath,
      'color-case-display.glb',
    );
    assert.deepEqual(
      byId.get('color-case')?.topLevelArtifactPaths.sort(),
      ['color-case-display.glb', 'color-case.3mf'],
    );
    assert.equal(byId.get('plain-case')?.sourcePath, 'plain-case.py');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('discovers a Hybrid build without inventing an editable source', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-hybrid-build-'));
  try {
    await writeUnifiedBuildFixture({
      backend: 'hybrid-mesh',
      name: 'dragon',
      root,
    });
    const [build] = await discoverModelBuilds(root, await scanArtifacts(root));
    assert.equal(build?.primaryPreviewPath, 'dragon.3mf');
    assert.equal(build?.displayPreviewPath, 'dragon-display.glb');
    assert.equal(build?.sourcePath, undefined);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('uses the sole part STL for a BRep part build', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-brep-part-build-'));
  try {
    await writeUnifiedBuildFixture({
      backend: 'brep-part',
      name: 'bracket',
      root,
    });
    const [build] = await discoverModelBuilds(root, await scanArtifacts(root));
    assert.equal(build?.primaryPreviewPath, 'bracket.stl');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects legacy, failed, and incomplete build reports', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-invalid-builds-'));
  try {
    await writeUnifiedBuildFixture({
      name: 'legacy',
      root,
      schema: 'evidence-cad-build/v4',
    });
    await writeUnifiedBuildFixture({ name: 'failed', pass: false, root });
    const incomplete = await writeUnifiedBuildFixture({
      backend: 'hybrid-mesh',
      name: 'missing-package',
      root,
    });
    await rm(incomplete.threeMfPath!);
    const stale = await writeUnifiedBuildFixture({
      name: 'stale-payload',
      root,
    });
    stale.report.legacyPayload = { schema: 'evidence-cad-build/v4' };
    await writeFile(stale.reportPath, JSON.stringify(stale.report));
    const builds = await discoverModelBuilds(root, await scanArtifacts(root));
    assert.deepEqual(builds, []);
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects a hash-consistent BRep audit whose geometry does not match', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-forged-export-audit-'));
  try {
    const build = await writeUnifiedBuildFixture({
      backend: 'brep-part',
      name: 'forged-bracket',
      root,
    });
    const auditPath = join(root, 'forged-bracket_export-audit.json');
    const audit = JSON.parse(await readFile(auditPath, 'utf8')) as {
      artifacts: Record<string, { observed: { volumeMm3: number } }>;
    };
    audit.artifacts['stl:forged-bracket']!.observed.volumeMm3 = 2;
    const auditPayload = JSON.stringify(audit);
    await writeFile(auditPath, auditPayload);
    const report = build.report as {
      artifacts: { exportAudit: { sha256: string } };
      backendData: { exportAudit: unknown };
    };
    report.artifacts.exportAudit.sha256 = fixtureDigest(auditPayload);
    report.backendData.exportAudit = audit;
    await writeFile(build.reportPath, JSON.stringify(report));

    assert.deepEqual(
      await discoverModelBuilds(root, await scanArtifacts(root)),
      [],
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects unsupported nested fields for every build backend', async () => {
  const backends = [
    'brep-part',
    'brep-assembly',
    'brep-color-regions',
    'hybrid-mesh',
  ] as const;
  for (const backend of backends) {
    for (const target of ['backendData', 'part'] as const) {
      const root = await mkdtemp(join(tmpdir(), 'amagine-nested-contract-'));
      try {
        const build = await writeUnifiedBuildFixture({
          backend,
          name: `${backend}-${target}`,
          root,
        });
        if (target === 'backendData') {
          const backendData = build.report.backendData as Record<string, unknown>;
          backendData.legacyPayload = { acceptedByOldReaders: true };
        } else {
          const parts = build.report.parts as Record<
            string,
            Record<string, unknown>
          >;
          parts[Object.keys(parts)[0]!]!.legacyPayload = {
            acceptedByOldReaders: true,
          };
        }
        await writeFile(build.reportPath, JSON.stringify(build.report));
        assert.deepEqual(
          await discoverModelBuilds(root, await scanArtifacts(root)),
          [],
          `${backend} accepted an unsupported ${target} field`,
        );
      } finally {
        await rm(root, { force: true, recursive: true });
      }
    }
  }
});

test('rejects legacy, missing, duplicate, and unknown material source bindings', async () => {
  const mutations: Record<
    string,
    (plan: Record<string, unknown>) => void
  > = {
    duplicate: (plan) => {
      const bindings = plan.sourceBindings as Record<string, unknown>[];
      bindings.push({ ...bindings[0]! });
    },
    legacy: (plan) => {
      const bindings = plan.sourceBindings;
      delete plan.sourceBindings;
      plan.intentBindings = bindings;
    },
    missing: (plan) => {
      (plan.sourceBindings as unknown[]).pop();
    },
    'unknown-kind': (plan) => {
      const [binding] = plan.sourceBindings as Record<string, unknown>[];
      binding!.sourceKind = 'legacy-inferred-source';
    },
    'unknown-source': (plan) => {
      const [binding] = plan.sourceBindings as Record<string, unknown>[];
      binding!.sourceId = 'unknown-scene-material';
    },
  };

  for (const [name, mutate] of Object.entries(mutations)) {
    const root = await mkdtemp(join(tmpdir(), 'amagine-source-binding-'));
    try {
      const build = await writeUnifiedBuildFixture({
        backend: 'hybrid-mesh',
        name: `source-${name}`,
        root,
      });
      const report = build.report as {
        artifacts: { materialPlan: { path: string; sha256: string } };
        materialPlan: Record<string, unknown>;
      };
      mutate(report.materialPlan);
      const materialPayload = JSON.stringify(report.materialPlan);
      await writeFile(report.artifacts.materialPlan.path, materialPayload);
      report.artifacts.materialPlan.sha256 = fixtureDigest(materialPayload);
      await writeFile(build.reportPath, JSON.stringify(report));

      assert.deepEqual(
        await discoverModelBuilds(root, await scanArtifacts(root)),
        [],
        `accepted ${name} material source bindings`,
      );
    } finally {
      await rm(root, { force: true, recursive: true });
    }
  }
});
