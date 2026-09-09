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
        Number(Boolean(right.primary)) - Number(Boolean(left.primary)) ||
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
        Number(Boolean(right.primary)) - Number(Boolean(left.primary)) ||
        (left.buildId && left.buildId === right.buildId && left.plateId && right.plateId
          ? Number(left.plateId) - Number(right.plateId) || Number(right.format === '3mf') - Number(left.format === '3mf')
          : 0) ||
        modifiedTime(right) - modifiedTime(left) ||
        Number(right.format === '3mf') - Number(left.format === '3mf') ||
        left.path.localeCompare(right.path),
    )[0];
}

export function printPreviewForSelection(
  artifacts: readonly ArtifactSummary[],
  selected: ArtifactSummary | undefined,
  model: { primaryPreviewPath: string } | undefined,
): ArtifactSummary | undefined {
  if (selected?.kind === 'model' && (selected.format === '3mf' || selected.format === 'stl')) return selected;
  return artifacts.find(({ path }) => path === model?.primaryPreviewPath) ?? preferredPrintPreviewArtifact(artifacts);
}

export function defaultPreviewArtifact(
  artifacts: readonly ArtifactSummary[],
): ArtifactSummary | undefined {
  return (
    preferredDisplayPreviewArtifact(artifacts) ??
    preferredPrintPreviewArtifact(artifacts)
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
  const groupKey = (artifact: ArtifactSummary) => artifact.buildId
    ? `${artifact.modelId ?? ''}:${artifact.buildId}` : 'legacy';
  const groups = new Map<string, number>();
  for (const artifact of artifacts) {
    if (artifact.primary && !groups.has(groupKey(artifact))) groups.set(groupKey(artifact), groups.size);
  }
  const preferred = artifacts.find(({ path }) => path === preferredPath);
  if (preferred) groups.set(groupKey(preferred), -1);
  return artifacts
    .map((artifact, index) => ({ artifact, index }))
    .filter(
      ({ artifact }) =>
        isPreviewModel(artifact) && artifact.primary === true,
    )
    .sort(
      (left, right) =>
        (groups.get(groupKey(left.artifact)) ?? 0) - (groups.get(groupKey(right.artifact)) ?? 0) ||
        Number(right.artifact.path === preferredPath) -
          Number(left.artifact.path === preferredPath) ||
        (left.artifact.buildId && left.artifact.buildId === right.artifact.buildId
          ? Number(left.artifact.plateId ?? 0) - Number(right.artifact.plateId ?? 0) ||
            Number(right.artifact.format === '3mf') - Number(left.artifact.format === '3mf')
          : 0) ||
        left.index - right.index,
    )
    .map(({ artifact }) => artifact);
}
