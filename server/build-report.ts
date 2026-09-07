import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import { dirname, extname, isAbsolute, relative, resolve, sep } from 'node:path';

import { isContainedRelativePath } from './path-safety.ts';

export const BUILD_REPORT_SCHEMA = 'evidence-a3d-build/v1';
export const BUILD_BACKENDS = new Set([
  'brep-assembly',
  'brep-color-regions',
  'brep-part',
  'hybrid-mesh',
]);

const SHA256 = /^[0-9a-f]{64}$/u;
const MAX_BOUND_JSON_BYTES = 2 * 1024 * 1024;
const SEMANTIC_ENVELOPE_TOLERANCE_MM = 0.5;
const SEMANTIC_RECORD_TOLERANCE_MM = 0.0002;
const INPUT_SCHEMAS = {
  intent: 'evidence-cad-intent/v5',
  profile: 'evidence-bambu-printer-profile/v1',
  scene: 'evidence-semantic-scene/v1',
} as const;

export interface BuildFileReference {
  coordinateFrame?: unknown;
  path?: unknown;
  sha256?: unknown;
  validator?: unknown;
  verified?: unknown;
}

interface ArtifactMatrixPart {
  glb?: unknown;
  step?: unknown;
  stl?: unknown;
  threeMf?: unknown;
}

interface BuildPart {
  isVolume?: unknown;
  print?: { valid?: unknown };
  representationMaster?: unknown;
  semantic?: { valid?: unknown; volumeMm3?: unknown };
  stepConsistency?: unknown;
  watertight?: unknown;
  windingConsistent?: unknown;
}

export interface UnifiedBuildReport {
  artifactMatrix?: { parts?: Record<string, ArtifactMatrixPart> };
  artifacts?: Record<string, BuildFileReference>;
  autoScale?: unknown;
  backend?: unknown;
  backendData?: unknown;
  builtAt?: unknown;
  coordinateFrames?: Record<string, unknown>;
  inputs?: Record<string, BuildFileReference & Record<string, unknown>>;
  materialPlan?: unknown;
  part?: unknown;
  parts?: Record<string, BuildPart>;
  pass?: unknown;
  revision?: unknown;
  runId?: unknown;
  scale?: unknown;
  schema?: unknown;
}

export interface ValidatedBuildReport {
  artifactPaths: Record<string, string>;
  inputPaths: Record<string, string>;
  report: UnifiedBuildReport;
  reportModifiedAtMs: number;
  reportPath: string;
}

export interface ValidateBuildOptions {
  expectedReportSha256?: string;
  maxBytes?: number;
  minimumArtifactModifiedAtMs?: number;
  minimumModifiedAtMs?: number;
}

function objectRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  return (
    JSON.stringify(Object.keys(value).sort()) ===
    JSON.stringify([...expected].sort())
  );
}

function validMaterialPlan(
  value: unknown,
  backend: string,
  buildPart: string,
  partIds: readonly string[],
): boolean {
  const plan = objectRecord(value);
  if (
    !plan ||
    !exactKeys(plan, [
      'archiveEncodes',
      'assignments',
      'coordinateFrame',
      'materials',
      'packageMode',
      'part',
      'scale',
      'schema',
      'sourceBindings',
    ]) ||
    plan.schema !== 'evidence-color-material-plan/v2' ||
    plan.part !== buildPart ||
    plan.coordinateFrame !== 'plate-print' ||
    plan.scale !== 1 ||
    JSON.stringify(plan.archiveEncodes) !==
      JSON.stringify(['part', 'region', 'rgb']) ||
    !['co_print_body', 'separate_parts'].includes(String(plan.packageMode))
  ) {
    return false;
  }
  if (
    (backend === 'brep-part' && plan.packageMode !== 'co_print_body') ||
    (backend === 'brep-color-regions' && plan.packageMode !== 'co_print_body') ||
    (backend === 'brep-assembly' && plan.packageMode !== 'separate_parts') ||
    (backend === 'hybrid-mesh' &&
      plan.packageMode !==
        (partIds.length === 1 ? 'co_print_body' : 'separate_parts'))
  ) {
    return false;
  }

  if (!Array.isArray(plan.materials) || plan.materials.length === 0) {
    return false;
  }
  const materials = new Map<string, Record<string, unknown>>();
  for (const rawMaterial of plan.materials) {
    const material = objectRecord(rawMaterial);
    if (
      !material ||
      !exactKeys(material, ['color', 'id', 'status']) ||
      !nonEmptyString(material.id) ||
      materials.has(material.id) ||
      typeof material.color !== 'string' ||
      !/^#[0-9A-F]{6}$/u.test(material.color) ||
      !['declared', 'proposed'].includes(String(material.status))
    ) {
      return false;
    }
    materials.set(material.id, material);
  }

  if (!Array.isArray(plan.assignments) || plan.assignments.length === 0) {
    return false;
  }
  const allowedScopes = new Set(
    backend === 'brep-color-regions'
      ? ['brep-region']
      : ['brep-part', 'brep-assembly'].includes(backend)
        ? ['whole-part']
        : ['volumetric-region', 'whole-part'],
  );
  const assignmentKeys = new Set<string>();
  const assignmentTargets = new Set<string>();
  const assignments: Record<string, unknown>[] = [];
  for (const rawAssignment of plan.assignments) {
    const assignment = objectRecord(rawAssignment);
    if (
      !assignment ||
      !exactKeys(assignment, ['materialId', 'part', 'region', 'scope']) ||
      !nonEmptyString(assignment.materialId) ||
      !materials.has(assignment.materialId) ||
      !nonEmptyString(assignment.part) ||
      !partIds.includes(assignment.part) ||
      !allowedScopes.has(String(assignment.scope)) ||
      (assignment.scope === 'whole-part'
        ? assignment.region !== null
        : !nonEmptyString(assignment.region))
    ) {
      return false;
    }
    const key = canonicalJson(assignment);
    const target = canonicalJson({
      part: assignment.part,
      region: assignment.region,
      scope: assignment.scope,
    });
    if (assignmentKeys.has(key) || assignmentTargets.has(target)) return false;
    assignmentKeys.add(key);
    assignmentTargets.add(target);
    assignments.push(assignment);
  }
  if (
    !partIds.every((partId) =>
      assignments.some((assignment) => assignment.part === partId),
    )
  ) {
    return false;
  }

  if (
    !Array.isArray(plan.sourceBindings) ||
    plan.sourceBindings.length !== assignments.length
  ) {
    return false;
  }
  const bindingKeys = new Set<string>();
  const intentSourceIds = new Set<string>();
  for (const rawBinding of plan.sourceBindings) {
    const binding = objectRecord(rawBinding);
    if (
      !binding ||
      !exactKeys(binding, [
        'color',
        'materialId',
        'materialStatus',
        'part',
        'region',
        'scope',
        'sourceId',
        'sourceKind',
      ]) ||
      !nonEmptyString(binding.materialId)
    ) {
      return false;
    }
    const material = materials.get(binding.materialId);
    if (
      !material ||
      binding.color !== material.color ||
      binding.materialStatus !== material.status ||
      ![
        'intent-color-region',
        'scene-part-appearance',
        'scene-part-material',
      ].includes(
        String(binding.sourceKind),
      ) ||
      !nonEmptyString(binding.sourceId) ||
      (binding.sourceKind === 'intent-color-region' &&
        binding.materialStatus !== 'declared') ||
      (['scene-part-appearance', 'scene-part-material'].includes(
        String(binding.sourceKind),
      ) &&
        (binding.materialStatus !== 'proposed' ||
          binding.scope !== 'whole-part' ||
          binding.region !== null)) ||
      !assignmentKeys.has(
        canonicalJson({
          materialId: binding.materialId,
          part: binding.part,
          region: binding.region,
          scope: binding.scope,
        }),
      )
    ) {
      return false;
    }
    const key = canonicalJson({
      materialId: binding.materialId,
      part: binding.part,
      region: binding.region,
      scope: binding.scope,
    });
    if (bindingKeys.has(key)) return false;
    bindingKeys.add(key);
    if (binding.sourceKind === 'intent-color-region') {
      if (intentSourceIds.has(binding.sourceId)) return false;
      intentSourceIds.add(binding.sourceId);
    }
  }
  return (
    bindingKeys.size === assignmentKeys.size &&
    [...bindingKeys].every((key) => assignmentKeys.has(key))
  );
}

function insideRoot(root: string, path: string): boolean {
  return isContainedRelativePath(relative(root, path));
}

interface StableBoundFileSnapshot {
  bytes?: Buffer;
  modifiedAtMs: number;
  sha256: string;
  size: number;
}

async function stableBoundFile(
  path: string,
  captureMaxBytes?: number,
): Promise<StableBoundFileSnapshot | undefined> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      !Number.isSafeInteger(before.size) ||
      (captureMaxBytes !== undefined && before.size > captureMaxBytes)
    ) {
      return undefined;
    }
    const hash = createHash('sha256');
    let bytes: Buffer | undefined;
    if (captureMaxBytes !== undefined) {
      bytes = await handle.readFile();
      if (bytes.length !== before.size) return undefined;
      hash.update(bytes);
    } else {
      const buffer = Buffer.allocUnsafe(1024 * 1024);
      let position = 0;
      while (position < before.size) {
        const { bytesRead } = await handle.read(
          buffer,
          0,
          Math.min(buffer.length, before.size - position),
          position,
        );
        if (bytesRead <= 0) return undefined;
        hash.update(buffer.subarray(0, bytesRead));
        position += bytesRead;
      }
    }
    const after = await handle.stat();
    const current = await lstat(path);
    if (
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      before.dev !== after.dev ||
      before.ino !== after.ino ||
      before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs ||
      before.ctimeMs !== after.ctimeMs ||
      before.dev !== current.dev ||
      before.ino !== current.ino ||
      before.size !== current.size ||
      before.mtimeMs !== current.mtimeMs ||
      before.ctimeMs !== current.ctimeMs
    ) {
      return undefined;
    }
    return {
      bytes,
      modifiedAtMs: before.mtimeMs,
      sha256: hash.digest('hex'),
      size: before.size,
    };
  } catch {
    return undefined;
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

interface ResolvedBoundFile {
  json?: unknown;
  modifiedAtMs: number;
  path: string;
}

async function readStableReport(
  path: string,
  maxBytes: number,
): Promise<
  | { bytes: Buffer; modifiedAtMs: number; sha256: string; size: number }
  | undefined
> {
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      before.size <= 0 ||
      before.size > maxBytes
    ) {
      return undefined;
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    const current = await lstat(path);
    if (
      bytes.length !== before.size ||
      current.isSymbolicLink() ||
      !current.isFile() ||
      current.nlink !== 1 ||
      before.dev !== after.dev ||
      before.ino !== after.ino ||
      before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs ||
      before.ctimeMs !== after.ctimeMs ||
      before.dev !== current.dev ||
      before.ino !== current.ino ||
      before.size !== current.size ||
      before.mtimeMs !== current.mtimeMs ||
      before.ctimeMs !== current.ctimeMs
    ) {
      return undefined;
    }
    return {
      bytes,
      modifiedAtMs: before.mtimeMs,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      size: before.size,
    };
  } catch {
    return undefined;
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function resolveBoundFile(
  workspaceRoot: string,
  reportPath: string,
  reference: BuildFileReference,
  options: { allowOutsideWorkspace?: boolean; parseJson?: boolean } = {},
): Promise<ResolvedBoundFile | undefined> {
  if (!nonEmptyString(reference.path) || !SHA256.test(String(reference.sha256))) {
    return undefined;
  }
  const candidate = isAbsolute(reference.path)
    ? reference.path
    : resolve(dirname(reportPath), reference.path);
  try {
    const metadata = await lstat(candidate);
    if (
      !metadata.isFile() ||
      metadata.isSymbolicLink() ||
      metadata.nlink !== 1
    ) {
      return undefined;
    }
    const canonical = await realpath(candidate);
    if (
      !options.allowOutsideWorkspace &&
      !insideRoot(workspaceRoot, canonical)
    ) {
      return undefined;
    }
    const snapshot = await stableBoundFile(
      canonical,
      options.parseJson ? MAX_BOUND_JSON_BYTES : undefined,
    );
    if (!snapshot || snapshot.sha256 !== reference.sha256) return undefined;
    return {
      json:
        options.parseJson && snapshot.bytes
          ? JSON.parse(snapshot.bytes.toString('utf8'))
          : undefined,
      modifiedAtMs: snapshot.modifiedAtMs,
      path: canonical,
    };
  } catch {
    return undefined;
  }
}

function rigidTransform(value: unknown): boolean {
  if (
    !Array.isArray(value) ||
    value.length !== 4 ||
    !value.every(
      (row) =>
        Array.isArray(row) &&
        row.length === 4 &&
        row.every(
          (item) => typeof item === 'number' && Number.isFinite(item),
        ),
    )
  ) {
    return false;
  }
  const matrix = value as number[][];
  if (
    matrix[3]?.some(
      (item, index) => Math.abs(item - [0, 0, 0, 1][index]!) > 1e-9,
    )
  ) {
    return false;
  }
  const columns = [0, 1, 2].map((column) =>
    [0, 1, 2].map((row) => matrix[row]![column]!),
  );
  for (let left = 0; left < 3; left += 1) {
    for (let right = left; right < 3; right += 1) {
      const dot = columns[left]!.reduce(
        (sum, item, index) => sum + item * columns[right]![index]!,
        0,
      );
      const expected = left === right ? 1 : 0;
      if (Math.abs(dot - expected) > 1e-7) return false;
    }
  }
  const [a, b, c] = columns;
  const determinant =
    a![0]! * (b![1]! * c![2]! - b![2]! * c![1]!) -
    b![0]! * (a![1]! * c![2]! - a![2]! * c![1]!) +
    c![0]! * (a![1]! * b![2]! - a![2]! * b![1]!);
  return Math.abs(determinant - 1) <= 1e-7;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function validBounds(value: unknown): boolean {
  const bounds = objectRecord(value);
  if (
    !bounds ||
    !exactKeys(bounds, ['max', 'min', 'size']) ||
    !finiteVector3(bounds.min) ||
    !finiteVector3(bounds.max) ||
    !finiteVector3(bounds.size) ||
    bounds.size.some((item) => item <= 0)
  ) {
    return false;
  }
  const minimum = bounds.min as number[];
  const maximum = bounds.max as number[];
  const size = bounds.size as number[];
  return size.every((item, index) => {
    const observed = maximum[index]! - minimum[index]!;
    return Math.abs(observed - item) <= Math.max(0.0002, Math.abs(item) * 1e-7);
  });
}

function validGeometryRecord(value: unknown): boolean {
  const geometry = objectRecord(value);
  return Boolean(
    geometry &&
      exactKeys(geometry, [
        'bodyCount',
        'boundsMm',
        'isVolume',
        'valid',
        'volumeMm3',
      ]) &&
      Number.isInteger(geometry.bodyCount) &&
      Number(geometry.bodyCount) >= 1 &&
      validBounds(geometry.boundsMm) &&
      geometry.isVolume === true &&
      geometry.valid === true &&
      finiteNumber(geometry.volumeMm3) &&
      geometry.volumeMm3 > 0,
  );
}

function semanticPartUnionBounds(
  parts: Record<string, BuildPart> | undefined,
): { max: number[]; min: number[]; size: number[] } | undefined {
  if (!parts || Object.keys(parts).length === 0) return undefined;
  const bounds = Object.values(parts).map((part) =>
    objectRecord(objectRecord(part.semantic)?.boundsMm),
  );
  if (bounds.some((item) => !item || !validBounds(item))) return undefined;
  const minimum = [0, 1, 2].map((axis) =>
    Math.min(...bounds.map((item) => Number((item!.min as number[])[axis]))),
  );
  const maximum = [0, 1, 2].map((axis) =>
    Math.max(...bounds.map((item) => Number((item!.max as number[])[axis]))),
  );
  return {
    max: maximum,
    min: minimum,
    size: maximum.map((item, axis) => item - minimum[axis]!),
  };
}

function boundsMatch(left: unknown, right: unknown, toleranceMm: number): boolean {
  const leftBounds = objectRecord(left);
  const rightBounds = objectRecord(right);
  if (!leftBounds || !rightBounds || !validBounds(leftBounds) || !validBounds(rightBounds)) {
    return false;
  }
  return (['min', 'max', 'size'] as const).every((field) =>
    (leftBounds[field] as number[]).every(
      (item, axis) =>
        Math.abs(item - (rightBounds[field] as number[])[axis]!) <= toleranceMm,
    ),
  );
}

function validSemanticAssembly(report: UnifiedBuildReport, value: unknown): boolean {
  const record = objectRecord(value);
  const intent = objectRecord(report.inputs?.intent);
  const expected = semanticPartUnionBounds(report.parts);
  return Boolean(
    record &&
      exactKeys(record, ['boundsMm', 'intentSha256']) &&
      SHA256.test(String(record.intentSha256)) &&
      record.intentSha256 === intent?.sha256 &&
      expected &&
      boundsMatch(record.boundsMm, expected, SEMANTIC_RECORD_TOLERANCE_MM),
  );
}

function validRawBrepShape(value: unknown): boolean {
  const shape = objectRecord(value);
  return Boolean(
    shape &&
      exactKeys(shape, ['bbox_mm', 'solid_count', 'valid', 'volume_mm3']) &&
      Number.isInteger(shape.solid_count) &&
      Number(shape.solid_count) >= 1 &&
      validBounds(shape.bbox_mm) &&
      shape.valid === true &&
      finiteNumber(shape.volume_mm3) &&
      shape.volume_mm3 > 0,
  );
}

function validOrientation(value: unknown): boolean {
  const orientation = objectRecord(value);
  return Boolean(
    orientation &&
      exactKeys(orientation, ['candidates', 'selected', 'strategy']) &&
      Array.isArray(orientation.candidates) &&
      orientation.candidates.length > 0 &&
      objectRecord(orientation.selected) &&
      Object.keys(objectRecord(orientation.selected)!).length > 0 &&
      nonEmptyString(orientation.strategy),
  );
}

function validParameters(value: unknown): boolean {
  const parameters = objectRecord(value);
  if (!parameters) return false;
  const required = [
    'affects',
    'default',
    'group',
    'label',
    'maximum',
    'minimum',
    'step',
    'unit',
    'value',
  ];
  for (const [parameterId, rawDescriptor] of Object.entries(parameters)) {
    const descriptor = objectRecord(rawDescriptor);
    if (!/^[a-z][a-z0-9_-]*$/u.test(parameterId) || !descriptor) return false;
    const expected = [
      ...required,
      ...('group_zh' in descriptor ? ['group_zh'] : []),
      ...('label_zh' in descriptor ? ['label_zh'] : []),
    ];
    if (
      !exactKeys(descriptor, expected) ||
      !Array.isArray(descriptor.affects) ||
      !descriptor.affects.every(nonEmptyString) ||
      !nonEmptyString(descriptor.label) ||
      (descriptor.group !== null && !nonEmptyString(descriptor.group)) ||
      (descriptor.unit !== null && !nonEmptyString(descriptor.unit)) ||
      ('group_zh' in descriptor && !nonEmptyString(descriptor.group_zh)) ||
      ('label_zh' in descriptor && !nonEmptyString(descriptor.label_zh))
    ) {
      return false;
    }
    const minimum = descriptor.minimum;
    const maximum = descriptor.maximum;
    const initial = descriptor.default;
    const step = descriptor.step;
    const current = descriptor.value;
    if (
      !finiteNumber(minimum) ||
      !finiteNumber(maximum) ||
      !finiteNumber(initial) ||
      !finiteNumber(step) ||
      !finiteNumber(current) ||
      minimum > maximum ||
      step <= 0 ||
      initial < minimum ||
      initial > maximum ||
      current < minimum ||
      current > maximum ||
      Math.abs((current - minimum) / step - Math.round((current - minimum) / step)) >
        1e-8
    ) {
      return false;
    }
  }
  return true;
}

function stringList(value: unknown, allowEmpty: boolean): boolean {
  return (
    Array.isArray(value) &&
    (allowEmpty || value.length > 0) &&
    value.every(nonEmptyString)
  );
}

function validHybridPart(part: Record<string, unknown>): boolean {
  const master = part.representationMaster;
  const expected = [
    'appearance',
    'bodyCount',
    'boundsMm',
    'colorRegions',
    'cutterNodeIds',
    'isVolume',
    'materialId',
    'orientation',
    'path',
    'positiveNodeIds',
    'print',
    'printTransform',
    'representationMaster',
    'sha256',
    'semantic',
    'triangles',
    'vertices',
    'volumeMm3',
    'volumeRemovedMm3',
    'watertight',
    'windingConsistent',
    ...(master === 'brep' ? ['masterStep', 'stepConsistency'] : []),
  ];
  const appearance = objectRecord(part.appearance);
  if (
    !['brep', 'mesh'].includes(String(master)) ||
    !exactKeys(part, expected) ||
    !validGeometryRecord(part.print) ||
    !validGeometryRecord(part.semantic) ||
    !validBounds(part.boundsMm) ||
    !Number.isInteger(part.bodyCount) ||
    Number(part.bodyCount) < 1 ||
    !Number.isInteger(part.triangles) ||
    Number(part.triangles) < 4 ||
    !Number.isInteger(part.vertices) ||
    Number(part.vertices) < 4 ||
    part.isVolume !== true ||
    part.watertight !== true ||
    part.windingConsistent !== true ||
    !finiteNumber(part.volumeMm3) ||
    part.volumeMm3 <= 0 ||
    !finiteNumber(part.volumeRemovedMm3) ||
    part.volumeRemovedMm3 < 0 ||
    !nonEmptyString(part.path) ||
    !nonEmptyString(part.materialId) ||
    !SHA256.test(String(part.sha256)) ||
    !rigidTransform(part.printTransform) ||
    !appearance ||
    !exactKeys(appearance, ['baseColor', 'metallic', 'roughness']) ||
    !/^#[0-9A-F]{6}$/u.test(String(appearance.baseColor)) ||
    !finiteNumber(appearance.metallic) ||
    appearance.metallic < 0 ||
    appearance.metallic > 1 ||
    !finiteNumber(appearance.roughness) ||
    appearance.roughness < 0 ||
    appearance.roughness > 1 ||
    !stringList(part.cutterNodeIds, true) ||
    !stringList(part.positiveNodeIds, false) ||
    !objectRecord(part.orientation) ||
    Object.keys(objectRecord(part.orientation)!).length === 0 ||
    !Array.isArray(part.colorRegions)
  ) {
    return false;
  }
  const regionIds = new Set<string>();
  for (const rawRegion of part.colorRegions) {
    const region = objectRecord(rawRegion);
    if (
      !region ||
      !exactKeys(region, [
        'bodyCount',
        'id',
        'isVolume',
        'materialId',
        'sourceMesh',
        'valid',
      ]) ||
      !nonEmptyString(region.id) ||
      regionIds.has(region.id) ||
      !nonEmptyString(region.materialId) ||
      !Number.isInteger(region.bodyCount) ||
      Number(region.bodyCount) < 1 ||
      region.isVolume !== true ||
      region.valid !== true ||
      !objectRecord(region.sourceMesh)
    ) {
      return false;
    }
    regionIds.add(region.id);
  }
  if (master !== 'brep') return true;
  const masterStep = objectRecord(part.masterStep);
  const stepConsistency = objectRecord(part.stepConsistency);
  return Boolean(
    masterStep &&
      exactKeys(masterStep, [
        'coordinateFrame',
        'path',
        'role',
        'sha256',
        'solidCount',
        'validator',
        'verifiedBrep',
      ]) &&
      masterStep.coordinateFrame === 'semantic' &&
      masterStep.role === 'brep-master' &&
      masterStep.solidCount === 1 &&
      masterStep.validator === 'build123d-occt' &&
      masterStep.verifiedBrep === true &&
      stepConsistency &&
      exactKeys(stepConsistency, ['comparison', 'pass', 'step']) &&
      stepConsistency.pass === true &&
      canonicalJson(stepConsistency.step) === canonicalJson(masterStep),
  );
}

function validPartRecord(value: unknown, backend: string): boolean {
  const part = objectRecord(value);
  if (!part) return false;
  if (backend === 'hybrid-mesh') return validHybridPart(part);
  return (
    ['brep-part', 'brep-assembly', 'brep-color-regions'].includes(backend) &&
    exactKeys(part, ['print', 'representationMaster', 'semantic']) &&
    part.representationMaster === 'brep' &&
    validGeometryRecord(part.print) &&
    validGeometryRecord(part.semantic)
  );
}

function validOverlapMap(value: unknown, partIds: readonly string[]): boolean {
  const overlaps = objectRecord(value);
  if (!overlaps) return false;
  const expected: string[] = [];
  const sorted = [...partIds].sort();
  for (let index = 0; index < sorted.length; index += 1) {
    for (const right of sorted.slice(index + 1)) {
      expected.push(`${sorted[index]!}&${right}`);
    }
  }
  return (
    exactKeys(overlaps, expected) &&
    Object.values(overlaps).every(
      (item) => finiteNumber(item) && item >= 0,
    )
  );
}

function validBackendData(
  report: UnifiedBuildReport,
  backend: string,
  partIds: readonly string[],
  requiresThreeMf: boolean,
): boolean {
  const data = objectRecord(report.backendData);
  if (!data) return false;
  if (!validSemanticAssembly(report, data.semanticAssembly)) return false;
  const brepCommon = () =>
    Boolean(objectRecord(data.exportAudit) && validParameters(data.parameters));
  if (backend === 'brep-part') {
    const expected = [
      'exportAudit',
      'parameters',
      'printOrientation',
      'semanticAssembly',
      ...(requiresThreeMf
        ? ['partColors', 'printPackageMode', 'threeMf']
        : []),
    ];
    const partColors = objectRecord(data.partColors);
    return Boolean(
      exactKeys(data, expected) &&
        brepCommon() &&
        validOrientation(data.printOrientation) &&
        (!requiresThreeMf ||
          (data.printPackageMode === 'co_print_body' &&
            partColors &&
            exactKeys(partColors, partIds) &&
            Object.values(partColors).every(
              (color) =>
                typeof color === 'string' && /^#[0-9A-F]{6}$/u.test(color),
            ) &&
            objectRecord(data.threeMf))),
    );
  }
  if (backend === 'brep-assembly') {
    const expected = [
      'assembly',
      'exportAudit',
      'overlapsMm3',
      'parameters',
      'printPlate',
      'semanticAssembly',
      ...(requiresThreeMf
        ? ['internalPartMeshes', 'partColors', 'printPackageMode', 'threeMf']
        : []),
    ];
    const assembly = objectRecord(data.assembly);
    const printPlate = objectRecord(data.printPlate);
    if (
      !exactKeys(data, expected) ||
      !brepCommon() ||
      !assembly ||
      !exactKeys(assembly, ['maxOverlapMm3', 'shape']) ||
      !finiteNumber(assembly.maxOverlapMm3) ||
      assembly.maxOverlapMm3 < 0 ||
      !validRawBrepShape(assembly.shape) ||
      !validOverlapMap(data.overlapsMm3, partIds) ||
      !printPlate ||
      !exactKeys(printPlate, [
        'bodyCount',
        'boundsMm',
        'isVolume',
        'layout',
        'valid',
        'volumeMm3',
      ]) ||
      !validGeometryRecord({
        bodyCount: printPlate.bodyCount,
        boundsMm: printPlate.boundsMm,
        isVolume: printPlate.isVolume,
        valid: printPlate.valid,
        volumeMm3: printPlate.volumeMm3,
      }) ||
      !objectRecord(printPlate.layout)
    ) {
      return false;
    }
    if (!requiresThreeMf) return true;
    const partColors = objectRecord(data.partColors);
    const internalMeshes = objectRecord(data.internalPartMeshes);
    const plateMeshes = objectRecord(internalMeshes?.['plate-print']);
    return Boolean(
      data.printPackageMode === 'separate_parts' &&
        partColors &&
        exactKeys(partColors, partIds) &&
        Object.values(partColors).every(
          (color) => typeof color === 'string' && /^#[0-9A-F]{6}$/u.test(color),
        ) &&
        internalMeshes &&
        exactKeys(internalMeshes, ['plate-print']) &&
        plateMeshes &&
        exactKeys(plateMeshes, partIds) &&
        objectRecord(data.threeMf),
    );
  }
  if (backend === 'brep-color-regions') {
    const assembly = objectRecord(data.assembly);
    return Boolean(
      exactKeys(data, [
        'assembly',
        'exportAudit',
        'internalRegionMeshes',
        'overlapsMm3',
        'parameters',
        'parentCoverage',
        'printOrientation',
        'printPackageMode',
        'printPlate',
        'regions',
        'semanticAssembly',
        'threeMf',
      ]) &&
        brepCommon() &&
        data.printPackageMode === 'co_print_body' &&
        validOrientation(data.printOrientation) &&
        assembly &&
        exactKeys(assembly, ['shape']) &&
        validRawBrepShape(assembly.shape) &&
        validGeometryRecord(data.printPlate) &&
        objectRecord(data.internalRegionMeshes) &&
        objectRecord(data.overlapsMm3) &&
        objectRecord(data.parentCoverage) &&
        objectRecord(data.regions) &&
        objectRecord(data.threeMf),
    );
  }
  if (backend === 'hybrid-mesh') {
    const assembly = objectRecord(data.assembly);
    const printPlate = objectRecord(data.printPlate);
    const stepConsistency = objectRecord(data.stepConsistency);
    return Boolean(
      exactKeys(data, [
        'assembly',
        'printPackageMode',
        'printPlate',
        'semanticAssembly',
        'stepConsistency',
        'threeMf',
      ]) &&
        data.printPackageMode ===
          (partIds.length === 1 ? 'co_print_body' : 'separate_parts') &&
        assembly &&
        exactKeys(assembly, ['maxOverlapMm3', 'overlapsMm3']) &&
        finiteNumber(assembly.maxOverlapMm3) &&
        assembly.maxOverlapMm3 >= 0 &&
        validOverlapMap(assembly.overlapsMm3, partIds) &&
        printPlate &&
        exactKeys(printPlate, ['boundsMm', 'layout', 'valid', 'volumeMm3']) &&
        validBounds(printPlate.boundsMm) &&
        printPlate.valid === true &&
        finiteNumber(printPlate.volumeMm3) &&
        printPlate.volumeMm3 > 0 &&
        objectRecord(printPlate.layout) &&
        stepConsistency &&
        exactKeys(stepConsistency, ['parts', 'pass', 'revision', 'schema']) &&
        stepConsistency.schema === 'evidence-step-consistency/v1' &&
        stepConsistency.pass === true &&
        stepConsistency.revision === report.revision &&
        objectRecord(stepConsistency.parts) &&
        objectRecord(data.threeMf),
    );
  }
  return false;
}

function expectedArtifactFrame(key: string): string | undefined {
  if (key === 'glb:display' || key.startsWith('step:')) return 'semantic';
  if (key === '3mf' || key === 'stl' || key.startsWith('plate-stl:')) {
    return 'plate-print';
  }
  if (key.startsWith('stl:')) return 'part-print';
  if (/^region:.+:semantic$/u.test(key)) return 'semantic';
  if (/^region:.+:print$/u.test(key)) return 'part-print';
  return undefined;
}

function artifactSuffixMatches(key: string, path: unknown): boolean {
  if (!nonEmptyString(path)) return false;
  const suffix = extname(path).toLowerCase();
  if (key === 'glb:display') return suffix === '.glb';
  if (key === '3mf') return suffix === '.3mf';
  if (
    key === 'stl' ||
    key.startsWith('stl:') ||
    key.startsWith('plate-stl:') ||
    /^region:.+:(?:print|semantic)$/u.test(key)
  ) {
    return suffix === '.stl';
  }
  if (key.startsWith('step:')) return suffix === '.step' || suffix === '.stp';
  return [
    'boundScene',
    'exportAudit',
    'materialPlan',
    'shapeConsistency',
    'stepConsistency',
  ].includes(key) && suffix === '.json';
}

function expectedArtifactKeys(
  backend: string,
  partIds: readonly string[],
  parts: Record<string, unknown>,
  artifacts: Record<string, unknown>,
  requiresThreeMf: boolean,
): Set<string> | undefined {
  const expected = new Set(['glb:display']);
  for (const partId of partIds) {
    expected.add(`stl:${partId}`);
  }
  if (backend === 'brep-part') {
    expected.add(`step:${partIds[0]}`);
  } else if (backend === 'brep-color-regions') {
    expected.add(`step:${partIds[0]}`);
    const regionFrames = new Map<string, Set<string>>();
    for (const key of Object.keys(artifacts)) {
      const match = /^region:(.+):(print|semantic)$/u.exec(key);
      if (!match) continue;
      const frames = regionFrames.get(match[1]!) ?? new Set<string>();
      frames.add(match[2]!);
      regionFrames.set(match[1]!, frames);
      expected.add(key);
    }
    if (
      regionFrames.size === 0 ||
      [...regionFrames.values()].some(
        (frames) =>
          frames.size !== 2 ||
          !frames.has('print') ||
          !frames.has('semantic'),
      )
    ) {
      return undefined;
    }
  } else if (backend === 'brep-assembly') {
    expected.add('stl');
    expected.add('step:assembly');
    for (const partId of partIds) {
      expected.add(`step:${partId}`);
      if (requiresThreeMf) expected.add(`plate-stl:${partId}`);
    }
  } else if (backend === 'hybrid-mesh') {
    expected.add('stl');
    expected.add('boundScene');
    expected.add('shapeConsistency');
    expected.add('stepConsistency');
    for (const partId of partIds) {
      if (objectRecord(parts[partId])?.representationMaster === 'brep') {
        expected.add(`step:${partId}`);
      }
    }
  }
  if (backend.startsWith('brep-')) {
    expected.add('exportAudit');
  }
  if (requiresThreeMf) {
    expected.add('3mf');
    expected.add('materialPlan');
  }
  return expected;
}

function expectedTopLevelKeys(
  backend: string,
  requiresThreeMf: boolean,
): string[] | undefined {
  if (!BUILD_BACKENDS.has(backend)) return undefined;
  const expected = new Set([
    'artifactMatrix',
    'artifacts',
    'autoScale',
    'backend',
    'backendData',
    'builtAt',
    'coordinateFrames',
    'features',
    'inputs',
    'part',
    'parts',
    'pass',
    'revision',
    'runId',
    'scale',
    'schema',
    'warnings',
  ]);
  if (backend.startsWith('brep-')) expected.add('events');
  if (
    ['brep-assembly', 'brep-color-regions', 'hybrid-mesh'].includes(backend) ||
    (backend === 'brep-part' && requiresThreeMf)
  ) {
    expected.add('materialPlan');
  }
  if (backend === 'hybrid-mesh') {
    for (const key of [
      'excludedDisplayNodes',
      'excludedFromManufacturingNodes',
      'fastenerGeometryChecks',
      'fastenerGroups',
      'includedDisplayNodes',
    ]) {
      expected.add(key);
    }
  }
  return [...expected];
}

function validStructure(report: UnifiedBuildReport): boolean {
  const backend = String(report.backend);
  const matrixParts = objectRecord(report.artifactMatrix?.parts);
  const declaresThreeMf = Boolean(
    matrixParts &&
      Object.values(matrixParts).some(
        (value) => objectRecord(value)?.threeMf === 'required',
      ),
  );
  const topLevelKeys = expectedTopLevelKeys(backend, declaresThreeMf);
  const reportRecord = objectRecord(report);
  if (
    !topLevelKeys ||
    !reportRecord ||
    !exactKeys(reportRecord, topLevelKeys) ||
    report.schema !== BUILD_REPORT_SCHEMA ||
    !BUILD_BACKENDS.has(backend) ||
    report.pass !== true ||
    report.scale !== 1 ||
    report.autoScale !== false ||
    !nonEmptyString(report.part) ||
    !nonEmptyString(report.revision) ||
    !nonEmptyString(report.runId) ||
    !nonEmptyString(report.builtAt)
  ) {
    return false;
  }
  const parts = objectRecord(report.parts);
  const artifacts = objectRecord(report.artifacts);
  const inputs = objectRecord(report.inputs);
  if (!parts || !matrixParts || !artifacts || !inputs) return false;
  const partIds = Object.keys(parts).sort();
  if (
    partIds.length === 0 ||
    JSON.stringify(partIds) !== JSON.stringify(Object.keys(matrixParts).sort())
  ) {
    return false;
  }
  const inputNames = Object.keys(inputs).sort();
  const expectedInputs = [
    'intent',
    'profile',
    'scene',
    ...(backend.startsWith('brep-') ? ['source'] : []),
    ...(backend === 'hybrid-mesh' ? ['geometry'] : []),
  ].sort();
  if (JSON.stringify(inputNames) !== JSON.stringify(expectedInputs)) return false;
  for (const [name, schema] of Object.entries(INPUT_SCHEMAS)) {
    const input = objectRecord(inputs[name]);
    if (
      !input ||
      input.schema !== schema ||
      !nonEmptyString(input.path) ||
      extname(input.path).toLowerCase() !== '.json'
    ) {
      return false;
    }
  }
  if (objectRecord(inputs.scene)?.revision !== report.revision) return false;
  if (
    backend.startsWith('brep-') &&
    (objectRecord(inputs.source)?.schema !== 'python-source/v1' ||
      !nonEmptyString(objectRecord(inputs.source)?.path) ||
      extname(String(objectRecord(inputs.source)?.path)).toLowerCase() !== '.py')
  ) {
    return false;
  }
  if (backend === 'hybrid-mesh') {
    const geometry = objectRecord(inputs.geometry);
    if (!geometry || Object.keys(geometry).length === 0) return false;
    for (const reference of Object.values(geometry)) {
      const record = objectRecord(reference);
      if (
        !record ||
        ![
          'brep-tessellation/v1',
          'display-mesh-source/v1',
          'mesh-source/v1',
        ].includes(String(record.schema)) ||
        !nonEmptyString(record.path) ||
        !['.glb', '.gltf', '.obj', '.ply', '.stl'].includes(
          extname(record.path).toLowerCase(),
        ) ||
        !SHA256.test(String(record.sha256))
      ) {
        return false;
      }
    }
  }

  const masters = new Set<string>();
  const threeMfStatuses = new Set<string>();
  for (const partId of partIds) {
    const part = parts[partId] as BuildPart;
    const requirements = objectRecord(matrixParts[partId]);
    if (!validPartRecord(part, backend) || !requirements) return false;
    masters.add(String(part.representationMaster));
    if (
      JSON.stringify(Object.keys(requirements).sort()) !==
        JSON.stringify(['glb', 'step', 'stl', 'threeMf']) ||
      requirements.glb !== 'required' ||
      requirements.stl !== 'required' ||
      !['required', 'not-applicable'].includes(String(requirements.threeMf))
    ) {
      return false;
    }
    const expectedStep =
      part.representationMaster === 'brep' ? 'required' : 'not-applicable';
    if (requirements.step !== expectedStep) return false;
    threeMfStatuses.add(String(requirements.threeMf));
    if (!objectRecord(artifacts[`stl:${partId}`])) return false;
    if (
      (part.representationMaster === 'brep') !==
      Boolean(objectRecord(artifacts[`step:${partId}`]))
    ) {
      return false;
    }
  }
  if (backend === 'brep-part' && (partIds.length !== 1 || masters.size !== 1 || !masters.has('brep'))) {
    return false;
  }
  if (
    backend === 'brep-color-regions' &&
    (partIds.length !== 1 || masters.size !== 1 || !masters.has('brep'))
  ) {
    return false;
  }
  if (backend === 'brep-assembly' && (partIds.length < 2 || masters.size !== 1 || !masters.has('brep'))) {
    return false;
  }
  if (backend === 'hybrid-mesh' && !masters.has('mesh')) return false;
  if (
    ['brep-color-regions', 'hybrid-mesh'].includes(backend) &&
    (threeMfStatuses.size !== 1 || !threeMfStatuses.has('required'))
  ) {
    return false;
  }
  if (backend === 'brep-assembly' && threeMfStatuses.size !== 1) return false;

  const requiresThreeMf = threeMfStatuses.has('required');
  if (!validBackendData(report, backend, partIds, requiresThreeMf)) return false;
  if (
    !objectRecord(artifacts['glb:display']) ||
    (requiresThreeMf &&
      (!objectRecord(artifacts['3mf']) ||
        !objectRecord(artifacts.materialPlan) ||
        !objectRecord(report.materialPlan))) ||
    (!requiresThreeMf && (artifacts['3mf'] !== undefined || artifacts.materialPlan !== undefined))
  ) {
    return false;
  }
  if (requiresThreeMf) {
    const threeMf = objectRecord(artifacts['3mf']);
    if (
      threeMf?.verified !== true ||
      threeMf.validator !== 'lib3mf' ||
      !validMaterialPlan(report.materialPlan, backend, String(report.part), partIds)
    ) {
      return false;
    }
  }
  if (['brep-assembly', 'hybrid-mesh'].includes(backend) && !objectRecord(artifacts.stl)) {
    return false;
  }
  if (!['brep-assembly', 'hybrid-mesh'].includes(backend) && artifacts.stl !== undefined) {
    return false;
  }
  const expectedKeys = expectedArtifactKeys(
    backend,
    partIds,
    parts,
    artifacts,
    requiresThreeMf,
  );
  if (
    !expectedKeys ||
    JSON.stringify([...expectedKeys].sort()) !==
      JSON.stringify(Object.keys(artifacts).sort())
  ) {
    return false;
  }
  if (backend === 'brep-color-regions') {
    const assignedRegions = new Set(
      (objectRecord(report.materialPlan)?.assignments as unknown[] | undefined)
        ?.map((assignment) => objectRecord(assignment)?.region)
        .filter(nonEmptyString) ?? [],
    );
    const artifactRegions = new Set(
      Object.keys(artifacts)
        .map((key) => /^region:(.+):semantic$/u.exec(key)?.[1])
        .filter(nonEmptyString),
    );
    if (
      JSON.stringify([...assignedRegions].sort()) !==
      JSON.stringify([...artifactRegions].sort())
    ) {
      return false;
    }
  }
  for (const [key, value] of Object.entries(artifacts)) {
    const artifact = objectRecord(value);
    if (
      !artifact ||
      !nonEmptyString(artifact.path) ||
      !SHA256.test(String(artifact.sha256)) ||
      !artifactSuffixMatches(key, artifact.path)
    ) {
      return false;
    }
    const expectedFrame = expectedArtifactFrame(key);
    if (expectedFrame && artifact.coordinateFrame !== expectedFrame) return false;
    if (key.startsWith('step:')) {
      const suffix = key.slice('step:'.length);
      if (!partIds.includes(suffix) && !(backend === 'brep-assembly' && suffix === 'assembly')) {
        return false;
      }
    }
  }

  const frames = objectRecord(report.coordinateFrames);
  if (!frames) return false;
  for (const name of ['part-print', 'plate-print']) {
    const frame = objectRecord(frames[name]);
    const transforms = objectRecord(frame?.partTransforms);
    if (
      !transforms ||
      JSON.stringify(Object.keys(transforms).sort()) !== JSON.stringify(partIds) ||
      !Object.values(transforms).every(rigidTransform)
    ) {
      return false;
    }
  }
  return true;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const object = objectRecord(value);
  if (object) {
    return `{${Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function finiteVector3(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every(
      (item) => typeof item === 'number' && Number.isFinite(item),
    )
  );
}

function geometryAuditRecord(
  value: unknown,
  observed: boolean,
  type: 'step' | 'stl',
): Record<string, unknown> | undefined {
  const record = objectRecord(value);
  const bounds = objectRecord(record?.boundsMm);
  if (
    !record ||
    !bounds ||
    !exactKeys(bounds, ['max', 'min', 'size']) ||
    !finiteVector3(bounds.min) ||
    !finiteVector3(bounds.max) ||
    !finiteVector3(bounds.size) ||
    bounds.size.some((item) => item <= 0) ||
    !Number.isInteger(record.bodyCount) ||
    Number(record.bodyCount) < 1 ||
    record.valid !== true ||
    typeof record.volumeMm3 !== 'number' ||
    !Number.isFinite(record.volumeMm3) ||
    record.volumeMm3 <= 0
  ) {
    return undefined;
  }
  const expectedKeys =
    observed && type === 'stl'
      ? [
          'bodyCount',
          'boundsMm',
          'faceCount',
          'valid',
          'vertexCount',
          'volumeMm3',
          'watertight',
          'windingConsistent',
        ]
      : ['bodyCount', 'boundsMm', 'valid', 'volumeMm3'];
  if (!exactKeys(record, expectedKeys)) return undefined;
  if (
    observed &&
    type === 'stl' &&
    (!Number.isInteger(record.faceCount) ||
      Number(record.faceCount) < 4 ||
      !Number.isInteger(record.vertexCount) ||
      Number(record.vertexCount) < 4 ||
      record.watertight !== true ||
      record.windingConsistent !== true)
  ) {
    return undefined;
  }
  return record;
}

function auditedGeometryMatches(
  expected: Record<string, unknown>,
  observed: Record<string, unknown>,
): boolean {
  if (expected.bodyCount !== observed.bodyCount) return false;
  const expectedBounds = objectRecord(expected.boundsMm)!;
  const observedBounds = objectRecord(observed.boundsMm)!;
  for (const field of ['min', 'max', 'size'] as const) {
    const wanted = expectedBounds[field] as number[];
    const actual = observedBounds[field] as number[];
    if (actual.some((item, index) => Math.abs(item - wanted[index]!) > 0.05)) {
      return false;
    }
  }
  const expectedVolume = Number(expected.volumeMm3);
  return (
    Math.abs(Number(observed.volumeMm3) - expectedVolume) <=
    Math.max(0.05, Math.abs(expectedVolume) * 0.005)
  );
}

async function validateBrepExportAudit(
  report: UnifiedBuildReport,
  artifactPaths: Record<string, string>,
  artifactJson: Record<string, unknown>,
): Promise<boolean> {
  if (!String(report.backend).startsWith('brep-')) return true;
  const inlineAudit = objectRecord(objectRecord(report.backendData)?.exportAudit);
  const fileAudit = objectRecord(artifactJson.exportAudit);
  if (
    !inlineAudit ||
    !fileAudit ||
    canonicalJson(inlineAudit) !== canonicalJson(fileAudit) ||
    !exactKeys(fileAudit, ['artifacts', 'errors', 'pass', 'schema']) ||
    fileAudit.schema !== 'evidence-export-audit/v1' ||
    fileAudit.pass !== true ||
    !Array.isArray(fileAudit.errors) ||
    fileAudit.errors.length !== 0
  ) {
    return false;
  }
  const auditedArtifacts = objectRecord(fileAudit.artifacts);
  const reportArtifacts = objectRecord(report.artifacts);
  if (!auditedArtifacts || !reportArtifacts) return false;
  const ownedKeys = Object.keys(reportArtifacts)
    .filter(
      (key) =>
        key === 'glb:display' ||
        key === 'stl' ||
        key.startsWith('stl:') ||
        key.startsWith('step:') ||
        key.startsWith('plate-stl:') ||
        key.startsWith('region:'),
    )
    .sort();
  if (
    JSON.stringify(Object.keys(auditedArtifacts).sort()) !==
    JSON.stringify(ownedKeys)
  ) {
    return false;
  }
  for (const key of ownedKeys) {
    const audit = objectRecord(auditedArtifacts[key]);
    const artifact = objectRecord(reportArtifacts[key]);
    if (
      !audit ||
      !artifact ||
      audit.pass !== true ||
      !Array.isArray(audit.errors) ||
      audit.errors.length !== 0 ||
      audit.path !== artifact.path ||
      audit.sha256 !== artifact.sha256
    ) {
      return false;
    }
    if (key === 'glb:display') {
      const observed = objectRecord(audit.observed);
      const observedNodes = observed?.nodes;
      if (
        !exactKeys(audit, [
          'errors',
          'expectedNodes',
          'observed',
          'pass',
          'path',
          'reader',
          'sha256',
          'type',
        ]) ||
        audit.type !== 'glb' ||
        audit.reader !== 'trimesh-gltf' ||
        !Array.isArray(audit.expectedNodes) ||
        audit.expectedNodes.length === 0 ||
        !audit.expectedNodes.every(nonEmptyString) ||
        !observed ||
        !exactKeys(observed, ['geometryCount', 'nodes']) ||
        !Number.isInteger(observed.geometryCount) ||
        observed.geometryCount !== audit.expectedNodes.length ||
        !Array.isArray(observedNodes) ||
        observedNodes.length !== audit.expectedNodes.length ||
        !observedNodes.every(nonEmptyString) ||
        !audit.expectedNodes.every((node) => observedNodes.includes(node))
      ) {
        return false;
      }
      continue;
    }
    const type = key.startsWith('step:') ? 'step' : 'stl';
    const recordKeys = [
      'errors',
      'expected',
      'observed',
      'pass',
      'path',
      ...(type === 'step' ? ['reader'] : []),
      'sha256',
      'type',
    ];
    const expected = geometryAuditRecord(audit.expected, false, type);
    const observed = geometryAuditRecord(audit.observed, true, type);
    if (
      !exactKeys(audit, recordKeys) ||
      audit.type !== type ||
      (type === 'step' && audit.reader !== 'build123d-occt') ||
      !expected ||
      !observed ||
      !auditedGeometryMatches(expected, observed)
    ) {
      return false;
    }
    if (type === 'step') {
      const suffix = key.slice('step:'.length);
      const semanticBounds =
        suffix === 'assembly'
          ? objectRecord(objectRecord(report.backendData)?.semanticAssembly)?.boundsMm
          : objectRecord(report.parts?.[suffix]?.semantic)?.boundsMm;
      if (
        !boundsMatch(
          semanticBounds,
          observed.boundsMm,
          0.05,
        )
      ) {
        return false;
      }
    }
  }
  return true;
}

async function referencedRealPath(
  ownerPath: string,
  reference: unknown,
): Promise<string | undefined> {
  if (!nonEmptyString(reference)) return undefined;
  try {
    return await realpath(
      isAbsolute(reference) ? reference : resolve(dirname(ownerPath), reference),
    );
  } catch {
    return undefined;
  }
}

const SCENE_APPEARANCE_PALETTE = [
  '#5B8FF9',
  '#61DDAA',
  '#65789B',
  '#F6BD16',
  '#7262FD',
  '#78D3F8',
  '#9661BC',
  '#F6903D',
] as const;

function scenePartExplicitColor(
  part: Record<string, unknown>,
): string | undefined {
  const appearance = objectRecord(part.appearance);
  const candidate = Object.prototype.hasOwnProperty.call(part, 'color')
    ? part.color
    : appearance && Object.prototype.hasOwnProperty.call(appearance, 'baseColor')
      ? appearance.baseColor
      : appearance?.color;
  if (typeof candidate === 'string' && /^#[0-9A-Fa-f]{6}$/u.test(candidate)) {
    return candidate.toUpperCase();
  }
  return undefined;
}

function scenePartColor(part: Record<string, unknown>): string | undefined {
  const explicit = scenePartExplicitColor(part);
  if (explicit) return explicit;
  if (!nonEmptyString(part.id)) return undefined;
  const digest = createHash('sha256')
    .update(canonicalJson(part.id))
    .digest('hex');
  return SCENE_APPEARANCE_PALETTE[
    Number.parseInt(digest.slice(0, 2), 16) % SCENE_APPEARANCE_PALETTE.length
  ];
}

function proposedScenePartColors(
  parts: Map<string, Record<string, unknown>>,
  materials: Map<string, Record<string, unknown>>,
): Map<string, string> {
  const used = new Set(
    [...materials.values()]
      .map((material) => material.color)
      .filter(
        (color): color is string =>
          typeof color === 'string' && /^#[0-9A-Fa-f]{6}$/u.test(color),
      )
      .map((color) => color.toUpperCase()),
  );
  for (const part of parts.values()) {
    const explicit = scenePartExplicitColor(part);
    if (explicit) used.add(explicit);
  }
  const result = new Map<string, string>();
  for (const partId of [...parts.keys()].sort()) {
    const part = parts.get(partId)!;
    if (nonEmptyString(part.materialId)) continue;
    const explicit = scenePartExplicitColor(part);
    let color = explicit ?? scenePartColor(part);
    if (!color) continue;
    if (!explicit && used.has(color)) {
      const start = SCENE_APPEARANCE_PALETTE.indexOf(
        color as (typeof SCENE_APPEARANCE_PALETTE)[number],
      );
      for (let offset = 1; offset <= SCENE_APPEARANCE_PALETTE.length; offset += 1) {
        const candidate =
          SCENE_APPEARANCE_PALETTE[
            (start + offset) % SCENE_APPEARANCE_PALETTE.length
          ]!;
        if (!used.has(candidate)) {
          color = candidate;
          break;
        }
      }
    }
    used.add(color);
    result.set(partId, color);
  }
  return result;
}

function validMaterialSources(
  report: UnifiedBuildReport,
  intent: Record<string, unknown>,
  scene: Record<string, unknown>,
): boolean {
  if (report.materialPlan === undefined || report.materialPlan === null) return true;
  const plan = objectRecord(report.materialPlan);
  if (
    !plan ||
    !Array.isArray(plan.materials) ||
    !Array.isArray(plan.sourceBindings)
  ) {
    return false;
  }
  const materials = new Map<string, Record<string, unknown>>();
  for (const rawMaterial of plan.materials) {
    const material = objectRecord(rawMaterial);
    if (!material || !nonEmptyString(material.id)) return false;
    materials.set(material.id, material);
  }

  const rawRegions =
    intent.color_regions === undefined ? [] : intent.color_regions;
  if (!Array.isArray(rawRegions)) return false;
  const regions = new Map<string, Record<string, unknown>>();
  const regionCountByPart = new Map<string, number>();
  for (const rawRegion of rawRegions) {
    const region = objectRecord(rawRegion);
    if (
      !region ||
      !nonEmptyString(region.name) ||
      !nonEmptyString(region.part) ||
      regions.has(region.name)
    ) {
      return false;
    }
    regions.set(region.name, region);
    regionCountByPart.set(
      region.part,
      (regionCountByPart.get(region.part) ?? 0) + 1,
    );
  }

  const rawSceneMaterials =
    scene.materials === undefined ? [] : scene.materials;
  if (!Array.isArray(scene.parts) || !Array.isArray(rawSceneMaterials)) {
    return false;
  }
  const sceneParts = new Map<string, Record<string, unknown>>();
  for (const rawPart of scene.parts) {
    const part = objectRecord(rawPart);
    if (!part || !nonEmptyString(part.id) || sceneParts.has(part.id)) return false;
    sceneParts.set(part.id, part);
  }
  const sceneMaterials = new Map<string, Record<string, unknown>>();
  for (const rawMaterial of rawSceneMaterials) {
    const material = objectRecord(rawMaterial);
    if (
      !material ||
      !nonEmptyString(material.id) ||
      sceneMaterials.has(material.id)
    ) {
      return false;
    }
    sceneMaterials.set(material.id, material);
  }
  const proposedPartColors = proposedScenePartColors(
    sceneParts,
    sceneMaterials,
  );

  const intentSources = new Set<string>();
  const sceneSourceParts = new Set<string>();
  for (const rawBinding of plan.sourceBindings) {
    const binding = objectRecord(rawBinding);
    if (!binding || !nonEmptyString(binding.materialId)) return false;
    const material = materials.get(binding.materialId);
    if (!material || !nonEmptyString(binding.part)) return false;

    if (binding.sourceKind === 'intent-color-region') {
      if (!nonEmptyString(binding.sourceId) || intentSources.has(binding.sourceId)) {
        return false;
      }
      const region = regions.get(binding.sourceId);
      if (!region || region.part !== binding.part) return false;
      intentSources.add(binding.sourceId);
      if (
        (binding.scope === 'whole-part'
          ? binding.region !== null ||
            regionCountByPart.get(binding.part) !== 1
          : binding.region !== binding.sourceId) ||
        typeof region.hex !== 'string' ||
        binding.color !== region.hex.toUpperCase() ||
        material.color !== region.hex.toUpperCase() ||
        material.status !== 'declared'
      ) {
        return false;
      }
      continue;
    }

    const part = sceneParts.get(binding.part);
    if (
      !part ||
      regionCountByPart.has(binding.part) ||
      sceneSourceParts.has(binding.part)
    ) {
      return false;
    }
    sceneSourceParts.add(binding.part);
    let expectedColor: unknown;
    if (nonEmptyString(part.materialId)) {
      const sceneMaterial = sceneMaterials.get(part.materialId);
      if (
        binding.sourceKind !== 'scene-part-material' ||
        binding.sourceId !== part.materialId ||
        binding.materialId !== part.materialId ||
        !sceneMaterial
      ) {
        return false;
      }
      expectedColor =
        typeof sceneMaterial.color === 'string'
          ? sceneMaterial.color.toUpperCase()
          : sceneMaterial.color;
    } else {
      if (
        binding.sourceKind !== 'scene-part-appearance' ||
        binding.sourceId !== binding.part ||
        binding.materialId !== `proposed-${binding.part}`
      ) {
        return false;
      }
      expectedColor = proposedPartColors.get(binding.part);
    }
    if (
      binding.color !== expectedColor ||
      material.color !== expectedColor ||
      material.status !== 'proposed'
    ) {
      return false;
    }
  }
  return (
    intentSources.size === regions.size &&
    [...regions.keys()].every((sourceId) => intentSources.has(sourceId))
  );
}

async function validateInputChain(
  report: UnifiedBuildReport,
  inputPaths: Record<string, string>,
  inputJson: Record<string, unknown>,
): Promise<boolean> {
  const intent = objectRecord(inputJson.intent);
  const profile = objectRecord(inputJson.profile);
  const scene = objectRecord(inputJson.scene);
  if (
    intent?.schema !== INPUT_SCHEMAS.intent ||
    profile?.schema !== INPUT_SCHEMAS.profile ||
    scene?.schema !== INPUT_SCHEMAS.scene ||
    scene.revision !== report.revision
  ) {
    return false;
  }
  const inputs = objectRecord(report.inputs);
  const intentInput = objectRecord(inputs?.intent);
  const profileInput = objectRecord(inputs?.profile);
  const sceneIntent = objectRecord(scene.intentRef);
  const profileReference = objectRecord(objectRecord(intent.printability)?.profile);
  const dimensions = objectRecord(intent.dimensions_mm);
  const semanticBounds = objectRecord(
    objectRecord(objectRecord(report.backendData)?.semanticAssembly)?.boundsMm,
  );
  if (
    sceneIntent?.schema !== INPUT_SCHEMAS.intent ||
    sceneIntent.sha256 !== intentInput?.sha256 ||
    profileReference?.sha256 !== profileInput?.sha256 ||
    (await referencedRealPath(inputPaths.scene!, sceneIntent.path)) !==
      inputPaths.intent ||
    (await referencedRealPath(inputPaths.intent!, profileReference?.path)) !==
      inputPaths.profile
  ) {
    return false;
  }
  if (!dimensions || !semanticBounds || !validBounds(semanticBounds)) return false;
  const semanticSize = semanticBounds.size as number[];
  for (const [axisIndex, axis] of ['x', 'y', 'z'].entries()) {
    const dimension = objectRecord(dimensions[axis]);
    if (
      !dimension ||
      !finiteNumber(dimension.value) ||
      dimension.value <= 0 ||
      Math.abs(semanticSize[axisIndex]! - dimension.value) >
        SEMANTIC_ENVELOPE_TOLERANCE_MM
    ) {
      return false;
    }
  }
  return validMaterialSources(report, intent, scene);
}

async function validateHybridEvidence(
  report: UnifiedBuildReport,
  artifactPaths: Record<string, string>,
  artifactJson: Record<string, unknown>,
): Promise<boolean> {
  if (report.backend !== 'hybrid-mesh') return true;
  const partIds = Object.keys(report.parts ?? {}).sort();
  const brepPartIds = partIds.filter(
    (partId) => report.parts?.[partId]?.representationMaster === 'brep',
  );
  const boundScene = objectRecord(artifactJson.boundScene);
  const shapeConsistency = objectRecord(artifactJson.shapeConsistency);
  const stepConsistency = objectRecord(artifactJson.stepConsistency);
  if (
    boundScene?.schema !== INPUT_SCHEMAS.scene ||
    boundScene.revision !== report.revision ||
    shapeConsistency?.schema !== 'evidence-shape-consistency-manifest/v1' ||
    shapeConsistency.pass !== true ||
    shapeConsistency.revision !== report.revision ||
    stepConsistency?.schema !== 'evidence-step-consistency/v1' ||
    stepConsistency.pass !== true ||
    stepConsistency.revision !== report.revision
  ) {
    return false;
  }
  const shapeParts = objectRecord(shapeConsistency.parts);
  const skippedParts = shapeConsistency.skippedParts;
  if (
    !shapeParts ||
    JSON.stringify(Object.keys(shapeParts).sort()) !== JSON.stringify(partIds) ||
    !Object.values(shapeParts).every(
      (record) => objectRecord(record)?.pass === true,
    ) ||
    !Array.isArray(skippedParts) ||
    skippedParts.length !== 0
  ) {
    return false;
  }
  for (const partId of partIds) {
    const evidence = objectRecord(shapeParts[partId]);
    const meshes = objectRecord(evidence?.meshes);
    const manufacturing = objectRecord(meshes?.b);
    const semantic = objectRecord(report.parts?.[partId]?.semantic);
    if (
      !manufacturing ||
      !semantic ||
      !boundsMatch(manufacturing.boundsMm, semantic.boundsMm, 0.05)
    ) {
      return false;
    }
  }
  const stepParts = objectRecord(stepConsistency.parts);
  if (
    !stepParts ||
    JSON.stringify(Object.keys(stepParts).sort()) !==
      JSON.stringify(brepPartIds)
  ) {
    return false;
  }
  for (const partId of brepPartIds) {
    const audit = objectRecord(stepParts[partId]);
    const step = objectRecord(audit?.step);
    const artifact = objectRecord(report.artifacts?.[`step:${partId}`]);
    if (
      audit?.pass !== true ||
      objectRecord(audit.comparison)?.pass !== true ||
      step?.verifiedBrep !== true ||
      step.validator !== 'build123d-occt' ||
      step.path !== artifact?.path ||
      step.sha256 !== artifact?.sha256 ||
      canonicalJson(report.parts?.[partId]?.stepConsistency) !==
        canonicalJson(audit)
    ) {
      return false;
    }
  }
  if (
    canonicalJson(objectRecord(report.backendData)?.stepConsistency) !==
    canonicalJson(stepConsistency)
  ) {
    return false;
  }
  return true;
}

export async function validateUnifiedBuildReport(
  workspaceRoot: string,
  reportPath: string,
  options: ValidateBuildOptions = {},
): Promise<ValidatedBuildReport | undefined> {
  try {
    const canonicalRoot = await realpath(workspaceRoot);
    const canonicalReport = await realpath(reportPath);
    if (!insideRoot(canonicalRoot, canonicalReport)) return undefined;
    const reportSnapshot = await readStableReport(
      canonicalReport,
      options.maxBytes ?? 2 * 1024 * 1024,
    );
    if (
      !reportSnapshot ||
      (options.expectedReportSha256 !== undefined &&
        (!SHA256.test(options.expectedReportSha256) ||
          reportSnapshot.sha256 !== options.expectedReportSha256)) ||
      (options.minimumModifiedAtMs !== undefined &&
        reportSnapshot.modifiedAtMs < options.minimumModifiedAtMs)
    ) {
      return undefined;
    }
    const report = JSON.parse(
      reportSnapshot.bytes.toString('utf8'),
    ) as UnifiedBuildReport;
    if (!validStructure(report)) return undefined;

    const artifactPaths: Record<string, string> = {};
    const artifactJson: Record<string, unknown> = {};
    const semanticJsonArtifacts = new Set([
      'boundScene',
      'exportAudit',
      'materialPlan',
      'shapeConsistency',
      'stepConsistency',
    ]);
    for (const [key, reference] of Object.entries(report.artifacts ?? {})) {
      const bound = await resolveBoundFile(
        canonicalRoot,
        canonicalReport,
        reference,
        { parseJson: semanticJsonArtifacts.has(key) },
      );
      if (!bound) return undefined;
      if (
        options.minimumArtifactModifiedAtMs !== undefined &&
        // Hybrid BRep masters may intentionally predate this compilation;
        // their current-run OCCT/geometry audit is bound separately.
        !(report.backend === 'hybrid-mesh' && key.startsWith('step:')) &&
        bound.modifiedAtMs < options.minimumArtifactModifiedAtMs
      ) {
        return undefined;
      }
      artifactPaths[key] = bound.path;
      if (bound.json !== undefined) artifactJson[key] = bound.json;
    }
    const inputPaths: Record<string, string> = {};
    const inputJson: Record<string, unknown> = {};
    for (const [key, reference] of Object.entries(report.inputs ?? {})) {
      if (key === 'geometry') continue;
      const bound = await resolveBoundFile(
        canonicalRoot,
        canonicalReport,
        reference,
        {
          // The selected printer profile is an immutable skill asset and is
          // intentionally shared by every session. All mutable build inputs
          // remain confined to the session workspace.
          allowOutsideWorkspace: key === 'profile',
          parseJson: ['intent', 'profile', 'scene'].includes(key),
        },
      );
      if (!bound) return undefined;
      inputPaths[key] = bound.path;
      if (bound.json !== undefined) inputJson[key] = bound.json;
    }
    const geometry = objectRecord(report.inputs?.geometry);
    if (geometry) {
      for (const [key, reference] of Object.entries(geometry)) {
        const bound = await resolveBoundFile(
          canonicalRoot,
          canonicalReport,
          reference as BuildFileReference,
        );
        if (!bound) return undefined;
        inputPaths[`geometry:${key}`] = bound.path;
      }
    }
    if (!(await validateInputChain(report, inputPaths, inputJson))) {
      return undefined;
    }
    if (!(await validateBrepExportAudit(report, artifactPaths, artifactJson))) {
      return undefined;
    }
    if (!(await validateHybridEvidence(report, artifactPaths, artifactJson))) {
      return undefined;
    }
    if (report.materialPlan !== undefined && report.materialPlan !== null) {
      const materialPlan = artifactJson.materialPlan;
      if (materialPlan === undefined) return undefined;
      if (canonicalJson(materialPlan) !== canonicalJson(report.materialPlan)) {
        return undefined;
      }
    }
    return {
      artifactPaths,
      inputPaths,
      report,
      reportModifiedAtMs: reportSnapshot.modifiedAtMs,
      reportPath: canonicalReport,
    };
  } catch {
    return undefined;
  }
}
