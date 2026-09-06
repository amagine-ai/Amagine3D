import { isAbsolute, win32 } from 'node:path';

/**
 * Decide whether a value returned by path.relative stays below its root.
 * Checking both native and Win32 absolute forms keeps cross-volume paths
 * fail-closed even when fixtures are evaluated on a non-Windows host.
 */
export function isContainedRelativePath(value: string): boolean {
  return (
    value === '' ||
    (value !== '..' &&
      !value.startsWith('../') &&
      !value.startsWith('..\\') &&
      !isAbsolute(value) &&
      !win32.isAbsolute(value))
  );
}
