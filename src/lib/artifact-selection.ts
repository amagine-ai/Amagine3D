import type { ArtifactSummary } from '../types';

const DISPLAY_PREVIEW_FORMATS = new Set(['glb']);
const PRINT_PREVIEW_FORMATS = new Set(['3mf', 'stl']);
const CURRENT_PREVIEW_FORMATS = new Set([
  ...DISPLAY_PREVIEW_FORMATS,
  ...PRINT_PREVIEW_FORMATS,
]);

function modifiedTime(artifact: ArtifactSummary): number {
  const value = Date.parse(artifact.modifiedAt);
  return Number.isFinite(value) ? value : 0;
}

/**
 * Choose the display model produced by the latest CAD build.
 */
export function preferredDisplayPreviewArtifact(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary | undefined {
  return artifacts
    .filter(
      (artifact) =>
        artifact.kind === 'model' &&
        artifact.format !== undefined &&
        DISPLAY_PREVIEW_FORMATS.has(artifact.format),
    )
    .sort(
      (left, right) =>
        Number(Boolean(right.featured)) - Number(Boolean(left.featured)) ||
        Number(right.path.endsWith('-display.glb')) -
          Number(left.path.endsWith('-display.glb')) ||
        modifiedTime(right) - modifiedTime(left) ||
        left.path.localeCompare(right.path),
    )[0];
}

/** Choose the latest printable package, preferring 3MF for the same build. */
export function preferredPrintPreviewArtifact(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary | undefined {
  return artifacts
    .filter(
      (artifact) =>
        artifact.kind === 'model' &&
        artifact.format !== undefined &&
        PRINT_PREVIEW_FORMATS.has(artifact.format),
    )
    .sort(
      (left, right) =>
        Number(Boolean(right.featured)) - Number(Boolean(left.featured)) ||
        modifiedTime(right) - modifiedTime(left) ||
        Number(right.format === '3mf') - Number(left.format === '3mf') ||
        left.path.localeCompare(right.path),
    )[0];
}

export function defaultPreviewArtifact(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary | undefined {
  return (
    preferredDisplayPreviewArtifact(artifacts) ??
    preferredPrintPreviewArtifact(artifacts)
  );
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
  const preferredPath = defaultPreviewArtifact(artifacts)?.path;
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
