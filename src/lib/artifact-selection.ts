import type { ArtifactSummary } from '../types';

const CURRENT_PREVIEW_FORMATS = new Set(['3mf', 'glb', 'stl']);

function modifiedTime(artifact: ArtifactSummary): number {
  const value = Date.parse(artifact.modifiedAt);
  return Number.isFinite(value) ? value : 0;
}

/**
 * Choose the visible model produced by the latest CAD build.
 *
 * Generated builds mark their display GLB as featured. Without build metadata,
 * prefer display GLBs for visual review and then fall back to print roots.
 */
export function preferredPreviewArtifact(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary | undefined {
  return artifacts
    .filter(
      (artifact) =>
        artifact.kind === 'model' &&
        artifact.format !== undefined &&
        CURRENT_PREVIEW_FORMATS.has(artifact.format),
    )
    .sort(
      (left, right) =>
        Number(Boolean(right.featured)) - Number(Boolean(left.featured)) ||
        Number(right.path.endsWith('-display.glb')) -
          Number(left.path.endsWith('-display.glb')) ||
        modifiedTime(right) - modifiedTime(left) ||
        Number(right.format === '3mf') - Number(left.format === '3mf') ||
        left.path.localeCompare(right.path),
    )[0];
}

function isPngImage(artifact: ArtifactSummary): boolean {
  return (
    artifact.kind === 'image' && artifact.path.toLowerCase().endsWith('.png')
  );
}

function isPreviewModel(artifact: ArtifactSummary): boolean {
  return (
    artifact.kind === 'model' &&
    artifact.format !== undefined &&
    CURRENT_PREVIEW_FORMATS.has(artifact.format)
  );
}

export function fileSectionArtifacts(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary[] {
  const preferredPath = preferredPreviewArtifact(artifacts)?.path;
  return artifacts
    .map((artifact, index) => ({ artifact, index }))
    .filter(({ artifact }) => isPreviewModel(artifact) || isPngImage(artifact))
    .sort(
      (left, right) =>
        Number(right.artifact.path === preferredPath) -
          Number(left.artifact.path === preferredPath) ||
        left.index - right.index,
    )
    .map(({ artifact }) => artifact);
}
