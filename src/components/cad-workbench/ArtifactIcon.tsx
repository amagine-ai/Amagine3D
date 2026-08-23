import styles from './ArtifactIcon.module.css';
import type { ArtifactSummary } from '../../types';

interface ArtifactIconProps {
  artifact: ArtifactSummary;
  selected?: boolean;
  size?: 'compact' | 'regular';
}

function artifactGlyph(artifact: ArtifactSummary): string {
  if (artifact.kind === 'model') return '3D';
  if (artifact.kind === 'source') return 'PY';
  if (artifact.kind === 'image') return 'IMG';
  return '{}';
}

export function ArtifactIcon({
  artifact,
  selected = false,
  size = 'regular',
}: ArtifactIconProps) {
  return (
    <span
      aria-hidden="true"
      className={styles.icon}
      data-selected={selected || undefined}
      data-size={size}
    >
      {artifactGlyph(artifact)}
    </span>
  );
}
