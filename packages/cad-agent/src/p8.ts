import {
  CadDomainError,
  modelProfileSchema,
  type ModelCapabilities,
  type ModelProfile,
  type ModelProfileSettings,
  type ModelProfileSnapshot,
} from '@amagine3d/cad-protocol';

export const IMAGE_ATTACHMENT_LIMITS = {
  maxCount: 4,
  maxBytesPerFile: 8 * 1024 * 1024,
  maxTotalBytes: 20 * 1024 * 1024,
  maxPixels: 24_000_000,
} as const;

const IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
]);

export type ImageInput = {
  fileName: string;
  mediaType: string;
  bytes: Uint8Array;
};

function invalid(message: string): never {
  throw new CadDomainError('InvalidExternalData', message, {
    category: 'protocol',
    retryable: false,
    operation: 'image-attachment-validation',
  });
}

function readU32(bytes: Uint8Array, offset: number): number {
  return (
    ((bytes[offset] ?? 0) << 24) |
    ((bytes[offset + 1] ?? 0) << 16) |
    ((bytes[offset + 2] ?? 0) << 8) |
    (bytes[offset + 3] ?? 0)
  );
}

function byte(bytes: Uint8Array, offset: number): number {
  return bytes[offset] ?? 0;
}

function dimensions(input: ImageInput): { width: number; height: number } {
  const b = input.bytes;
  if (
    input.mediaType === 'image/png' &&
    b.length >= 24 &&
    b
      .slice(0, 8)
      .every(
        (value, index) => value === [137, 80, 78, 71, 13, 10, 26, 10][index],
      )
  ) {
    return { width: readU32(b, 16), height: readU32(b, 20) };
  }
  if (
    input.mediaType === 'image/gif' &&
    b.length >= 10 &&
    new TextDecoder().decode(b.slice(0, 6)) in { GIF87a: 1, GIF89a: 1 }
  ) {
    return {
      width: byte(b, 6) | (byte(b, 7) << 8),
      height: byte(b, 8) | (byte(b, 9) << 8),
    };
  }
  if (
    input.mediaType === 'image/webp' &&
    b.length >= 30 &&
    new TextDecoder().decode(b.slice(0, 4)) === 'RIFF' &&
    new TextDecoder().decode(b.slice(8, 12)) === 'WEBP'
  ) {
    const kind = new TextDecoder().decode(b.slice(12, 16));
    if (kind === 'VP8X')
      return {
        width: 1 + byte(b, 24) + (byte(b, 25) << 8) + (byte(b, 26) << 16),
        height: 1 + byte(b, 27) + (byte(b, 28) << 8) + (byte(b, 29) << 16),
      };
  }
  if (
    input.mediaType === 'image/jpeg' &&
    b.length >= 4 &&
    b[0] === 0xff &&
    b[1] === 0xd8
  ) {
    let offset = 2;
    while (offset + 9 < b.length) {
      if (b[offset] !== 0xff) {
        offset += 1;
        continue;
      }
      const marker = byte(b, offset + 1);
      const length = (byte(b, offset + 2) << 8) | byte(b, offset + 3);
      if (marker >= 0xc0 && marker <= 0xc3 && length >= 7) {
        return {
          height: (byte(b, offset + 5) << 8) | byte(b, offset + 6),
          width: (byte(b, offset + 7) << 8) | byte(b, offset + 8),
        };
      }
      offset += 2 + length;
    }
  }
  invalid(
    `The ${input.fileName} image header is invalid or dimensions are unavailable.`,
  );
}

export function validateImageInputs(
  inputs: readonly ImageInput[],
): Array<{ input: ImageInput; width: number; height: number }> {
  if (inputs.length === 0) return [];
  if (inputs.length > IMAGE_ATTACHMENT_LIMITS.maxCount)
    invalid(
      `At most ${IMAGE_ATTACHMENT_LIMITS.maxCount} images may be attached.`,
    );
  const total = inputs.reduce((sum, input) => sum + input.bytes.byteLength, 0);
  if (total > IMAGE_ATTACHMENT_LIMITS.maxTotalBytes)
    invalid('The total image attachment size is too large.');
  return inputs.map((input) => {
    if (!IMAGE_TYPES.has(input.mediaType))
      invalid(`${input.mediaType} is not an allowed image type.`);
    if (
      input.bytes.byteLength === 0 ||
      input.bytes.byteLength > IMAGE_ATTACHMENT_LIMITS.maxBytesPerFile
    )
      invalid(`${input.fileName} exceeds the per-image size limit.`);
    const { width, height } = dimensions(input);
    if (
      width <= 0 ||
      height <= 0 ||
      width * height > IMAGE_ATTACHMENT_LIMITS.maxPixels
    )
      invalid(`${input.fileName} exceeds the pixel limit.`);
    return { input, width, height };
  });
}

export function assertModelUsable(
  profile: ModelProfile,
  needsImage = false,
): void {
  modelProfileSchema.parse(profile);
  if (!profile.enabled)
    throw new Error(`Model profile ${profile.id} is disabled.`);
  if (profile.validation.status !== 'valid')
    throw new Error(
      `Model profile ${profile.displayName} has not passed validation.`,
    );
  if (!profile.capabilities.textInput || !profile.capabilities.toolCalling)
    throw new Error(
      `Model profile ${profile.displayName} cannot run CAD tools.`,
    );
  if (needsImage && !profile.capabilities.imageInput)
    throw new Error(
      `Model profile ${profile.displayName} does not support image input.`,
    );
}

export function resolveDefaultModelProfile(
  settings: ModelProfileSettings | undefined,
  envModelId: string | undefined,
): ModelProfile | undefined {
  const profiles = settings?.profiles ?? [];
  const preferred =
    settings?.defaultProfileId === null ||
    settings?.defaultProfileId === undefined
      ? undefined
      : profiles.find((profile) => profile.id === settings.defaultProfileId);
  if (preferred?.enabled && preferred.validation.status === 'valid')
    return preferred;
  const env = envModelId?.trim();
  return profiles.find(
    (profile) =>
      profile.enabled &&
      profile.validation.status === 'valid' &&
      profile.modelId === env,
  );
}

export function freezeModelProfile(
  profile: ModelProfile,
): ModelProfileSnapshot {
  assertModelUsable(profile);
  return {
    profileId: profile.id,
    profileRevision: profile.revision,
    provider: profile.provider,
    modelId: profile.modelId,
    capabilities: profile.capabilities,
  };
}

export function upsertModelProfile(
  settings: ModelProfileSettings,
  profile: ModelProfile,
): ModelProfileSettings {
  modelProfileSchema.parse(profile);
  const existing = settings.profiles.find((item) => item.id === profile.id);
  const next =
    existing === undefined
      ? profile
      : { ...profile, revision: existing.revision + 1 };
  return {
    ...settings,
    profiles: [
      ...settings.profiles.filter((item) => item.id !== profile.id),
      next,
    ],
  };
}

export function toFileUIParts(
  files: readonly { name: string; type: string; bytes: Uint8Array }[],
): Array<{ type: 'file'; mediaType: string; filename: string; url: string }> {
  const base64 = (bytes: Uint8Array): string => {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return globalThis.btoa(binary);
  };
  return validateImageInputs(
    files.map((file) => ({
      fileName: file.name,
      mediaType: file.type,
      bytes: file.bytes,
    })),
  ).map(({ input }) => ({
    type: 'file' as const,
    mediaType: input.mediaType,
    filename: input.fileName,
    url: `data:${input.mediaType};base64,${base64(input.bytes)}`,
  }));
}

export function capabilitiesFromFlags(
  flags: Partial<ModelCapabilities>,
): ModelCapabilities {
  return {
    textInput: false,
    imageInput: false,
    toolCalling: false,
    reasoning: false,
    ...flags,
  };
}

export type ModelSmokeRunner = {
  text: () => Promise<void>;
  toolCalling: () => Promise<void>;
  image?: () => Promise<void>;
  sdkVersion: string;
};

export async function validateModelCapabilities(
  capabilities: ModelCapabilities,
  runner: ModelSmokeRunner,
): Promise<{
  status: 'valid' | 'failed';
  validatedAt: string;
  reason: string | null;
  sdkVersion: string;
}> {
  try {
    await runner.text();
    if (capabilities.toolCalling) await runner.toolCalling();
    if (capabilities.imageInput) {
      if (runner.image === undefined)
        throw new Error('Image smoke runner is unavailable.');
      await runner.image();
    }
    return {
      status: 'valid',
      validatedAt: new Date().toISOString(),
      reason: null,
      sdkVersion: runner.sdkVersion,
    };
  } catch (error) {
    return {
      status: 'failed',
      validatedAt: new Date().toISOString(),
      reason:
        error instanceof Error
          ? error.message.slice(0, 2_000)
          : 'Model capability smoke failed.',
      sdkVersion: runner.sdkVersion,
    };
  }
}
