import { describe, expect, it } from 'vitest';

import {
  IMAGE_ATTACHMENT_LIMITS,
  assertModelUsable,
  freezeModelProfile,
  resolveDefaultModelProfile,
  toFileUIParts,
  validateModelCapabilities,
  validateImageInputs,
} from './p8';

const profile = {
  schemaVersion: 1 as const,
  id: 'profile-1',
  revision: 1,
  displayName: 'Vision CAD',
  connectionId: 'gateway',
  provider: 'openai-compatible',
  modelId: 'vision-model',
  defaultParameters: {},
  capabilities: {
    textInput: true,
    imageInput: true,
    toolCalling: true,
    reasoning: true,
  },
  enabled: true,
  validation: {
    status: 'valid' as const,
    validatedAt: '2026-08-15T00:00:00.000Z',
    reason: null,
    sdkVersion: '4.0.65',
  },
};

const png = new Uint8Array([
  137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 2, 0,
  0, 0, 3,
]);

describe('P8 model and attachment contracts', () => {
  it('prefers the saved web default over the env fallback', () => {
    const other = { ...profile, id: 'profile-2', modelId: 'env-model' };
    expect(
      resolveDefaultModelProfile(
        {
          schemaVersion: 1,
          defaultProfileId: 'profile-1',
          profiles: [profile, other],
        },
        'env-model',
      )?.id,
    ).toBe('profile-1');
    expect(
      resolveDefaultModelProfile(
        {
          schemaVersion: 1,
          defaultProfileId: null,
          profiles: [profile, other],
        },
        'env-model',
      )?.id,
    ).toBe('profile-2');
  });

  it('freezes a validated capability snapshot and gates image use', () => {
    assertModelUsable(profile, true);
    expect(freezeModelProfile(profile)).toMatchObject({
      profileId: 'profile-1',
      profileRevision: 1,
    });
    expect(() =>
      assertModelUsable(
        {
          ...profile,
          capabilities: { ...profile.capabilities, imageInput: false },
        },
        true,
      ),
    ).toThrow('image input');
  });

  it('validates image magic bytes, dimensions and converts files to UI parts', () => {
    const [validated] = validateImageInputs([
      { fileName: 'ref.png', mediaType: 'image/png', bytes: png },
    ]);
    expect(validated).toMatchObject({ width: 2, height: 3 });
    expect(
      toFileUIParts([{ name: 'ref.png', type: 'image/png', bytes: png }])[0]
        ?.url,
    ).toMatch(/^data:image\/png;base64,/u);
    expect(() =>
      validateImageInputs([
        {
          fileName: 'fake.png',
          mediaType: 'image/png',
          bytes: new Uint8Array([1, 2, 3]),
        },
      ]),
    ).toThrow('header');
    expect(IMAGE_ATTACHMENT_LIMITS.maxCount).toBe(4);
  });

  it('records capability smoke failures without exposing secrets', async () => {
    const result = await validateModelCapabilities(profile.capabilities, {
      sdkVersion: '4.0.65',
      text: async () => undefined,
      toolCalling: async () => {
        throw new Error('tool endpoint unavailable');
      },
      image: async () => undefined,
    });
    expect(result.status).toBe('failed');
    expect(result.reason).toContain('tool endpoint');
  });
});
