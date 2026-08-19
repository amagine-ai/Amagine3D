import type {
  Artifact,
  CadWorkerRequest,
  CadWorkerResponse,
  JsonValue,
  QaReport,
} from '@amagine3d/cad-protocol';

export type RuntimeVersions = Record<string, string>;

export type RuntimeAsset = {
  name: string;
  url: string;
  sha256: string;
};

export type RuntimePackagePin = {
  distribution: string;
  version: string;
  wheelFileName: string;
  url: string;
  sha256: string;
};

export type RuntimeManifest = {
  schemaVersion: 1;
  cacheKey: string;
  publicBasePath: string;
  bundleUrl: string;
  bundleSha256: string;
  wasmUrl: string;
  wasmSha256: string;
  pyodideVersion: string;
  pyodideIndexUrl: string;
  pyodidePackages: string[];
  pyodideAssets: RuntimeAsset[];
  pythonPackages: RuntimePackagePin[];
  build123dVersion: string;
  ocpWasmVersion: string;
  lib3mfWasmVersion: string;
  trimeshVersion: string;
  workflowRevision: string;
};

export type RuntimeArtifact = {
  kind: Artifact['kind'];
  fileName: string;
  mediaType: string;
  bytes: Uint8Array;
  regionName?: string;
};

export type RuntimeBuildResult = {
  buildReport: JsonValue;
  qaReport: QaReport;
  artifacts: RuntimeArtifact[];
  wasmHeapBytes?: number;
};

export type RuntimeLogSink = (
  level: Extract<CadWorkerResponse, { type: 'log' }>['level'],
  line: string,
) => void;

export interface CadRuntime {
  bootstrap(log: RuntimeLogSink): Promise<RuntimeVersions>;
  build(
    request: Extract<CadWorkerRequest, { type: 'build' }>,
    log: RuntimeLogSink,
  ): Promise<RuntimeBuildResult>;
  dispose(): void;
}

export type CadExecutorEvent =
  | Extract<CadWorkerResponse, { type: 'progress' }>
  | Extract<CadWorkerResponse, { type: 'log' }>;

export type CadExecutorOptions = {
  workerFactory: () => Worker;
  timeoutMs?: number;
  bootstrapTimeoutMs?: number;
  onEvent?: (event: CadExecutorEvent) => void;
  createRequestId?: () => string;
};
