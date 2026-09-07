import { realpath } from 'node:fs/promises';
import { basename, relative, sep } from 'node:path';

import type { ArtifactSummary } from '../src/types.ts';
import {
  type UnifiedBuildReport,
  validateUnifiedBuildReport,
} from './build-report.ts';
import { resolveArtifactPath } from './artifacts.ts';

const MAX_BUILD_REPORT_BYTES = 2 * 1024 * 1024;

export interface ModelBuild {
  artifactPaths: string[];
  displayPreviewPath: string;
  modelId: string;
  primaryPreviewPath: string;
  reportPath: string;
  sourcePath?: string;
  topLevelArtifactPaths: string[];
}

function safeRelativePath(root: string, candidate: string): string | undefined {
  const path = relative(root, candidate);
  if (path === '' || path === '..' || path.startsWith(`..${sep}`)) {
    return undefined;
  }
  return path.split(sep).join('/');
}

function primaryArtifactKey(
  report: UnifiedBuildReport,
): '3mf' | 'stl' | undefined {
  const parts = report.artifactMatrix?.parts;
  if (!parts || Object.keys(parts).length === 0) return undefined;
  const records = Object.values(parts);
  if (
    records.some(
      (record) =>
        record?.stl !== 'required' ||
        record?.glb !== 'required' ||
        !['required', 'not-applicable'].includes(String(record?.step)) ||
        !['required', 'not-applicable'].includes(String(record?.threeMf)),
    )
  ) {
    return undefined;
  }
  const backend = String(report.backend);
  const requiresThreeMf = records.some(
    (record) => record.threeMf === 'required',
  );
  if (
    (['brep-color-regions', 'hybrid-mesh'].includes(backend) &&
      !requiresThreeMf) ||
    (backend.startsWith('brep-') &&
      records.some((record) => record.step !== 'required'))
  ) {
    return undefined;
  }
  return requiresThreeMf ? '3mf' : 'stl';
}

function primaryArtifactReferenceKey(
  report: UnifiedBuildReport,
  key: '3mf' | 'stl',
): string | undefined {
  if (report.artifacts?.[key] || key === '3mf') return key;
  const partIds = Object.keys(report.artifactMatrix?.parts ?? {});
  return partIds.length === 1 ? `stl:${partIds[0]}` : undefined;
}

export async function discoverModelBuilds(
  workspaceRoot: string,
  artifacts: readonly ArtifactSummary[],
): Promise<ModelBuild[]> {
  const builds: ModelBuild[] = [];
  const canonicalRoot = await realpath(workspaceRoot);
  for (const artifact of artifacts) {
    if (
      artifact.kind !== 'report' ||
      !artifact.name.endsWith('_report.json') ||
      artifact.size > MAX_BUILD_REPORT_BYTES
    ) {
      continue;
    }
    const reportPath = await resolveArtifactPath(workspaceRoot, artifact.path);
    if (!reportPath) continue;
    const validated = await validateUnifiedBuildReport(
      workspaceRoot,
      reportPath,
      { maxBytes: MAX_BUILD_REPORT_BYTES },
    );
    if (!validated) continue;
    const report = validated.report;
    const primaryKey = primaryArtifactKey(report);
    if (!primaryKey) continue;
    const primaryReference = primaryArtifactReferenceKey(report, primaryKey);
    const primaryPreviewPath = primaryReference
      ? safeRelativePath(
          canonicalRoot,
          validated.artifactPaths[primaryReference] ?? '',
        )
      : undefined;
    const displayPreviewPath = safeRelativePath(
      canonicalRoot,
      validated.artifactPaths['glb:display'] ?? '',
    );
    if (!primaryPreviewPath || !displayPreviewPath) continue;
    const sourcePath = validated.inputPaths.source
      ? safeRelativePath(canonicalRoot, validated.inputPaths.source)
      : undefined;
    const artifactPaths = [
      ...new Set(
        Object.values(validated.artifactPaths)
          .map((path) => safeRelativePath(canonicalRoot, path))
          .filter((path): path is string => Boolean(path)),
      ),
    ];
    const topLevelArtifactPaths = [
      displayPreviewPath,
      primaryPreviewPath,
      ...['3mf', 'stl'].map((key) =>
        safeRelativePath(
          canonicalRoot,
          validated.artifactPaths[key] ?? '',
        ),
      ),
    ].filter((path): path is string => Boolean(path));
    builds.push({
      artifactPaths,
      displayPreviewPath,
      modelId:
        typeof report.part === 'string' && report.part.trim()
          ? report.part
          : basename(primaryPreviewPath, `.${primaryKey}`),
      primaryPreviewPath,
      reportPath: artifact.path,
      ...(sourcePath ? { sourcePath } : {}),
      topLevelArtifactPaths: [...new Set(topLevelArtifactPaths)],
    });
  }
  return builds.sort((left, right) =>
    left.primaryPreviewPath.localeCompare(right.primaryPreviewPath),
  );
}
