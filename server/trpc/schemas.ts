import { z } from 'zod';

import { USER_SESSION_ID } from '../../src/session-id.ts';
import { BUNDLED_POMODORO_SESSION_ID } from '../../src/types.ts';
import { MAX_ARCHIVE_FILES } from '../artifact-archive.ts';
import { MAX_TRASH_FILES } from '../artifact-trash.ts';
import { MAX_TRASH_SESSIONS } from '../session-trash.ts';

const userSessionIdSchema = z.string().regex(USER_SESSION_ID);
const sessionIdSchema = z.union([
  z.literal(BUNDLED_POMODORO_SESSION_ID),
  userSessionIdSchema,
]);
const artifactPathSchema = z.string().min(1).max(1_024);

export const sessionInputSchema = z.strictObject({
  sessionId: sessionIdSchema,
});

export const artifactFileInputSchema = z.strictObject({
  path: artifactPathSchema,
  sessionId: userSessionIdSchema,
});

export const bundledArtifactFileInputSchema = z.strictObject({
  path: artifactPathSchema,
});

export const artifactArchiveInputSchema = z.strictObject({
  paths: z.array(artifactPathSchema).min(2).max(MAX_ARCHIVE_FILES),
  sessionId: sessionIdSchema,
});

export const trashArtifactsInputSchema = z.strictObject({
  paths: z.array(artifactPathSchema).min(1).max(MAX_TRASH_FILES),
  sessionId: sessionIdSchema,
});

export const trashStorageInputSchema = z.strictObject({
  sessionIds: z
    .array(userSessionIdSchema)
    .min(1)
    .max(MAX_TRASH_SESSIONS),
});

const parameterValuesSchema = z
  .record(
    z.string().min(1).max(160),
    z.number().finite(),
  )
  .refine((values) => Object.keys(values).length > 0, {
    error: 'At least one parameter value is required.',
  })
  .refine((values) => Object.keys(values).length <= 100, {
    error: 'Too many parameter values.',
  });

export const parameterBuildRequestSchema = z.strictObject({
  primaryPreviewPath: artifactPathSchema,
  sourceHash: z.string().regex(/^[a-f0-9]{64}$/u),
  sourcePath: artifactPathSchema,
  values: parameterValuesSchema,
});

export const rebuildParametersInputSchema = z.strictObject({
  sessionId: sessionIdSchema,
  ...parameterBuildRequestSchema.shape,
});

export type ParameterBuildRequest = z.infer<
  typeof parameterBuildRequestSchema
>;
