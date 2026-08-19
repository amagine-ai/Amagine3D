type MockFileNode = { kind: 'file'; name: string; bytes: Uint8Array };
type MockDirectoryNode = {
  kind: 'directory';
  name: string;
  entries: Map<string, MockFileNode | MockDirectoryNode>;
};

function notFound(name: string): DOMException {
  return new DOMException(`${name} was not found.`, 'NotFoundError');
}

function copyUint8Array(value: unknown): Uint8Array {
  if (!(value instanceof Uint8Array)) {
    throw new TypeError('The mock OPFS accepts Uint8Array writes only.');
  }
  return new Uint8Array(value);
}

function makeFileHandle(node: MockFileNode): FileSystemFileHandle {
  return {
    kind: 'file',
    name: node.name,
    getFile: async () =>
      ({
        arrayBuffer: async () => node.bytes.slice().buffer,
      }) as File,
    createWritable: async () =>
      ({
        write: async (data: FileSystemWriteChunkType) => {
          node.bytes = copyUint8Array(data);
        },
        close: async () => undefined,
        abort: async () => undefined,
      }) as FileSystemWritableFileStream,
  } as FileSystemFileHandle;
}

function makeDirectoryHandle(
  node: MockDirectoryNode,
): FileSystemDirectoryHandle {
  const handle = {
    kind: 'directory',
    name: node.name,
    getDirectoryHandle: async (
      name: string,
      options?: { create?: boolean },
    ) => {
      const existing = node.entries.get(name);
      if (existing?.kind === 'directory') {
        return makeDirectoryHandle(existing);
      }
      if (existing !== undefined || options?.create !== true) {
        throw notFound(name);
      }
      const directory: MockDirectoryNode = {
        kind: 'directory',
        name,
        entries: new Map(),
      };
      node.entries.set(name, directory);
      return makeDirectoryHandle(directory);
    },
    getFileHandle: async (name: string, options?: { create?: boolean }) => {
      const existing = node.entries.get(name);
      if (existing?.kind === 'file') {
        return makeFileHandle(existing);
      }
      if (existing !== undefined || options?.create !== true) {
        throw notFound(name);
      }
      const file: MockFileNode = {
        kind: 'file',
        name,
        bytes: new Uint8Array(),
      };
      node.entries.set(name, file);
      return makeFileHandle(file);
    },
    removeEntry: async (name: string) => {
      if (!node.entries.delete(name)) {
        throw notFound(name);
      }
    },
    entries: async function* () {
      for (const [name, entry] of node.entries) {
        yield [
          name,
          entry.kind === 'file'
            ? makeFileHandle(entry)
            : makeDirectoryHandle(entry),
        ] as [string, FileSystemHandle];
      }
    },
  };
  return handle as unknown as FileSystemDirectoryHandle;
}

export function createMockOpfsRoot(): FileSystemDirectoryHandle {
  return makeDirectoryHandle({
    kind: 'directory',
    name: '',
    entries: new Map(),
  });
}
