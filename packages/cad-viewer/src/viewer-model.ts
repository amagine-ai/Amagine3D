import type {
  Artifact,
  CadWorkerArtifactPayload,
  ColorRegionPlan,
} from '@amagine3d/cad-protocol';

import { ViewerDomainError } from './errors';
import type {
  ViewerFormat,
  ViewerModel,
  ViewerPart,
  ViewerRegion,
} from './types';

export type ViewerArtifactInput = Pick<
  CadWorkerArtifactPayload,
  'artifact' | 'bytes'
>;

function artifactFormat(artifact: Artifact): ViewerFormat | undefined {
  if (artifact.kind === 'model-3mf') return '3mf';
  if (artifact.kind === 'preview-glb') return 'glb';
  if (artifact.kind === 'region-stl' || artifact.kind === 'stl') return 'stl';
  return undefined;
}

function regionFor(
  artifact: Artifact,
  colorRegionPlan: ColorRegionPlan | undefined,
): ViewerRegion | undefined {
  if (!artifact.regionName) return undefined;
  const region = colorRegionPlan?.regions.find(
    (candidate) =>
      candidate.id === artifact.regionName ||
      candidate.name === artifact.regionName,
  );
  if (!region) {
    throw new ViewerDomainError(
      'LoadFailed',
      `Region “${artifact.regionName}” has no matching color metadata.`,
      false,
    );
  }
  return region;
}

export function createViewerModelFromArtifacts(input: {
  id: string;
  name: string;
  artifacts: readonly ViewerArtifactInput[];
  colorRegionPlan?: ColorRegionPlan;
}): ViewerModel {
  const renderable = input.artifacts.filter(({ artifact }) =>
    Boolean(artifactFormat(artifact)),
  );
  const regionArtifacts = renderable.filter(
    ({ artifact }) => artifact.kind === 'region-stl',
  );
  const glbArtifacts = renderable.filter(
    ({ artifact }) => artifact.kind === 'preview-glb',
  );
  const threeMfArtifacts = renderable.filter(
    ({ artifact }) => artifact.kind === 'model-3mf',
  );
  const stlArtifacts = renderable.filter(
    ({ artifact }) => artifact.kind === 'stl',
  );
  const selected =
    regionArtifacts.length > 0
      ? regionArtifacts
      : glbArtifacts.length > 0
        ? glbArtifacts
        : threeMfArtifacts.length > 0
          ? threeMfArtifacts
          : stlArtifacts;

  if (selected.length === 0) {
    throw new ViewerDomainError(
      'EmptyModel',
      'This run has no 3MF, STL, or GLB artifact to preview.',
      true,
    );
  }

  const partIds = new Set<string>();
  const parts: ViewerPart[] = selected.map(({ artifact, bytes }) => {
    if (bytes.byteLength === 0 || bytes.byteLength !== artifact.byteLength) {
      throw new ViewerDomainError(
        'LoadFailed',
        `${artifact.fileName} does not match its artifact metadata.`,
        false,
      );
    }
    if (partIds.has(artifact.id)) {
      throw new ViewerDomainError(
        'LoadFailed',
        `Artifact ID “${artifact.id}” is duplicated.`,
        false,
      );
    }
    partIds.add(artifact.id);
    const format = artifactFormat(artifact);
    if (!format) {
      throw new ViewerDomainError(
        'UnsupportedFormat',
        `${artifact.fileName} is not a supported preview format.`,
        false,
      );
    }
    const region = regionFor(artifact, input.colorRegionPlan);
    return {
      id: artifact.id,
      name: artifact.fileName,
      format,
      bytes,
      ...(region ? { region } : {}),
    };
  });

  return {
    id: input.id,
    name: input.name,
    parts,
    layout: 'assembled',
  };
}
