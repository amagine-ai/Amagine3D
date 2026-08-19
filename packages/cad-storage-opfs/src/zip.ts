import { ArchiveIntegrityMismatch } from './errors';
import { decodeText, encodeText } from './hash';
import { assertSafeArchivePath } from './path';

const LOCAL_FILE_SIGNATURE = 0x04034b50;
const CENTRAL_FILE_SIGNATURE = 0x02014b50;
const END_SIGNATURE = 0x06054b50;
const UTF8_FLAG = 0x0800;
const MAX_ENTRY_COUNT = 10_000;
const MAX_ARCHIVE_BYTES = 512 * 1024 * 1024;

type ZipEntry = { path: string; bytes: Uint8Array };

const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    const tableIndex = (crc ^ byte) & 0xff;
    const tableValue = CRC_TABLE[tableIndex];
    if (tableValue === undefined) {
      throw new ArchiveIntegrityMismatch('ZIP', 'CRC table lookup failed');
    }
    crc = tableValue ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function concat(chunks: Uint8Array[]): Uint8Array {
  const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const output = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

function u16(value: number): Uint8Array {
  const bytes = new Uint8Array(2);
  new DataView(bytes.buffer).setUint16(0, value, true);
  return bytes;
}

function u32(value: number): Uint8Array {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, value, true);
  return bytes;
}

function readU16(view: DataView, offset: number, path = 'ZIP'): number {
  if (offset < 0 || offset + 2 > view.byteLength) {
    throw new ArchiveIntegrityMismatch(path, 'truncated 16-bit field');
  }
  return view.getUint16(offset, true);
}

function readU32(view: DataView, offset: number, path = 'ZIP'): number {
  if (offset < 0 || offset + 4 > view.byteLength) {
    throw new ArchiveIntegrityMismatch(path, 'truncated 32-bit field');
  }
  return view.getUint32(offset, true);
}

export function encodeZip(entries: Iterable<ZipEntry>): Uint8Array {
  const localChunks: Uint8Array[] = [];
  const centralChunks: Uint8Array[] = [];
  let localOffset = 0;
  let entryCount = 0;

  for (const entry of entries) {
    assertSafeArchivePath(entry.path);
    const name = encodeText(entry.path);
    const checksum = crc32(entry.bytes);
    const localHeader = concat([
      u32(LOCAL_FILE_SIGNATURE),
      u16(20),
      u16(UTF8_FLAG),
      u16(0),
      u16(0),
      u16(0),
      u32(checksum),
      u32(entry.bytes.byteLength),
      u32(entry.bytes.byteLength),
      u16(name.byteLength),
      u16(0),
      name,
    ]);
    localChunks.push(localHeader, entry.bytes);

    centralChunks.push(
      concat([
        u32(CENTRAL_FILE_SIGNATURE),
        u16(20),
        u16(20),
        u16(UTF8_FLAG),
        u16(0),
        u16(0),
        u16(0),
        u32(checksum),
        u32(entry.bytes.byteLength),
        u32(entry.bytes.byteLength),
        u16(name.byteLength),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(0),
        u32(localOffset),
        name,
      ]),
    );
    localOffset += localHeader.byteLength + entry.bytes.byteLength;
    entryCount += 1;
  }

  if (entryCount > 0xffff) {
    throw new ArchiveIntegrityMismatch('ZIP', 'too many entries for ZIP32');
  }
  const central = concat(centralChunks);
  const end = concat([
    u32(END_SIGNATURE),
    u16(0),
    u16(0),
    u16(entryCount),
    u16(entryCount),
    u32(central.byteLength),
    u32(localOffset),
    u16(0),
  ]);
  const archive = concat([...localChunks, central, end]);
  if (archive.byteLength > MAX_ARCHIVE_BYTES) {
    throw new ArchiveIntegrityMismatch('ZIP', 'archive exceeds 512 MiB limit');
  }
  return archive;
}

function findEnd(view: DataView): number {
  const minimum = Math.max(0, view.byteLength - 65_557);
  for (let offset = view.byteLength - 22; offset >= minimum; offset -= 1) {
    if (readU32(view, offset) === END_SIGNATURE) {
      return offset;
    }
  }
  throw new ArchiveIntegrityMismatch('ZIP', 'missing end-of-central-directory');
}

export function decodeZip(archive: Uint8Array): Map<string, Uint8Array> {
  if (archive.byteLength > MAX_ARCHIVE_BYTES) {
    throw new ArchiveIntegrityMismatch('ZIP', 'archive exceeds 512 MiB limit');
  }
  const view = new DataView(
    archive.buffer,
    archive.byteOffset,
    archive.byteLength,
  );
  const endOffset = findEnd(view);
  const disk = readU16(view, endOffset + 4);
  const centralDisk = readU16(view, endOffset + 6);
  const entryCount = readU16(view, endOffset + 10);
  const centralSize = readU32(view, endOffset + 12);
  const centralOffset = readU32(view, endOffset + 16);
  const commentLength = readU16(view, endOffset + 20);
  if (
    disk !== 0 ||
    centralDisk !== 0 ||
    entryCount > MAX_ENTRY_COUNT ||
    endOffset + 22 + commentLength !== archive.byteLength ||
    centralOffset + centralSize !== endOffset
  ) {
    throw new ArchiveIntegrityMismatch('ZIP', 'invalid ZIP32 directory');
  }

  const output = new Map<string, Uint8Array>();
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (readU32(view, offset) !== CENTRAL_FILE_SIGNATURE) {
      throw new ArchiveIntegrityMismatch('ZIP', 'invalid central entry');
    }
    const flags = readU16(view, offset + 8);
    const method = readU16(view, offset + 10);
    const checksum = readU32(view, offset + 16);
    const compressedSize = readU32(view, offset + 20);
    const uncompressedSize = readU32(view, offset + 24);
    const nameLength = readU16(view, offset + 28);
    const extraLength = readU16(view, offset + 30);
    const commentSize = readU16(view, offset + 32);
    const localHeaderOffset = readU32(view, offset + 42);
    const nameStart = offset + 46;
    const nameEnd = nameStart + nameLength;
    if (nameEnd > archive.byteLength) {
      throw new ArchiveIntegrityMismatch('ZIP', 'truncated entry name');
    }
    const path = decodeText(archive.slice(nameStart, nameEnd));
    assertSafeArchivePath(path);
    if (output.has(path)) {
      throw new ArchiveIntegrityMismatch(path, 'duplicate ZIP path');
    }
    if ((flags & 0x0001) !== 0 || method !== 0) {
      throw new ArchiveIntegrityMismatch(
        path,
        'only unencrypted stored ZIP entries are supported',
      );
    }
    if (compressedSize !== uncompressedSize) {
      throw new ArchiveIntegrityMismatch(path, 'invalid stored entry sizes');
    }
    if (readU32(view, localHeaderOffset) !== LOCAL_FILE_SIGNATURE) {
      throw new ArchiveIntegrityMismatch(path, 'missing local entry header');
    }
    const localNameLength = readU16(view, localHeaderOffset + 26, path);
    const localExtraLength = readU16(view, localHeaderOffset + 28, path);
    const dataStart =
      localHeaderOffset + 30 + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > centralOffset) {
      throw new ArchiveIntegrityMismatch(path, 'entry data exceeds bounds');
    }
    const bytes = archive.slice(dataStart, dataEnd);
    if (crc32(bytes) !== checksum) {
      throw new ArchiveIntegrityMismatch(path, 'CRC32 mismatch');
    }
    output.set(path, bytes);
    offset = nameEnd + extraLength + commentSize;
  }
  if (offset !== endOffset) {
    throw new ArchiveIntegrityMismatch(
      'ZIP',
      'central directory size mismatch',
    );
  }
  return output;
}
