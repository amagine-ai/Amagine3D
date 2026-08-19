import runtimeAssets from '../runtime-assets.json';

import type { RuntimeManifest } from './types';

const publicBasePath = `/cad-runtime/${runtimeAssets.cacheKey}`;

function packageVersion(distribution: string): string {
  const packagePin = runtimeAssets.pythonWheels.find(
    (candidate) => candidate.distribution === distribution,
  );
  if (packagePin === undefined) {
    throw new Error(`CAD runtime package ${distribution} is not pinned.`);
  }
  return packagePin.version;
}

function profileAsset(id: string) {
  const asset = runtimeAssets.profileAssets.find(
    (candidate) => candidate.id === id,
  );
  if (asset === undefined) {
    throw new Error(`CAD profile asset ${id} is not pinned.`);
  }
  return {
    url: `${publicBasePath}/${asset.path}`,
    sha256: asset.sha256,
  };
}

function standaloneWasmAsset() {
  const asset = runtimeAssets.pyodide.coreAssets.find(
    (candidate) => candidate.serveStatic === true,
  );
  if (asset === undefined) {
    throw new Error('CAD runtime WASM asset is not marked serveStatic.');
  }
  return {
    url: `${publicBasePath}/pyodide/${asset.fileName}`,
    sha256: asset.sha256,
  };
}

const wasmAsset = standaloneWasmAsset();

export const CAD_RUNTIME_MANIFEST: RuntimeManifest = Object.freeze({
  schemaVersion: 1,
  cacheKey: runtimeAssets.cacheKey,
  publicBasePath,
  bundleUrl: `${publicBasePath}/${runtimeAssets.bundle.fileName}`,
  bundleSha256: runtimeAssets.bundle.sha256,
  wasmUrl: wasmAsset.url,
  wasmSha256: wasmAsset.sha256,
  pyodideVersion: runtimeAssets.pyodide.version,
  pyodideIndexUrl: `${publicBasePath}/pyodide/`,
  pyodidePackages: runtimeAssets.pyodide.packageSeeds,
  pyodideAssets: runtimeAssets.pyodide.coreAssets.map((asset) => ({
    name: asset.fileName,
    url: `${publicBasePath}/pyodide/${asset.fileName}`,
    sha256: asset.sha256,
  })),
  pythonPackages: runtimeAssets.pythonWheels.map((packagePin) => ({
    distribution: packagePin.distribution,
    version: packagePin.version,
    wheelFileName: packagePin.fileName,
    url: `${publicBasePath}/wheels/${packagePin.fileName}`,
    sha256: packagePin.sha256,
  })),
  build123dVersion: packageVersion('build123d'),
  ocpWasmVersion: packageVersion('cadquery-ocp-novtk-OCP.wasm'),
  lib3mfWasmVersion: packageVersion('lib3mf-OCP.wasm'),
  trimeshVersion: packageVersion('trimesh'),
  workflowRevision: 'workflow-2026.08.19.3',
});

export const RUNTIME_LIMITS = Object.freeze({
  runtimeBundleBytes: 128 * 1024 * 1024,
  wasmBytes: 32 * 1024 * 1024,
  artifactBytes: 128 * 1024 * 1024,
  totalArtifactBytes: 384 * 1024 * 1024,
  logBytes: 64 * 1024,
  defaultTimeoutMs: 120_000,
  // A cold browser start downloads, verifies, and installs Pyodide plus the
  // pinned OpenCascade/build123d wheels. Slow or uncached connections can
  // legitimately take several minutes, and killing the Worker here forces the
  // next retry to restart the entire bootstrap from zero.
  bootstrapTimeoutMs: 600_000,
});

export const PROFILE_ASSETS = Object.freeze({
  'single-color': {
    directory: 'hardware-enclosure-single',
    operations: profileAsset('single-color-operations'),
    meshAudit: profileAsset('single-color-mesh-audit'),
  },
  'multi-color': {
    directory: 'hardware-enclosure-multi',
    operations: profileAsset('multi-color-operations'),
    meshAudit: profileAsset('multi-color-mesh-audit'),
    threeMf: profileAsset('multi-color-three-mf'),
  },
});
