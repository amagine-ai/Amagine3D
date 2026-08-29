import { readFile } from 'node:fs/promises';
import { basename, isAbsolute, relative, resolve, sep } from 'node:path';

import type { ArtifactSummary } from '../src/types.ts';

const ASSEMBLY_BUILD_REPORT_SCHEMA = 'evidence-cad-assembly-build/v3';
const BUILD_REPORT_SCHEMAS = new Set([
  ASSEMBLY_BUILD_REPORT_SCHEMA,
  'evidence-cad-build/v4',
  'evidence-color-build/v5',
]);
const MAX_BUILD_REPORT_BYTES = 2 * 1024 * 1024;

interface ReportFileReference {
  path?: unknown;
}

interface RawBuildReport {
  artifacts?: Record<string, ReportFileReference>;
  part?: unknown;
  schema?: unknown;
  source?: ReportFileReference | null;
}

export interface ModelBuild {
  artifactPaths: string[];
  displayPreviewPath: string;
  modelId: string;
  primaryPreviewPath: string;
  reportPath: string;
  sourcePath: string;
}

function safeRelativePath(root: string, value: string): string | undefined {
  const candidate = isAbsolute(value) ? value : resolve(root, value);
  const path = relative(root, candidate);
  if (path === '' || path === '..' || path.startsWith(`..${sep}`)) {
    return undefined;
  }
  return path.split(sep).join('/');
}

function artifactPathForReference(
  root: string,
  artifacts: readonly ArtifactSummary[],
  value: unknown,
  kind?: ArtifactSummary['kind'],
): string | undefined {
  if (typeof value !== 'string') return undefined;
  const available = kind
    ? artifacts.filter((artifact) => artifact.kind === kind)
    : [...artifacts];
  const relativePath = safeRelativePath(root, value);
  if (
    relativePath &&
    available.some((artifact) => artifact.path === relativePath)
  ) {
    return relativePath;
  }
  const fileName = basename(value);
  const matches = available.filter(
    (artifact) => basename(artifact.path) === fileName,
  );
  return matches.length === 1 ? matches[0]?.path : undefined;
}

function reportArtifactPaths(
  root: string,
  artifacts: readonly ArtifactSummary[],
  report: RawBuildReport,
): string[] {
  const paths = Object.values(report.artifacts ?? {})
    .map((reference) =>
      artifactPathForReference(root, artifacts, reference?.path, 'model'),
    )
    .filter((path): path is string => Boolean(path));
  return [...new Set(paths)];
}

function primaryArtifactKey(schema: string): string {
  return schema === 'evidence-color-build/v5' ? '3mf' : 'stl';
}

export async function discoverModelBuilds(
  workspaceRoot: string,
  artifacts: readonly ArtifactSummary[],
): Promise<ModelBuild[]> {
  const builds: ModelBuild[] = [];
  for (const artifact of artifacts) {
    if (
      artifact.kind !== 'report' ||
      !artifact.name.endsWith('_report.json') ||
      artifact.size > MAX_BUILD_REPORT_BYTES
    ) {
      continue;
    }
    let report: RawBuildReport;
    try {
      report = JSON.parse(
        await readFile(resolve(workspaceRoot, artifact.path), 'utf8'),
      ) as RawBuildReport;
    } catch {
      continue;
    }
    const schema = String(report.schema);
    if (!BUILD_REPORT_SCHEMAS.has(schema)) continue;
    const sourcePath = artifactPathForReference(
      workspaceRoot,
      artifacts,
      report.source?.path,
      'source',
    );
    const primaryKey = primaryArtifactKey(schema);
    const primaryPreviewPath = artifactPathForReference(
      workspaceRoot,
      artifacts,
      report.artifacts?.[primaryKey]?.path,
      'model',
    );
    const displayPreviewPath = artifactPathForReference(
      workspaceRoot,
      artifacts,
      report.artifacts?.['glb:display']?.path,
      'model',
    );
    if (!sourcePath || !primaryPreviewPath || !displayPreviewPath) continue;
    builds.push({
      artifactPaths: reportArtifactPaths(workspaceRoot, artifacts, report),
      displayPreviewPath,
      modelId:
        typeof report.part === 'string' && report.part.trim()
          ? report.part
          : basename(sourcePath, '.py'),
      primaryPreviewPath,
      reportPath: artifact.path,
      sourcePath,
    });
  }
  return builds.sort((left, right) =>
    left.primaryPreviewPath.localeCompare(right.primaryPreviewPath),
  );
}
