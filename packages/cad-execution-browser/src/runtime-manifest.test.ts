import { readFile } from 'node:fs/promises';

import { describe, expect, it } from 'vitest';

import runtimeAssets from '../runtime-assets.json';

import { sha256Hex } from './hash';
import {
  CAD_RUNTIME_MANIFEST,
  PROFILE_ASSETS,
  RUNTIME_LIMITS,
} from './runtime-manifest';

describe('pinned CAD runtime assets', () => {
  it('locks unique downloadable wheels with immutable hashes', () => {
    expect(runtimeAssets.schemaVersion).toBe(1);
    const distributions = runtimeAssets.pythonWheels.map(
      (wheel) => wheel.distribution,
    );
    const fileNames = runtimeAssets.pythonWheels.map((wheel) => wheel.fileName);
    expect(new Set(distributions).size).toBe(distributions.length);
    expect(new Set(fileNames).size).toBe(fileNames.length);
    for (const wheel of runtimeAssets.pythonWheels) {
      expect(wheel.url).toMatch(/^https:\/\/files\.pythonhosted\.org\//u);
      expect(wheel.sha256).toMatch(/^[a-f0-9]{64}$/u);
    }
  });

  it('pins immutable runtime versions to versioned same-origin assets', () => {
    expect(CAD_RUNTIME_MANIFEST.build123dVersion).toBe('0.11.1');
    expect(CAD_RUNTIME_MANIFEST.pyodideVersion).toBe('314.0.3');
    expect(CAD_RUNTIME_MANIFEST.cacheKey).toBe(runtimeAssets.cacheKey);
    expect(CAD_RUNTIME_MANIFEST.publicBasePath).toBe(
      `/cad-runtime/${runtimeAssets.cacheKey}`,
    );
    expect(CAD_RUNTIME_MANIFEST.bundleUrl).toBe(
      `/cad-runtime/${runtimeAssets.cacheKey}/${runtimeAssets.bundle.fileName}`,
    );
    expect(CAD_RUNTIME_MANIFEST.bundleSha256).toMatch(/^[a-f0-9]{64}$/u);
    expect(CAD_RUNTIME_MANIFEST.pyodideAssets).toHaveLength(4);
    expect(CAD_RUNTIME_MANIFEST.pythonPackages).toHaveLength(
      runtimeAssets.pythonWheels.length,
    );
    for (const packagePin of CAD_RUNTIME_MANIFEST.pythonPackages) {
      expect(packagePin.sha256).toMatch(/^[a-f0-9]{64}$/u);
      expect(packagePin.wheelFileName).toContain(packagePin.version);
      expect(packagePin.url).toMatch(/^\/cad-runtime\//u);
    }
  });

  it('serves the Pyodide WASM as a standalone verified asset, not inside the bundle', () => {
    const staticAssets = runtimeAssets.pyodide.coreAssets.filter(
      (asset) => asset.serveStatic === true,
    );
    expect(staticAssets).toHaveLength(1);
    expect(staticAssets[0]?.fileName).toBe('pyodide.asm.wasm');
    expect(CAD_RUNTIME_MANIFEST.wasmUrl).toBe(
      `/cad-runtime/${runtimeAssets.cacheKey}/pyodide/pyodide.asm.wasm`,
    );
    expect(CAD_RUNTIME_MANIFEST.wasmSha256).toMatch(/^[a-f0-9]{64}$/u);
    expect(CAD_RUNTIME_MANIFEST.wasmSha256).toBe(staticAssets[0]?.sha256);
  });

  it('allows a cold runtime bootstrap more time than a geometry build', () => {
    expect(RUNTIME_LIMITS.bootstrapTimeoutMs).toBeGreaterThan(
      RUNTIME_LIMITS.defaultTimeoutMs,
    );
    expect(RUNTIME_LIMITS.bootstrapTimeoutMs).toBeGreaterThanOrEqual(600_000);
  });

  it('matches the checksums of the npm-distributed Pyodide core assets', async () => {
    for (const asset of CAD_RUNTIME_MANIFEST.pyodideAssets) {
      const fileName = asset.url.split('/').at(-1);
      if (!fileName) throw new Error(`Missing file name for ${asset.url}`);
      const bytes = await readFile(
        new URL(`../node_modules/pyodide/${fileName}`, import.meta.url),
      );
      expect(await sha256Hex(bytes), asset.name).toBe(asset.sha256);
    }
  });

  it('matches every Amagine3D workflow runtime checksum', async () => {
    for (const asset of runtimeAssets.profileAssets) {
      const bytes = await readFile(
        new URL(`../../../${asset.source}`, import.meta.url),
      );
      expect(await sha256Hex(bytes), asset.source).toBe(asset.sha256);
    }
    expect(PROFILE_ASSETS['single-color'].operations.url).toMatch(
      /^\/cad-runtime\//u,
    );
    expect(PROFILE_ASSETS['multi-color'].threeMf.url).toMatch(
      /^\/cad-runtime\//u,
    );
  });
});
