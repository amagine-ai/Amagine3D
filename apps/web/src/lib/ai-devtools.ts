import { registerTelemetry } from 'ai';

import { config } from '../config';

let registered = false;

export async function registerCadAgentDevTools(): Promise<void> {
  if (config.nodeEnv !== 'development' || registered) return;
  const { DevToolsTelemetry } = await import('@ai-sdk/devtools');
  registerTelemetry(DevToolsTelemetry());
  registered = true;
}
