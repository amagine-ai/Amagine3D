import { z } from 'zod';

const nonBlankStringSchema = z.string().trim().min(1);
const modelIdSchema = nonBlankStringSchema
  .max(256)
  .regex(/^\S+$/u, 'Model IDs must not contain whitespace.');
const optionalEnvironmentValue = <Schema extends z.ZodType>(schema: Schema) =>
  z.preprocess(
    (value) =>
      typeof value === 'string' && value.trim().length === 0
        ? undefined
        : value,
    schema.optional(),
  );

const environmentSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']),
  AMAGINE3D_MODEL_GATEWAY_API_KEY: nonBlankStringSchema,
  AMAGINE3D_MODEL_GATEWAY_BASE_URL: z.httpUrl(),
  AMAGINE3D_CAD_MODEL: modelIdSchema,
  AMAGINE3D_WEB_SEARCH_MODEL: optionalEnvironmentValue(modelIdSchema),
  TAVILY_API_KEY: optionalEnvironmentValue(nonBlankStringSchema),
  TAVILY_BASE_URL: optionalEnvironmentValue(z.httpUrl()),
});

const environment = environmentSchema.parse({
  NODE_ENV: process.env.NODE_ENV,
  AMAGINE3D_MODEL_GATEWAY_API_KEY: process.env.AMAGINE3D_MODEL_GATEWAY_API_KEY,
  AMAGINE3D_MODEL_GATEWAY_BASE_URL:
    process.env.AMAGINE3D_MODEL_GATEWAY_BASE_URL,
  AMAGINE3D_CAD_MODEL: process.env.AMAGINE3D_CAD_MODEL,
  AMAGINE3D_WEB_SEARCH_MODEL: process.env.AMAGINE3D_WEB_SEARCH_MODEL,
  TAVILY_API_KEY: process.env.TAVILY_API_KEY,
  TAVILY_BASE_URL: process.env.TAVILY_BASE_URL,
});

export const config = {
  nodeEnv: environment.NODE_ENV,
  cadModelEnvironment: {
    AMAGINE3D_MODEL_GATEWAY_API_KEY:
      environment.AMAGINE3D_MODEL_GATEWAY_API_KEY,
    AMAGINE3D_MODEL_GATEWAY_BASE_URL:
      environment.AMAGINE3D_MODEL_GATEWAY_BASE_URL,
    AMAGINE3D_CAD_MODEL: environment.AMAGINE3D_CAD_MODEL,
  },
  researchEnvironment: {
    AMAGINE3D_MODEL_GATEWAY_API_KEY:
      environment.AMAGINE3D_MODEL_GATEWAY_API_KEY,
    AMAGINE3D_MODEL_GATEWAY_BASE_URL:
      environment.AMAGINE3D_MODEL_GATEWAY_BASE_URL,
    ...(environment.AMAGINE3D_WEB_SEARCH_MODEL === undefined
      ? {}
      : {
          AMAGINE3D_WEB_SEARCH_MODEL: environment.AMAGINE3D_WEB_SEARCH_MODEL,
        }),
    ...(environment.TAVILY_API_KEY === undefined
      ? {}
      : { TAVILY_API_KEY: environment.TAVILY_API_KEY }),
    ...(environment.TAVILY_BASE_URL === undefined
      ? {}
      : { TAVILY_BASE_URL: environment.TAVILY_BASE_URL }),
  },
} as const;
