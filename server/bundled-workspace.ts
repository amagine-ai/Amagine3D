import {
  BUNDLED_POMODORO_SESSION_ID,
  type ArtifactCollection,
  type ArtifactSummary,
  type ArtifactWorkspace,
} from '../src/types.ts';
import { scanArtifacts } from './artifacts.ts';

export const BUNDLED_POMODORO_ID = 'amagine3d-pomodoro';
export const BUNDLED_POMODORO_NAME = 'Amagine3D Pomodoro Timer';
export const BUNDLED_POMODORO_PREVIEW = 'focus-bar-logical-assembly.3mf';

export const BUNDLED_POMODORO_WORKSPACE: ArtifactWorkspace = {
  id: BUNDLED_POMODORO_ID,
  name: BUNDLED_POMODORO_NAME,
  path: `bundled-projects/${BUNDLED_POMODORO_ID}/`,
  readOnly: true,
  sessionId: BUNDLED_POMODORO_SESSION_ID,
};

function asBundledArtifact(artifact: ArtifactSummary): ArtifactSummary {
  return {
    ...artifact,
    featured: artifact.name === BUNDLED_POMODORO_PREVIEW,
    readOnly: true,
    url: `/api/bundled-artifacts/file?path=${encodeURIComponent(artifact.path)}`,
  };
}

/** Expose immutable showcase files without creating a PI session JSONL. */
export async function bundledPomodoroArtifacts(
  bundledPomodoroRoot: string,
): Promise<ArtifactCollection> {
  const bundledArtifacts = (await scanArtifacts(bundledPomodoroRoot))
    .filter(({ kind }) => kind === 'model')
    .map(asBundledArtifact);
  return {
    artifacts: bundledArtifacts,
    artifactWorkspace: BUNDLED_POMODORO_WORKSPACE,
  };
}
