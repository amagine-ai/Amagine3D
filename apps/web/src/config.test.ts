import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const validEnvironment = {
  NODE_ENV: 'test',
  AMAGINE3D_MODEL_GATEWAY_API_KEY: 'gateway-key',
  AMAGINE3D_MODEL_GATEWAY_BASE_URL: 'https://gateway.example.com/v1',
  AMAGINE3D_CAD_MODEL: 'cad-model',
  AMAGINE3D_WEB_SEARCH_MODEL: 'research-model',
  TAVILY_API_KEY: 'tavily-key',
  TAVILY_BASE_URL: 'https://api.tavily.com',
} as const;

describe('server configuration', () => {
  beforeEach(() => {
    vi.resetModules();
    for (const [name, value] of Object.entries(validEnvironment)) {
      vi.stubEnv(name, value);
    }
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('loads validated CAD and research settings', async () => {
    const { config } = await import('./config');

    expect(config).toEqual({
      nodeEnv: 'test',
      cadModelEnvironment: {
        AMAGINE3D_MODEL_GATEWAY_API_KEY: 'gateway-key',
        AMAGINE3D_MODEL_GATEWAY_BASE_URL: 'https://gateway.example.com/v1',
        AMAGINE3D_CAD_MODEL: 'cad-model',
      },
      researchEnvironment: {
        AMAGINE3D_MODEL_GATEWAY_API_KEY: 'gateway-key',
        AMAGINE3D_MODEL_GATEWAY_BASE_URL: 'https://gateway.example.com/v1',
        AMAGINE3D_WEB_SEARCH_MODEL: 'research-model',
        TAVILY_API_KEY: 'tavily-key',
        TAVILY_BASE_URL: 'https://api.tavily.com',
      },
    });
  });

  it('treats blank optional research settings as unset', async () => {
    vi.stubEnv('AMAGINE3D_WEB_SEARCH_MODEL', '  ');
    vi.stubEnv('TAVILY_API_KEY', '');
    vi.stubEnv('TAVILY_BASE_URL', '  ');

    const { config } = await import('./config');

    expect(config.researchEnvironment).toEqual({
      AMAGINE3D_MODEL_GATEWAY_API_KEY: 'gateway-key',
      AMAGINE3D_MODEL_GATEWAY_BASE_URL: 'https://gateway.example.com/v1',
    });
  });

  it('rejects invalid gateway URLs', async () => {
    vi.stubEnv('AMAGINE3D_MODEL_GATEWAY_BASE_URL', 'not-a-url');

    await expect(import('./config')).rejects.toThrow();
  });
});
