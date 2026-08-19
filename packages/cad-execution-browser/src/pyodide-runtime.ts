import {
  artifactKindSchema,
  CadDomainError,
  jsonValueSchema,
  qaReportSchema,
  SCHEMA_VERSION,
  type CadWorkerRequest,
  type JsonValue,
} from '@amagine3d/cad-protocol';
import {
  loadPyodide,
  version as bundledPyodideVersion,
  type PyodideAPI,
} from 'pyodide';
import { z } from 'zod';

import { assertSha256 } from './hash';
import { PYTHON_BUILD_SCRIPT } from './python-execution';
import { decodeRuntimeBundle } from './runtime-bundle';
import {
  CAD_RUNTIME_MANIFEST,
  PROFILE_ASSETS,
  RUNTIME_LIMITS,
} from './runtime-manifest';
import { safeWorkspaceSegment } from './source-policy';
import type {
  CadRuntime,
  RuntimeArtifact,
  RuntimeBuildResult,
  RuntimeLogSink,
  RuntimeVersions,
} from './types';

const pythonBuildEnvelopeSchema = z.object({
  buildReport: jsonValueSchema,
  qaReport: qaReportSchema,
  artifacts: z.array(
    z.object({
      kind: artifactKindSchema,
      path: z.string().min(1).max(1_024),
      fileName: z.string().min(1).max(255),
      mediaType: z.string().min(1).max(160),
      regionName: z.string().trim().min(1).max(120).optional(),
    }),
  ),
  stdout: z.string().max(65_536),
});

const textEncoder = new TextEncoder();
const CAD_WORKER_OPFS_DIRECTORY = 'amagine3d-cad-worker';

function sameOriginRuntimeUrl(path: string): string {
  return new URL(path, globalThis.location.origin).href;
}

function canonicalJson(value: unknown): Uint8Array {
  return textEncoder.encode(`${JSON.stringify(value)}\n`);
}

function toUint8Array(value: string | Uint8Array): Uint8Array {
  return typeof value === 'string' ? textEncoder.encode(value) : value.slice();
}

function heapBytes(api: PyodideAPI): number | undefined {
  const internal = api as unknown as {
    _module?: { HEAP8?: { buffer?: ArrayBuffer } };
  };
  return internal._module?.HEAP8?.buffer?.byteLength;
}

export class PyodideCadRuntime implements CadRuntime {
  readonly #assetCache = new Map<string, Uint8Array>();
  #api: PyodideAPI | undefined;
  #nativeFs: { syncfs: () => Promise<void> } | undefined;
  #versions: RuntimeVersions | undefined;

  requiredAsset(url: string, label: string): Uint8Array {
    const bytes = this.#assetCache.get(sameOriginRuntimeUrl(url));
    if (bytes === undefined) {
      throw new Error(`${label} is missing from the verified runtime bundle.`);
    }
    return bytes;
  }

  async fetchVerifiedText(
    name: string,
    url: string,
    sha256: string,
  ): Promise<string> {
    const absoluteUrl = sameOriginRuntimeUrl(url);
    const cached = this.#assetCache.get(absoluteUrl);
    if (cached === undefined) {
      throw new CadDomainError(
        'IntegrityMismatch',
        `${name} is missing from the verified CAD runtime bundle.`,
        {
          category: 'integrity',
          retryable: false,
          operation: 'bootstrap',
        },
      );
    }
    await assertSha256(name, cached, sha256);
    return new TextDecoder().decode(cached);
  }

  async loadRuntimeBundle(log: RuntimeLogSink): Promise<void> {
    log('info', 'Loading local CAD runtime bundle from the same origin.');
    const response = await fetch(
      sameOriginRuntimeUrl(CAD_RUNTIME_MANIFEST.bundleUrl),
      { cache: 'force-cache', credentials: 'omit' },
    );
    if (!response.ok) {
      throw new CadDomainError(
        'IntegrityMismatch',
        `CAD runtime bundle could not be loaded (HTTP ${response.status}).`,
        { category: 'integrity', retryable: true, operation: 'bootstrap' },
      );
    }
    const declaredLength = Number(response.headers.get('Content-Length'));
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > RUNTIME_LIMITS.runtimeBundleBytes
    ) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        'CAD runtime bundle exceeds its configured byte limit.',
        { category: 'integrity', retryable: false, operation: 'bootstrap' },
      );
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    log(
      'info',
      `Loaded local CAD runtime bundle (${String(bytes.byteLength)} bytes); verifying integrity.`,
    );
    if (bytes.byteLength > RUNTIME_LIMITS.runtimeBundleBytes) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        'CAD runtime bundle exceeds its configured byte limit.',
        { category: 'integrity', retryable: false, operation: 'bootstrap' },
      );
    }
    try {
      await assertSha256(
        'CAD runtime bundle',
        bytes,
        CAD_RUNTIME_MANIFEST.bundleSha256,
      );
      for (const [path, payload] of decodeRuntimeBundle(bytes)) {
        const url = sameOriginRuntimeUrl(
          `${CAD_RUNTIME_MANIFEST.publicBasePath}/${path}`,
        );
        this.#assetCache.set(url, payload);
      }
      log(
        'info',
        `Verified and indexed ${String(this.#assetCache.size)} runtime assets.`,
      );
    } catch (cause) {
      throw new CadDomainError(
        'IntegrityMismatch',
        'CAD runtime bundle failed its pinned integrity check.',
        {
          category: 'integrity',
          retryable: false,
          operation: 'bootstrap',
          cause,
        },
      );
    }
  }

  async verifyStandaloneWasm(log: RuntimeLogSink): Promise<void> {
    const url = sameOriginRuntimeUrl(CAD_RUNTIME_MANIFEST.wasmUrl);
    const response = await fetch(url, {
      cache: 'force-cache',
      credentials: 'omit',
    });
    if (!response.ok) {
      throw new CadDomainError(
        'IntegrityMismatch',
        `CAD runtime WASM could not be loaded (HTTP ${response.status}).`,
        { category: 'integrity', retryable: true, operation: 'bootstrap' },
      );
    }
    const declaredLength = Number(response.headers.get('Content-Length'));
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > RUNTIME_LIMITS.wasmBytes
    ) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        'CAD runtime WASM exceeds its configured byte limit.',
        { category: 'integrity', retryable: false, operation: 'bootstrap' },
      );
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > RUNTIME_LIMITS.wasmBytes) {
      throw new CadDomainError(
        'ArtifactTooLarge',
        'CAD runtime WASM exceeds its configured byte limit.',
        { category: 'integrity', retryable: false, operation: 'bootstrap' },
      );
    }
    try {
      await assertSha256(
        'CAD runtime WASM',
        bytes,
        CAD_RUNTIME_MANIFEST.wasmSha256,
      );
      log('info', 'Standalone CAD WASM integrity verified.');
    } catch (cause) {
      throw new CadDomainError(
        'IntegrityMismatch',
        'CAD runtime WASM failed its pinned integrity check.',
        {
          category: 'integrity',
          retryable: false,
          operation: 'bootstrap',
          cause,
        },
      );
    }
  }

  verifiedFetch(originalFetch: typeof fetch): typeof fetch {
    return async (input, init) => {
      const rawUrl =
        typeof input === 'string' || input instanceof URL
          ? input.toString()
          : input.url;
      const url = new URL(rawUrl, globalThis.location.href);
      const cached = this.#assetCache.get(url.href);
      if (cached !== undefined) {
        return new Response(cached.slice().buffer, {
          status: 200,
          headers: {
            'Cache-Control': 'public, max-age=31536000, immutable',
            'Content-Length': String(cached.byteLength),
          },
        });
      }
      if (url.href === sameOriginRuntimeUrl(CAD_RUNTIME_MANIFEST.wasmUrl)) {
        return originalFetch(input, init);
      }
      if (url.pathname.startsWith(`${CAD_RUNTIME_MANIFEST.publicBasePath}/`)) {
        throw new Error(
          `Unbundled CAD runtime request blocked: ${url.pathname}`,
        );
      }
      return originalFetch(input, init);
    };
  }

  async pyodideModuleFactory(): Promise<
    (settings: unknown) => Promise<unknown>
  > {
    const moduleUrl = sameOriginRuntimeUrl(
      `${CAD_RUNTIME_MANIFEST.publicBasePath}/pyodide/pyodide.asm.mjs`,
    );
    const source = this.requiredAsset(moduleUrl, 'Verified Pyodide module');
    const blobUrl = URL.createObjectURL(
      new Blob([source.slice().buffer], { type: 'text/javascript' }),
    );
    try {
      const loaded = (await import(blobUrl)) as { default?: unknown };
      if (typeof loaded.default !== 'function') {
        throw new Error('Verified Pyodide module does not export a factory.');
      }
      return loaded.default as (settings: unknown) => Promise<unknown>;
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
  }

  retainProfileAssets(): void {
    const profileUrls = [
      PROFILE_ASSETS['single-color'].operations.url,
      PROFILE_ASSETS['single-color'].meshAudit.url,
      PROFILE_ASSETS['multi-color'].operations.url,
      PROFILE_ASSETS['multi-color'].meshAudit.url,
      PROFILE_ASSETS['multi-color'].threeMf.url,
    ].map(sameOriginRuntimeUrl);
    const profiles = profileUrls.map(
      (url) =>
        [url, this.requiredAsset(url, 'CAD workflow profile').slice()] as const,
    );
    this.#assetCache.clear();
    for (const [url, bytes] of profiles) this.#assetCache.set(url, bytes);
  }

  async bootstrap(log: RuntimeLogSink): Promise<RuntimeVersions> {
    if (this.#versions) return this.#versions;
    if (bundledPyodideVersion !== CAD_RUNTIME_MANIFEST.pyodideVersion) {
      throw new CadDomainError(
        'IntegrityMismatch',
        `Bundled Pyodide ${bundledPyodideVersion} does not match ${CAD_RUNTIME_MANIFEST.pyodideVersion}.`,
        { category: 'integrity', retryable: false, operation: 'bootstrap' },
      );
    }

    await this.loadRuntimeBundle(log);
    await this.verifyStandaloneWasm(log);
    log('info', `Loading Pyodide ${bundledPyodideVersion}.`);
    const loadOptions: Parameters<typeof loadPyodide>[0] = {
      indexURL: sameOriginRuntimeUrl(CAD_RUNTIME_MANIFEST.pyodideIndexUrl),
      lockFileContents: new TextDecoder().decode(
        this.requiredAsset(
          `${CAD_RUNTIME_MANIFEST.publicBasePath}/pyodide/pyodide-lock.json`,
          'Pyodide lock file',
        ),
      ),
      packageBaseUrl: sameOriginRuntimeUrl(
        `${CAD_RUNTIME_MANIFEST.publicBasePath}/pyodide/`,
      ),
      packages: CAD_RUNTIME_MANIFEST.pyodidePackages,
      stdout: (line) => log('info', line),
      stderr: (line) => log('error', line),
    };
    Object.assign(loadOptions, {
      createPyodideModule: await this.pyodideModuleFactory(),
    });
    log('info', 'Verified Pyodide module imported; initializing WebAssembly.');
    const originalFetch = globalThis.fetch;
    globalThis.fetch = this.verifiedFetch(originalFetch.bind(globalThis));
    let api: PyodideAPI;
    let raw: unknown;
    try {
      api = await loadPyodide(loadOptions);
      this.#api = api;
      log('info', 'Pyodide WebAssembly initialized; opening browser OPFS.');
      if (!navigator.storage?.getDirectory) {
        throw new CadDomainError(
          'StorageUnavailable',
          'Chrome OPFS is unavailable in the CAD Worker.',
          { category: 'storage', retryable: false, operation: 'bootstrap' },
        );
      }
      const originRoot = await navigator.storage.getDirectory();
      const workerRoot = await originRoot.getDirectoryHandle(
        CAD_WORKER_OPFS_DIRECTORY,
        { create: true },
      );
      this.#nativeFs = await api.mountNativeFS('/opfs', workerRoot);
      log('info', 'Browser OPFS mounted.');
      const localWheels = CAD_RUNTIME_MANIFEST.pythonPackages.map(
        (packagePin) => sameOriginRuntimeUrl(packagePin.url),
      );
      api.globals.set(
        '_amagine_runtime_wheels_json',
        JSON.stringify(localWheels),
      );
      log(
        'info',
        `Installing ${String(localWheels.length)} pinned local wheels.`,
      );
      raw = await api.runPythonAsync(`
import importlib.metadata
import json
import sys

import micropip
await micropip.install(
    json.loads(_amagine_runtime_wheels_json),
    keep_going=False,
    deps=False,
    reinstall=True,
)

versions = {
    "pyodide": "${bundledPyodideVersion}",
    "python": sys.version.split()[0],
    "build123d": importlib.metadata.version("build123d"),
    "ocpWasm": importlib.metadata.version("cadquery-ocp-novtk-OCP.wasm"),
    "lib3mfWasm": importlib.metadata.version("lib3mf-OCP.wasm"),
    "trimesh": importlib.metadata.version("trimesh"),
}
json.dumps(versions)
`);
      log('info', 'Pinned Python wheels installed; verifying versions.');
    } finally {
      globalThis.fetch = originalFetch;
    }
    if (typeof raw !== 'string') {
      throw new Error('Runtime version probe did not return JSON.');
    }
    const versions = JSON.parse(raw) as RuntimeVersions;
    const expected = {
      pyodide: CAD_RUNTIME_MANIFEST.pyodideVersion,
      build123d: CAD_RUNTIME_MANIFEST.build123dVersion,
      ocpWasm: CAD_RUNTIME_MANIFEST.ocpWasmVersion,
      lib3mfWasm: CAD_RUNTIME_MANIFEST.lib3mfWasmVersion,
      trimesh: CAD_RUNTIME_MANIFEST.trimeshVersion,
    };
    for (const [name, version] of Object.entries(expected)) {
      if (versions[name] !== version) {
        throw new CadDomainError(
          'IntegrityMismatch',
          `${name} runtime ${versions[name] ?? 'missing'} does not match ${version}.`,
          { category: 'integrity', retryable: false, operation: 'bootstrap' },
        );
      }
    }
    this.retainProfileAssets();
    log('info', 'CAD runtime versions verified.');
    this.#versions = versions;
    return versions;
  }

  async installProfile(
    api: PyodideAPI,
    workflowKind: Extract<CadWorkerRequest, { type: 'build' }>['workflowKind'],
    workspace: string,
  ): Promise<void> {
    const assets = PROFILE_ASSETS[workflowKind];
    const operations = await this.fetchVerifiedText(
      `${assets.directory}/amagine_cad.py`,
      assets.operations.url,
      assets.operations.sha256,
    );
    const meshAudit = await this.fetchVerifiedText(
      `${assets.directory}/amagine_mesh_audit.py`,
      assets.meshAudit.url,
      assets.meshAudit.sha256,
    );
    api.FS.mkdirTree(workspace);
    api.FS.writeFile(`${workspace}/amagine_cad.py`, operations);
    api.FS.writeFile(`${workspace}/amagine_mesh_audit.py`, meshAudit);
    if (workflowKind === 'multi-color') {
      const multiColorAssets = PROFILE_ASSETS['multi-color'];
      const threeMf = await this.fetchVerifiedText(
        `${assets.directory}/amagine_three_mf.py`,
        multiColorAssets.threeMf.url,
        multiColorAssets.threeMf.sha256,
      );
      api.FS.writeFile(`${workspace}/amagine_three_mf.py`, threeMf);
    }
  }

  async cleanupWorkspace(api: PyodideAPI, workspace: string): Promise<void> {
    api.globals.set('_amagine_cleanup_workspace', workspace);
    await api.runPythonAsync(`
from pathlib import Path
import shutil
cleanup_workspace = Path(_amagine_cleanup_workspace)
if cleanup_workspace.is_dir():
    shutil.rmtree(cleanup_workspace)
elif cleanup_workspace.exists():
    cleanup_workspace.unlink()
`);
    await this.#nativeFs?.syncfs();
  }

  async build(
    request: Extract<CadWorkerRequest, { type: 'build' }>,
    log: RuntimeLogSink,
  ): Promise<RuntimeBuildResult> {
    const api = this.#api;
    if (!api || !this.#versions) {
      throw new Error('Pyodide runtime is not bootstrapped.');
    }
    const projectId = safeWorkspaceSegment(request.projectId ?? request.runId);
    const runId = safeWorkspaceSegment(request.runId);
    const workspace = `/opfs/${projectId}/.cad-worker/${runId}`;
    await this.cleanupWorkspace(api, workspace);
    await this.installProfile(api, request.workflowKind, workspace);

    api.globals.set('_amagine_workspace', workspace);
    api.globals.set('_amagine_run_id', request.runId);
    api.globals.set('_amagine_workflow', request.workflowKind);
    api.globals.set('_amagine_source', request.source);
    api.globals.set('_amagine_source_hash', request.sourceHash);
    api.globals.set(
      '_amagine_overrides_json',
      JSON.stringify(request.parameterOverrides),
    );
    api.globals.set(
      '_amagine_color_plan_json',
      JSON.stringify(
        request.colorRegionPlan ?? { schemaVersion: 1, regions: [] },
      ),
    );
    api.globals.set(
      '_amagine_qa_targets_json',
      JSON.stringify(request.qaTargets ?? {}),
    );
    api.globals.set(
      '_amagine_mechanisms_json',
      JSON.stringify(request.mechanisms ?? []),
    );
    api.globals.set(
      '_amagine_feature_checks_json',
      JSON.stringify(request.featureChecks ?? []),
    );

    let raw: unknown;
    try {
      raw = await api.runPythonAsync(PYTHON_BUILD_SCRIPT);
    } catch (cause) {
      await this.cleanupWorkspace(api, workspace);
      const message = cause instanceof Error ? cause.message : String(cause);
      if (message.includes('SourceHashConflict')) {
        throw new CadDomainError(
          'SourceHashConflict',
          'CAD source hash does not match the build request.',
          {
            category: 'integrity',
            retryable: false,
            operation: 'build',
            cause,
          },
        );
      }
      if (message.includes('Forbidden') || message.includes('blocked')) {
        throw new CadDomainError('ExecutionRejected', message, {
          category: 'execution',
          retryable: false,
          operation: 'build',
          cause,
        });
      }
      throw cause;
    }
    if (typeof raw !== 'string')
      throw new Error('CAD build did not return JSON.');
    const envelope = pythonBuildEnvelopeSchema.parse(JSON.parse(raw));
    for (const line of envelope.stdout.trim().split('\n')) {
      if (line) log('info', line);
    }
    const buildReport = jsonValueSchema.parse(envelope.buildReport);
    const qaReport = qaReportSchema.parse(envelope.qaReport);
    const artifacts: RuntimeArtifact[] = [];
    for (const artifact of envelope.artifacts) {
      if (!artifact.path.startsWith(`${workspace}/cad_out/`)) {
        throw new CadDomainError(
          'ExecutionRejected',
          `Artifact escaped the project run directory: ${artifact.path}`,
          { category: 'execution', retryable: false, operation: 'export' },
        );
      }
      const bytes = toUint8Array(api.FS.readFile(artifact.path));
      artifacts.push({
        kind: artifact.kind,
        fileName: artifact.fileName,
        mediaType: artifact.mediaType,
        bytes,
        ...(artifact.regionName === undefined
          ? {}
          : { regionName: artifact.regionName }),
      });
    }

    const buildDocument = { schemaVersion: SCHEMA_VERSION, data: buildReport };
    artifacts.unshift(
      {
        kind: 'model-source',
        fileName: 'model.py',
        mediaType: 'text/x-python',
        bytes: textEncoder.encode(request.source),
      },
      {
        kind: 'build-report',
        fileName: 'build-report.json',
        mediaType: 'application/json',
        bytes: canonicalJson(buildDocument),
      },
      {
        kind: 'qa-report',
        fileName: 'qa-report.json',
        mediaType: 'application/json',
        bytes: canonicalJson(qaReport),
      },
    );
    if (request.workflowKind === 'multi-color' && request.colorRegionPlan) {
      artifacts.splice(1, 0, {
        kind: 'color-plan',
        fileName: 'color-plan.json',
        mediaType: 'application/json',
        bytes: canonicalJson(request.colorRegionPlan),
      });
    }

    const currentHeapBytes = heapBytes(api);
    await this.cleanupWorkspace(api, workspace);
    return {
      buildReport: buildReport as JsonValue,
      qaReport,
      artifacts,
      ...(currentHeapBytes === undefined
        ? {}
        : { wasmHeapBytes: currentHeapBytes }),
    };
  }

  dispose(): void {
    this.#api = undefined;
    this.#versions = undefined;
    this.#nativeFs = undefined;
    this.#assetCache.clear();
  }
}
