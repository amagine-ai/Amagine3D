import { UnsafeArchivePath } from './errors';

const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/u;

export function assertSafeStorageId(value: string, label: string): void {
  if (!SAFE_SEGMENT.test(value) || value === '.' || value === '..') {
    throw new UnsafeArchivePath(`${label}:${value}`);
  }
}

export function assertSafeArchivePath(path: string): void {
  if (
    path.length === 0 ||
    path.length > 1_024 ||
    path.startsWith('/') ||
    path.includes('\\') ||
    path.includes('\0') ||
    /^[A-Za-z]:/u.test(path)
  ) {
    throw new UnsafeArchivePath(path);
  }

  const segments = path.split('/');
  if (
    segments.some(
      (segment) => segment.length === 0 || segment === '.' || segment === '..',
    )
  ) {
    throw new UnsafeArchivePath(path);
  }
}

export function joinPath(...segments: string[]): string {
  return segments.filter((segment) => segment.length > 0).join('/');
}

export function parentPath(path: string): string {
  const lastSlash = path.lastIndexOf('/');
  return lastSlash === -1 ? '' : path.slice(0, lastSlash);
}

export function baseName(path: string): string {
  const lastSlash = path.lastIndexOf('/');
  return lastSlash === -1 ? path : path.slice(lastSlash + 1);
}
