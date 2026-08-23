import type { ArtifactSummary } from '../types';

const CURRENT_PREVIEW_FORMATS = new Set(['3mf', 'stl']);

function modifiedTime(artifact: ArtifactSummary): number {
  const value = Date.parse(artifact.modifiedAt);
  return Number.isFinite(value) ? value : 0;
}

/**
 * Choose the printable model produced by the latest CAD build.
 *
 * Single-color builds emit one combined STL. Multi-color builds emit a combined
 * 3MF after their per-region STLs. STEP is an export format here, not a browser
 * preview source. A same-time 3MF wins the tie without relying on filename
 * keywords such as "currentmodel".
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
        modifiedTime(right) - modifiedTime(left) ||
        Number(right.format === '3mf') - Number(left.format === '3mf') ||
        left.path.localeCompare(right.path),
    )[0];
}
