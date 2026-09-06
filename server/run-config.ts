export const MAX_TIMER_DELAY_MS = 2_147_483_647;
export const DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS = 1_800_000;
export const DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS = 7_200_000;
export const DEFAULT_FIRST_BUILD_REMINDER_MS = 600_000;

export interface AgentRunTimeouts {
  hardTimeoutMs: number;
  idleTimeoutMs: number;
}

interface AgentRunTimeoutEnvironment {
  AGENT_RUN_HARD_TIMEOUT_MS?: string;
  AGENT_RUN_IDLE_TIMEOUT_MS?: string;
}

function readDuration(
  environment: AgentRunTimeoutEnvironment,
  name: keyof AgentRunTimeoutEnvironment,
  fallback: number,
): number {
  const rawValue = environment[name];
  if (rawValue === undefined) return fallback;
  if (!/^(0|[1-9][0-9]*)$/u.test(rawValue)) {
    throw new RangeError(
      `${name} must be an integer from 0 to ${MAX_TIMER_DELAY_MS} milliseconds.`,
    );
  }
  const value = Number(rawValue);
  if (!Number.isSafeInteger(value) || value > MAX_TIMER_DELAY_MS) {
    throw new RangeError(
      `${name} must be an integer from 0 to ${MAX_TIMER_DELAY_MS} milliseconds.`,
    );
  }
  return value;
}

export function agentRunTimeoutsFromEnv(
  environment: AgentRunTimeoutEnvironment = {
    AGENT_RUN_HARD_TIMEOUT_MS: process.env.AGENT_RUN_HARD_TIMEOUT_MS,
    AGENT_RUN_IDLE_TIMEOUT_MS: process.env.AGENT_RUN_IDLE_TIMEOUT_MS,
  },
): AgentRunTimeouts {
  return {
    hardTimeoutMs: readDuration(
      environment,
      'AGENT_RUN_HARD_TIMEOUT_MS',
      DEFAULT_AGENT_RUN_HARD_TIMEOUT_MS,
    ),
    idleTimeoutMs: readDuration(
      environment,
      'AGENT_RUN_IDLE_TIMEOUT_MS',
      DEFAULT_AGENT_RUN_IDLE_TIMEOUT_MS,
    ),
  };
}
