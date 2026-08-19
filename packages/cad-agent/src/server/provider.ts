import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { CadDomainError } from '@amagine3d/cad-protocol';
import type { LanguageModel } from 'ai';

export type CadModelEnvironment = {
  AMAGINE3D_MODEL_GATEWAY_API_KEY?: string;
  AMAGINE3D_MODEL_GATEWAY_BASE_URL?: string;
  AMAGINE3D_CAD_MODEL?: string;
};

function required(
  environment: CadModelEnvironment,
  name: keyof CadModelEnvironment,
): string {
  const value = environment[name]?.trim();
  if (value === undefined || value.length === 0) {
    throw new CadDomainError(
      'InvalidExternalData',
      `${name} is required for the CAD Agent.`,
      {
        category: 'protocol',
        retryable: false,
        operation: 'cad-agent-configuration',
      },
    );
  }
  return value;
}

export function createCadModelFromEnvironment(
  environment: CadModelEnvironment,
  modelIdOverride?: string,
): LanguageModel {
  const apiKey = required(environment, 'AMAGINE3D_MODEL_GATEWAY_API_KEY');
  const baseURL = required(environment, 'AMAGINE3D_MODEL_GATEWAY_BASE_URL');
  const modelId =
    modelIdOverride?.trim() || required(environment, 'AMAGINE3D_CAD_MODEL');
  let url: URL;
  try {
    url = new URL(baseURL);
  } catch {
    throw new CadDomainError(
      'InvalidExternalData',
      'AMAGINE3D_MODEL_GATEWAY_BASE_URL must be an absolute HTTP(S) URL.',
      {
        category: 'protocol',
        retryable: false,
        operation: 'cad-agent-configuration',
      },
    );
  }
  if (!['http:', 'https:'].includes(url.protocol) || /\s/u.test(modelId)) {
    throw new CadDomainError(
      'InvalidExternalData',
      'CAD Agent gateway URL or model ID is invalid.',
      {
        category: 'protocol',
        retryable: false,
        operation: 'cad-agent-configuration',
      },
    );
  }
  const provider = createOpenAICompatible({
    name: 'amagine3d-cad-model-gateway',
    apiKey,
    baseURL: baseURL.replace(/\/+$/u, ''),
  });
  return provider(modelId);
}
