import type {
  Artifact,
  CadWorkflowKind,
  QaReport,
} from '@amagine3d/cad-protocol';

export type CadWorkflowProfile = {
  kind: CadWorkflowKind;
  profileId: 'hardware-enclosure-single' | 'hardware-enclosure-multi';
  publisher: 'publish_model' | 'publish_color_model';
  requiredArtifactKinds: readonly Artifact['kind'][];
  requiredQaSections: readonly string[];
};

const profiles: Record<CadWorkflowKind, CadWorkflowProfile> = {
  'single-color': {
    kind: 'single-color',
    profileId: 'hardware-enclosure-single',
    publisher: 'publish_model',
    requiredArtifactKinds: [
      'model-source',
      'build-report',
      'qa-report',
      'step',
      'stl',
    ],
    requiredQaSections: ['overall'],
  },
  'multi-color': {
    kind: 'multi-color',
    profileId: 'hardware-enclosure-multi',
    publisher: 'publish_color_model',
    requiredArtifactKinds: [
      'model-source',
      'color-plan',
      'build-report',
      'qa-report',
      'model-3mf',
      'region-stl',
    ],
    requiredQaSections: ['overall', 'regions', 'overlap', '3mf-readback'],
  },
};

export function getCadWorkflowProfile(
  workflowKind: CadWorkflowKind,
): CadWorkflowProfile {
  return profiles[workflowKind];
}

export function missingCompletionRequirements(
  workflowKind: CadWorkflowKind,
  report: QaReport,
  artifacts: readonly Artifact[],
): string[] {
  const profile = getCadWorkflowProfile(workflowKind);
  const kinds = new Set(artifacts.map((artifact) => artifact.kind));
  const missing = profile.requiredArtifactKinds
    .filter((kind) => !kinds.has(kind))
    .map((kind) => `artifact:${kind}`);
  if (report.workflowKind !== workflowKind) missing.push('qa:workflow-profile');
  if (report.status !== 'passed') missing.push('qa:passed');
  if (workflowKind === 'multi-color') {
    if (report.regionReports === undefined) missing.push('qa:regions');
    if (report.overlapCheck?.status === 'failed') missing.push('qa:overlap');
    if (report.threeMfReadbackCheck?.status !== 'passed') {
      missing.push('qa:3mf-readback');
    }
  }
  return [...new Set(missing)];
}
