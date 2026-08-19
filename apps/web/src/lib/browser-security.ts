const UNSAFE_FILE_CHARACTERS = /[<>:"/\\|?*]+/gu;

function replaceControlCharacters(value: string): string {
  return [...value]
    .map((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127 ? '-' : character;
    })
    .join('');
}

export function externalHttpUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:'
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export function safeDownloadFileName(
  value: string,
  fallback = 'amagine3d-download',
): string {
  const leaf = value.normalize('NFKC').split(/[\\/]/u).at(-1) ?? '';
  const sanitized = replaceControlCharacters(leaf)
    .replaceAll(UNSAFE_FILE_CHARACTERS, '-')
    .replace(/^\.+/u, '')
    .replace(/\s+/gu, ' ')
    .trim()
    .slice(0, 180)
    .replace(/[. ]+$/u, '');
  return sanitized.length > 0 ? sanitized : fallback;
}
