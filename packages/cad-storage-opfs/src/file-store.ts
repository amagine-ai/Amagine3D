import { StorageNotAvailable } from './errors';
import { toOwnedBytes } from './hash';
import { baseName, joinPath, parentPath } from './path';

export interface FileStore {
  read(path: string): Promise<Uint8Array | undefined>;
  write(path: string, contents: Uint8Array): Promise<void>;
  remove(path: string): Promise<void>;
  list(prefix?: string): Promise<string[]>;
  size(path: string): Promise<number | undefined>;
}

export class MemoryFileStore implements FileStore {
  readonly files = new Map<string, Uint8Array>();
  beforeWrite: ((path: string, contents: Uint8Array) => void) | undefined;

  async read(path: string): Promise<Uint8Array | undefined> {
    const value = this.files.get(path);
    return value === undefined ? undefined : value.slice();
  }

  async write(path: string, contents: Uint8Array): Promise<void> {
    this.beforeWrite?.(path, contents);
    this.files.set(path, contents.slice());
  }

  async remove(path: string): Promise<void> {
    this.files.delete(path);
  }

  async list(prefix = ''): Promise<string[]> {
    return [...this.files.keys()]
      .filter((path) => path.startsWith(prefix))
      .sort();
  }

  async size(path: string): Promise<number | undefined> {
    return this.files.get(path)?.byteLength;
  }
}

export class OpfsFileStore implements FileStore {
  constructor(private readonly root: FileSystemDirectoryHandle) {}

  async read(path: string): Promise<Uint8Array | undefined> {
    try {
      const handle = await this.getFileHandle(path, false);
      const file = await handle.getFile();
      return new Uint8Array(await file.arrayBuffer());
    } catch (error) {
      if (error instanceof DOMException && error.name === 'NotFoundError') {
        return undefined;
      }
      throw error;
    }
  }

  async write(path: string, contents: Uint8Array): Promise<void> {
    const handle = await this.getFileHandle(path, true);
    const writable = await handle.createWritable();
    try {
      await writable.write(toOwnedBytes(contents));
      await writable.close();
    } catch (error) {
      await writable.abort(error).catch(() => undefined);
      throw error;
    }
  }

  async remove(path: string): Promise<void> {
    const directory = await this.getDirectory(parentPath(path), false);
    try {
      await directory.removeEntry(baseName(path));
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'NotFoundError')) {
        throw error;
      }
    }
  }

  async list(prefix = ''): Promise<string[]> {
    const normalizedPrefix = prefix.replace(/\/+$/u, '');
    const startDirectory = normalizedPrefix.includes('/')
      ? parentPath(normalizedPrefix)
      : '';
    const results: string[] = [];

    try {
      const directory = await this.getDirectory(startDirectory, false);
      await this.walk(directory, startDirectory, results);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'NotFoundError') {
        return [];
      }
      throw error;
    }

    return results.filter((path) => path.startsWith(prefix)).sort();
  }

  async size(path: string): Promise<number | undefined> {
    try {
      const handle = await this.getFileHandle(path, false);
      return (await handle.getFile()).size;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'NotFoundError') {
        return undefined;
      }
      throw error;
    }
  }

  private async getDirectory(
    path: string,
    create: boolean,
  ): Promise<FileSystemDirectoryHandle> {
    let directory = this.root;
    for (const segment of path.split('/').filter(Boolean)) {
      directory = await directory.getDirectoryHandle(segment, { create });
    }
    return directory;
  }

  private async getFileHandle(
    path: string,
    create: boolean,
  ): Promise<FileSystemFileHandle> {
    const directory = await this.getDirectory(parentPath(path), create);
    return directory.getFileHandle(baseName(path), { create });
  }

  private async walk(
    directory: FileSystemDirectoryHandle,
    prefix: string,
    results: string[],
  ): Promise<void> {
    for await (const [name, handle] of directory.entries()) {
      const path = joinPath(prefix, name);
      if (handle.kind === 'file') {
        results.push(path);
      } else {
        await this.walk(handle, path, results);
      }
    }
  }
}

export async function openOpfsFileStore(): Promise<OpfsFileStore> {
  if (
    typeof navigator === 'undefined' ||
    navigator.storage === undefined ||
    typeof navigator.storage.getDirectory !== 'function'
  ) {
    throw new StorageNotAvailable(
      'Origin Private File System is not available in this browser context.',
    );
  }

  try {
    return new OpfsFileStore(await navigator.storage.getDirectory());
  } catch (error) {
    throw new StorageNotAvailable('Unable to open the OPFS root.', error);
  }
}
