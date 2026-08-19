import type { StorageCapacityStatus } from './types';

function validByteCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

export async function inspectBrowserStorage(
  requestPersistence = true,
): Promise<StorageCapacityStatus> {
  if (
    typeof navigator === 'undefined' ||
    navigator.storage === undefined ||
    typeof navigator.storage.estimate !== 'function'
  ) {
    return {
      supported: false,
      persisted: null,
      persistenceRequested: false,
      quotaBytes: null,
      usageBytes: null,
      usageRatio: null,
      warning: 'not-supported',
    };
  }

  const storage = navigator.storage;
  const persistenceSupported =
    typeof storage.persisted === 'function' &&
    typeof storage.persist === 'function';
  let persisted = persistenceSupported ? await storage.persisted() : null;
  let persistenceRequested = false;
  if (requestPersistence && persistenceSupported && persisted === false) {
    persistenceRequested = true;
    persisted = await storage.persist();
  }
  const estimate = await storage.estimate();
  const quotaBytes = validByteCount(estimate.quota);
  const usageBytes = validByteCount(estimate.usage);
  const usageRatio =
    quotaBytes !== null && quotaBytes > 0 && usageBytes !== null
      ? usageBytes / quotaBytes
      : null;
  const warning =
    usageRatio !== null && usageRatio >= 0.85
      ? 'quota-nearly-full'
      : requestPersistence && persisted === false
        ? 'persistence-denied'
        : null;
  return {
    supported: true,
    persisted,
    persistenceRequested,
    quotaBytes,
    usageBytes,
    usageRatio,
    warning,
  };
}
