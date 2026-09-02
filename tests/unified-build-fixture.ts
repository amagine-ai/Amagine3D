import { createHash, randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const IDENTITY = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

function geometryRecord(bodyCount = 1) {
  return {
    bodyCount,
    boundsMm: { max: [1, 1, 1], min: [0, 0, 0], size: [1, 1, 1] },
    isVolume: true,
    valid: true,
    volumeMm3: bodyCount,
  };
}

function rawBrepShape(solidCount = 1) {
  return {
    bbox_mm: { max: [1, 1, 1], min: [0, 0, 0], size: [1, 1, 1] },
    solid_count: solidCount,
    valid: true,
    volume_mm3: solidCount,
  };
}

function printOrientation() {
  return {
    candidates: [{ name: 'identity' }],
    selected: { name: 'identity' },
    strategy: 'fixture-rigid-orientation',
  };
}

function overlapMap(parts: readonly string[]) {
  return Object.fromEntries(
    parts.flatMap((left, index) =>
      parts.slice(index + 1).map((right) => [`${left}&${right}`, 0]),
    ),
  );
}

export interface UnifiedBuildFixtureOptions {
  backend?:
    | 'brep-assembly'
    | 'brep-color-regions'
    | 'brep-part'
    | 'hybrid-mesh';
  colored?: boolean;
  name: string;
  parts?: string[];
  pass?: boolean;
  root: string;
  schema?: string;
  sourceContent?: string;
}

export function fixtureDigest(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex');
}

function materialPlan(
  name: string,
  parts: readonly string[],
  backend: UnifiedBuildFixtureOptions['backend'],
) {
  const intentColorSource = backend === 'brep-color-regions';
  const scope =
    backend === 'brep-color-regions' ? 'brep-region' : 'whole-part';
  const materials = parts.map((part, index) => ({
    color: index % 2 === 0 ? '#F05A35' : '#171717',
    fieldStatus: {
      color: intentColorSource ? 'declared' : 'proposed',
      filament: 'proposed',
      transmission: 'proposed',
    },
    filament: null,
    id: `material-${part}`,
    status: intentColorSource ? 'declared' : 'proposed',
    transmission: null,
  }));
  return {
    archiveEncodes: ['part', 'region', 'rgb'],
    archiveOmits: ['filament', 'transmission', 'slicer-filament-slot'],
    assignments: parts.map((part) => ({
      materialId: `material-${part}`,
      part,
      region: scope === 'whole-part' ? null : `${part}-region`,
      scope,
    })),
    coordinateFrame: 'plate-print',
    sourceBindings: parts.map((part, index) => ({
      color: materials[index]!.color,
      filament: null,
      materialId: `material-${part}`,
      materialStatus: intentColorSource ? 'declared' : 'proposed',
      part,
      region: scope === 'whole-part' ? null : `${part}-region`,
      scope,
      sourceId: intentColorSource ? `${part}-region` : `material-${part}`,
      sourceKind: intentColorSource
        ? 'intent-color-region'
        : 'scene-part-material',
      transmission: null,
    })),
    materials,
    packageMode: parts.length === 1 ? 'co_print_body' : 'separate_parts',
    part: name,
    requiresManualSlicerAssignment: true,
    scale: 1,
    schema: 'evidence-color-material-plan/v1',
  };
}

export async function writeUnifiedBuildFixture(
  options: UnifiedBuildFixtureOptions,
): Promise<{
  displayPath: string;
  report: Record<string, unknown>;
  reportPath: string;
  sourcePath?: string;
  stlPath: string;
  threeMfPath?: string;
}> {
  const {
    backend = 'brep-part',
    colored = ['brep-color-regions', 'hybrid-mesh'].includes(backend),
    name,
    parts =
      backend === 'brep-assembly' ? [`${name}-base`, `${name}-cover`] : [name],
    pass = true,
    root,
    schema = 'evidence-a3d-build/v1',
    sourceContent = '# parametric CAD source\n',
  } = options;
  const hybrid = backend === 'hybrid-mesh';
  const needsThreeMf = colored;
  const revision = `${name}-revision-1`;
  const hybridStepConsistency = {
    parts: {},
    pass: true,
    revision,
    schema: 'evidence-step-consistency/v1',
  };

  const writeBound = async (filename: string, payload: string | Buffer) => {
    const path = join(root, filename);
    await writeFile(path, payload);
    return { path, sha256: fixtureDigest(payload) };
  };

  const profile = await writeBound(
    `${name}_profile.json`,
    JSON.stringify({ schema: 'evidence-bambu-printer-profile/v1' }),
  );
  const featureIds = Object.fromEntries(
    parts.map((part) => [part, `${part}/body`]),
  );
  const manufacturing =
    parts.length === 1
      ? { mode: 'single-part' }
      : {
          interfaces: [
            {
              acceptance: 'Fixture parts retain their declared ownership.',
              assembly_axis: '+Z',
              between: [parts[0], parts[1]],
              clearance_mm: 0,
              connection: 'glue-face',
              engagement_mm: 1,
              features: [featureIds[parts[0]!], featureIds[parts[1]!]],
              id: `${parts[0]}-${parts[1]}-fixture`,
            },
          ],
          mode: 'multipart',
          parts: parts.map((part) => ({
            acceptance: `${part} remains a separately identified part.`,
            name: part,
            role: `physical ${part} test part`,
          })),
        };
  const colorRegions =
    backend === 'brep-color-regions'
      ? parts.map((part, index) => ({
          boundary: `the complete ${part} fixture region`,
          continuity: 'continuous-core',
          evidence: `the fixture declares ${part} as a manufactured color region`,
          hex: index % 2 === 0 ? '#F05A35' : '#171717',
          name: `${part}-region`,
          part,
          purpose: 'fixture manufactured color',
        }))
      : undefined;
  const intent = await writeBound(
    `${name}_intent.json`,
    JSON.stringify({
      assumptions: ['fixture dimensions are test-only evidence'],
      coordinate_system: {
        back: 'y-max',
        bottom: 'z-min',
        front: 'y-min',
        left: 'x-min',
        right: 'x-max',
        top: 'z-max',
        x_positive: 'right',
        y_positive: 'back',
        z_positive: 'top',
      },
      dimensions_mm: Object.fromEntries(
        ['x', 'y', 'z'].map((axis) => [
          axis,
          { confidence: 'high', source: 'user', value: 1 },
        ]),
      ),
      features: parts.map((part) => ({
        acceptance: `${part} remains present in the compiled fixture.`,
        evidence: `the fixture declares the ${part} body`,
        id: featureIds[part],
        kind: 'body',
        part,
      })),
      manufacturing,
      part: name,
      printability: {
        bed_contact: 'z-min',
        build_axis: '+Z',
        critical_features: Object.values(featureIds),
        minimum_wall_target_mm: 0.9,
        ...(needsThreeMf
          ? {
              print_package_mode:
                parts.length === 1 ? 'co_print_body' : 'separate_parts',
            }
          : {}),
        profile: { path: profile.path, sha256: profile.sha256 },
        support_policy: 'support-free',
      },
      reference_files: [],
      representation: 'full-3d',
      schema: 'evidence-cad-intent/v4',
      task_mode: 'specification',
      visual: {
        landmarks: ['all fixture parts remain identifiable'],
        reference_view: 'isometric',
        required: true,
      },
      ...(colorRegions
        ? {
            color_regions: colorRegions,
            palette_reduction: {
              applied: false,
              reason: 'fixture preserves every declared region',
            },
          }
        : {}),
    }),
  );
  const scene = await writeBound(
    `${name}_scene.json`,
    JSON.stringify({
      coordinateSystem: { handedness: 'right', up: 'Z' },
      interfaces: [],
      intentRef: {
        path: intent.path,
        schema: 'evidence-cad-intent/v4',
        sha256: intent.sha256,
      },
      materials: parts.map((part, index) => ({
        color: index % 2 === 0 ? '#F05A35' : '#171717',
        id: `material-${part}`,
      })),
      nodes: parts.map((part) => ({
        featureId: featureIds[part],
        id: `${part}-body`,
        operation: parts.length === 1 ? 'union' : 'none',
        partId: part,
        recipe: {
          kind: 'sourceMesh',
          parameters: { sourceMesh: `${part}.stl` },
        },
        role: parts.length === 1 ? 'solid' : 'separate',
      })),
      parts: parts.map((part) => ({
        id: part,
        materialId: `material-${part}`,
        representationMaster: hybrid ? 'mesh' : 'brep',
      })),
      revision,
      schema: 'evidence-semantic-scene/v1',
      units: 'mm',
    }),
  );
  const display = await writeBound(`${name}-display.glb`, 'glTF-display');
  const artifacts: Record<string, Record<string, unknown>> = {
    'glb:display': { ...display, coordinateFrame: 'semantic' },
  };

  for (const part of parts) {
    const partStem = parts.length === 1 && part === name ? name : `${name}-${part}`;
    const stl = await writeBound(`${partStem}.stl`, `solid ${part}\nendsolid ${part}\n`);
    artifacts[`stl:${part}`] = {
      ...stl,
      coordinateFrame: 'part-print',
    };
    if (!hybrid) {
      const step = await writeBound(`${partStem}.step`, `ISO-10303-21 ${part}`);
      artifacts[`step:${part}`] = {
        ...step,
        coordinateFrame: 'semantic',
      };
    }
  }

  let topStl = artifacts[`stl:${parts[0]}`]!.path as string;
  if (backend === 'brep-assembly' || hybrid) {
    const stl = await writeBound(`${name}.stl`, `solid ${name}\nendsolid ${name}\n`);
    artifacts.stl = { ...stl, coordinateFrame: 'plate-print' };
    topStl = stl.path;
  }
  if (backend === 'brep-assembly') {
    const assemblyStep = await writeBound(`${name}-assemble.step`, 'ISO-10303-21 assembly');
    artifacts['step:assembly'] = {
      ...assemblyStep,
      coordinateFrame: 'semantic',
    };
  }
  if (backend === 'brep-color-regions') {
    const semanticRegion = await writeBound(
      `${name}-region-semantic.stl`,
      `solid ${name}-region-semantic\nendsolid ${name}-region-semantic\n`,
    );
    const printRegion = await writeBound(
      `${name}-region-print.stl`,
      `solid ${name}-region-print\nendsolid ${name}-region-print\n`,
    );
    artifacts[`region:${name}-region:semantic`] = {
      ...semanticRegion,
      coordinateFrame: 'semantic',
    };
    artifacts[`region:${name}-region:print`] = {
      ...printRegion,
      coordinateFrame: 'part-print',
    };
  }
  if (hybrid) {
    for (const [key, filename, payload] of [
      [
        'boundScene',
        `${name}_scene_artifacts.json`,
        { revision, schema: 'evidence-semantic-scene/v1' },
      ],
      [
        'shapeConsistency',
        `${name}_shape-consistency.json`,
        {
          parts: Object.fromEntries(
            parts.map((part) => [
              part,
              {
                meshes: {
                  a: { boundsMm: geometryRecord().boundsMm },
                  b: { boundsMm: geometryRecord().boundsMm },
                },
                pass: true,
              },
            ]),
          ),
          pass: true,
          revision,
          schema: 'evidence-shape-consistency-manifest/v1',
          skippedParts: [],
        },
      ],
      [
        'stepConsistency',
        `${name}_step-consistency.json`,
        hybridStepConsistency,
      ],
    ] as const) {
      artifacts[key] = await writeBound(filename, JSON.stringify(payload));
    }
  }

  let inlineMaterialPlan: ReturnType<typeof materialPlan> | undefined;
  let threeMfPath: string | undefined;
  if (needsThreeMf) {
    if (backend === 'brep-assembly') {
      for (const part of parts) {
        const plateStl = await writeBound(
          `${name}-${part}-plate.stl`,
          `solid ${part}-plate\nendsolid ${part}-plate\n`,
        );
        artifacts[`plate-stl:${part}`] = {
          ...plateStl,
          coordinateFrame: 'plate-print',
        };
      }
    }
    const threeMf = await writeBound(`${name}.3mf`, '3MF-package');
    threeMfPath = threeMf.path;
    artifacts['3mf'] = {
      ...threeMf,
      coordinateFrame: 'plate-print',
      validator: 'lib3mf',
      verified: true,
    };
    inlineMaterialPlan = materialPlan(name, parts, backend);
    const plan = await writeBound(
      `${name}_material-plan.json`,
      JSON.stringify(inlineMaterialPlan),
    );
    artifacts.materialPlan = plan;
  }

  let exportAudit: Record<string, unknown> | undefined;
  if (!hybrid) {
    const expectedGeometry = {
      bodyCount: 1,
      boundsMm: { min: [0, 0, 0], max: [1, 1, 1], size: [1, 1, 1] },
      valid: true,
      volumeMm3: 1,
    };
    const audited = Object.fromEntries(
      Object.entries(artifacts)
        .filter(
          ([key]) =>
            key === 'glb:display' ||
            key === 'stl' ||
            key.startsWith('stl:') ||
            key.startsWith('step:') ||
            key.startsWith('plate-stl:') ||
            key.startsWith('region:'),
        )
        .map(([key, artifact]) => {
          if (key === 'glb:display') {
            const nodes = [`${name}-display-node`];
            return [
              key,
              {
                errors: [],
                expectedNodes: nodes,
                observed: { geometryCount: 1, nodes },
                pass: true,
                path: artifact.path,
                reader: 'trimesh-gltf',
                sha256: artifact.sha256,
                type: 'glb',
              },
            ];
          }
          const type = key.startsWith('step:') ? 'step' : 'stl';
          return [
            key,
            {
              errors: [],
              expected: expectedGeometry,
              observed:
                type === 'stl'
                  ? {
                      ...expectedGeometry,
                      faceCount: 12,
                      vertexCount: 8,
                      watertight: true,
                      windingConsistent: true,
                    }
                  : expectedGeometry,
              pass: true,
              path: artifact.path,
              ...(type === 'step' ? { reader: 'build123d-occt' } : {}),
              sha256: artifact.sha256,
              type,
            },
          ];
        }),
    );
    exportAudit = {
      artifacts: audited,
      errors: [],
      pass: true,
      schema: 'evidence-export-audit/v1',
    };
    const audit = await writeBound(
      `${name}_export-audit.json`,
      JSON.stringify(exportAudit),
    );
    artifacts.exportAudit = audit;
  }

  let sourcePath: string | undefined;
  const inputs: Record<string, unknown> = {
    intent: {
      ...intent,
      schema: 'evidence-cad-intent/v4',
    },
    profile: {
      ...profile,
      schema: 'evidence-bambu-printer-profile/v1',
    },
    scene: {
      ...scene,
      revision,
      schema: 'evidence-semantic-scene/v1',
    },
  };
  if (hybrid) {
    const geometry = await writeBound(`${name}-source.glb`, 'glTF-source');
    inputs.geometry = {
      [`${name}-source`]: { ...geometry, schema: 'mesh-source/v1' },
    };
  } else {
    const source = await writeBound(`${name}.py`, sourceContent);
    sourcePath = source.path;
    inputs.source = { ...source, schema: 'python-source/v1' };
  }

  const partTransforms = Object.fromEntries(parts.map((part) => [part, IDENTITY]));
  const layout = { auto_scale: false, scale: 1, transforms: partTransforms };
  const threeMfEvidence = { verified: true };
  const partReports = Object.fromEntries(
    parts.map((part, index) => {
      if (!hybrid) {
        return [
          part,
          {
            print: geometryRecord(),
            representationMaster: 'brep',
            semantic: geometryRecord(),
          },
        ];
      }
      const artifact = artifacts[`stl:${part}`]!;
      return [
        part,
        {
          appearance: {
            baseColor: index % 2 === 0 ? '#F05A35' : '#171717',
            metallic: 0,
            roughness: 0.58,
          },
          bodyCount: 1,
          boundsMm: geometryRecord().boundsMm,
          colorRegions: [],
          cutterNodeIds: [],
          isVolume: true,
          materialId: `material-${part}`,
          orientation: { name: 'identity' },
          path: artifact.path,
          positiveNodeIds: [`${part}-body`],
          print: geometryRecord(),
          printTransform: IDENTITY,
          representationMaster: 'mesh',
          semantic: geometryRecord(),
          sha256: artifact.sha256,
          triangles: 12,
          vertices: 8,
          volumeMm3: 1,
          volumeRemovedMm3: 0,
          watertight: true,
          windingConsistent: true,
        },
      ];
    }),
  );
  let backendData: Record<string, unknown>;
  const semanticAssembly = {
    boundsMm: geometryRecord().boundsMm,
    intentSha256: intent.sha256,
  };
  if (backend === 'brep-part') {
    backendData = {
      exportAudit,
      parameters: {},
      printOrientation: printOrientation(),
      semanticAssembly,
    };
  } else if (backend === 'brep-assembly') {
    backendData = {
      assembly: {
        maxOverlapMm3: 0.01,
        shape: rawBrepShape(parts.length),
      },
      exportAudit,
      overlapsMm3: overlapMap(parts),
      parameters: {},
      printPlate: { ...geometryRecord(parts.length), layout },
      semanticAssembly,
      ...(needsThreeMf
        ? {
            internalPartMeshes: {
              'plate-print': Object.fromEntries(
                parts.map((part) => [part, { path: `${part}-plate.stl` }]),
              ),
            },
            partColors: Object.fromEntries(
              parts.map((part, index) => [
                part,
                index % 2 === 0 ? '#F05A35' : '#171717',
              ]),
            ),
            printPackageMode: 'separate_parts',
            threeMf: threeMfEvidence,
          }
        : {}),
    };
  } else if (backend === 'brep-color-regions') {
    const region = `${parts[0]!}-region`;
    backendData = {
      assembly: { shape: rawBrepShape() },
      exportAudit,
      internalRegionMeshes: { [region]: { path: `${region}.stl` } },
      overlapsMm3: {},
      parameters: {},
      parentCoverage: { pass: true },
      printOrientation: printOrientation(),
      printPackageMode: 'co_print_body',
      printPlate: geometryRecord(),
      regions: { [region]: { color: '#F05A35' } },
      semanticAssembly,
      threeMf: threeMfEvidence,
    };
  } else {
    backendData = {
      assembly: { maxOverlapMm3: 0.01, overlapsMm3: overlapMap(parts) },
      printPackageMode: parts.length === 1 ? 'co_print_body' : 'separate_parts',
      printPlate: {
        boundsMm: geometryRecord(parts.length).boundsMm,
        layout,
        valid: true,
        volumeMm3: parts.length,
      },
      semanticAssembly,
      stepConsistency: hybridStepConsistency,
      threeMf: threeMfEvidence,
    };
  }
  const report: Record<string, unknown> = {
    artifactMatrix: {
      parts: Object.fromEntries(
        parts.map((part) => [
          part,
          {
            glb: 'required',
            step: hybrid ? 'not-applicable' : 'required',
            stl: 'required',
            threeMf: needsThreeMf ? 'required' : 'not-applicable',
          },
        ]),
      ),
    },
    artifacts,
    autoScale: false,
    backend,
    backendData,
    builtAt: new Date().toISOString(),
    coordinateFrames: {
      semantic: { scale: 1, units: 'mm', up: 'Z' },
      'part-print': { partTransforms, scale: 1, units: 'mm' },
      'plate-print': { partTransforms, scale: 1, units: 'mm' },
    },
    ...(hybrid
      ? {
          excludedDisplayNodes: [],
          excludedFromManufacturingNodes: [],
          fastenerGeometryChecks: {},
          fastenerGroups: {},
          includedDisplayNodes: [],
        }
      : { events: [] }),
    features: {},
    inputs,
    ...(['brep-assembly', 'brep-color-regions', 'hybrid-mesh'].includes(
      backend,
    )
      ? { materialPlan: inlineMaterialPlan ?? null }
      : {}),
    part: name,
    parts: partReports,
    pass,
    revision,
    runId: randomUUID(),
    scale: 1,
    schema,
    warnings: [],
  };
  const reportPath = join(root, `${name}_report.json`);
  await writeFile(reportPath, JSON.stringify(report));
  return {
    displayPath: display.path,
    report,
    reportPath,
    ...(sourcePath ? { sourcePath } : {}),
    stlPath: topStl,
    ...(threeMfPath ? { threeMfPath } : {}),
  };
}
