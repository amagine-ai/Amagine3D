import {
  ACCEPTED_IMAGE_TYPES,
  MAX_IMAGE_BYTES,
  MAX_IMAGE_COUNT,
  MAX_TOTAL_IMAGE_BYTES,
  type ImageAttachment,
} from '../src/types.ts';

export { parseModelSpec, type ModelSpec } from '@amagine3d/a3d-runtime';

export interface ChatRequest {
  images?: ImageAttachment[];
  message: string;
  sessionId: string;
  webSearchEnabled?: boolean;
}

const acceptedImageTypes = new Set<string>(ACCEPTED_IMAGE_TYPES);

function base64ByteLength(value: string): number {
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
  return (value.length * 3) / 4 - padding;
}

function isImageAttachment(value: unknown): value is ImageAttachment {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ImageAttachment>;
  if (
    typeof candidate.name !== 'string' ||
    candidate.name.length === 0 ||
    candidate.name.length > 255 ||
    typeof candidate.mimeType !== 'string' ||
    !acceptedImageTypes.has(candidate.mimeType) ||
    typeof candidate.data !== 'string' ||
    candidate.data.length === 0 ||
    candidate.data.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]*={0,2}$/.test(candidate.data)
  ) {
    return false;
  }
  return base64ByteLength(candidate.data) <= MAX_IMAGE_BYTES;
}

export function isChatRequest(value: unknown): value is ChatRequest {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ChatRequest>;
  const images = candidate.images ?? [];
  return (
    typeof candidate.message === 'string' &&
    candidate.message.length <= 20_000 &&
    Array.isArray(images) &&
    images.length <= MAX_IMAGE_COUNT &&
    images.every(isImageAttachment) &&
    images.reduce((total, image) => total + base64ByteLength(image.data), 0) <=
      MAX_TOTAL_IMAGE_BYTES &&
    (candidate.message.trim().length > 0 || images.length > 0) &&
    (candidate.webSearchEnabled === undefined ||
      typeof candidate.webSearchEnabled === 'boolean') &&
    typeof candidate.sessionId === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      candidate.sessionId,
    )
  );
}
