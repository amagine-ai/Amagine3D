import {
  cadWorkflowKindSchema,
  researchPacketSchema,
  type CadWorkflowState,
} from '@amagine3d/cad-protocol';
import {
  ToolLoopAgent,
  isStepCount,
  type LanguageModel,
  type StepResult,
} from 'ai';
import { z } from 'zod';

import {
  createCadTools,
  type CadToolImplementations,
  type CadTools,
} from '../tool-contracts';
import {
  assembleCadInstructions,
  type VerifiedWorkflowInstructions,
} from './workflow-instructions';

export const cadAgentCallOptionsSchema = z.object({
  runId: z.string().trim().min(1).max(160),
  workflowKind: cadWorkflowKindSchema,
  phase: z.enum([
    'briefing',
    'coding',
    'building',
    'qa',
    'visual_review_waiting',
    'visual_review',
    'ready_to_finish',
    'completed',
    'failed',
    'cancelled',
  ]),
  userRequest: z.string().trim().min(1).max(8_000),
  research: researchPacketSchema.optional(),
  visualReviewConsent: z.enum(['approved', 'declined']),
  modelProfileId: z.string().trim().min(1).max(160).optional(),
  modificationContext: z
    .object({
      source: z.string().min(1).max(2_000_000),
      designBrief: z.unknown(),
      parameterSchema: z.unknown(),
      latestQa: z.unknown().optional(),
    })
    .optional(),
});
export type CadAgentCallOptions = z.infer<typeof cadAgentCallOptionsSchema>;

type AgentPhase = CadAgentCallOptions['phase'];

function toolPolicy(
  phase: AgentPhase,
  visualReviewConsent: CadAgentCallOptions['visualReviewConsent'],
): {
  activeTools: Array<keyof CadTools>;
  toolChoice: 'auto' | { type: 'tool'; toolName: keyof CadTools };
} {
  switch (phase) {
    case 'briefing':
      return {
        activeTools: ['saveDesignBrief'],
        toolChoice: { type: 'tool', toolName: 'saveDesignBrief' },
      };
    case 'coding':
      return {
        activeTools: ['writeCadSource'],
        toolChoice: { type: 'tool', toolName: 'writeCadSource' },
      };
    case 'building':
      return {
        activeTools: ['buildAndCheck'],
        toolChoice: { type: 'tool', toolName: 'buildAndCheck' },
      };
    case 'visual_review_waiting':
      return visualReviewConsent === 'approved'
        ? {
            activeTools: ['requestVisualReview'],
            toolChoice: { type: 'tool', toolName: 'requestVisualReview' },
          }
        : { activeTools: [], toolChoice: 'auto' };
    case 'ready_to_finish':
      return {
        activeTools: ['finishCadRun'],
        toolChoice: { type: 'tool', toolName: 'finishCadRun' },
      };
    case 'qa':
    case 'visual_review':
    case 'completed':
    case 'failed':
    case 'cancelled':
      return { activeTools: [], toolChoice: 'auto' };
  }
}

export type CreateCadAgentOptions = {
  model: LanguageModel;
  instructions: VerifiedWorkflowInstructions;
  workflowKind: CadAgentCallOptions['workflowKind'];
  implementations?: Partial<CadToolImplementations>;
  resolvePhase?: (input: {
    options: CadAgentCallOptions;
    steps: Array<StepResult<CadTools>>;
  }) => AgentPhase;
};

export function createCadAgent(options: CreateCadAgentOptions) {
  const tools = createCadTools(options.workflowKind, options.implementations);
  return new ToolLoopAgent({
    id: `amagine3d-${options.workflowKind}`,
    model: options.model,
    tools,
    callOptionsSchema: cadAgentCallOptionsSchema,
    stopWhen: isStepCount(20),
    maxOutputTokens: 8_000,
    ...(process.env.NODE_ENV === 'development'
      ? { include: { requestBody: true as const } }
      : {}),
    prepareCall: ({ options: rawOptions, ...settings }) => {
      const callOptions = cadAgentCallOptionsSchema.parse(rawOptions);
      if (callOptions.workflowKind !== options.workflowKind) {
        throw new Error('Agent workflow does not match its verified skill.');
      }
      const policy = toolPolicy(
        callOptions.phase,
        callOptions.visualReviewConsent,
      );
      return {
        ...settings,
        instructions: assembleCadInstructions({
          runId: callOptions.runId,
          workflowKind: callOptions.workflowKind,
          userRequest: callOptions.userRequest,
          instructions: options.instructions,
          ...(callOptions.research === undefined
            ? {}
            : { research: callOptions.research }),
          ...(callOptions.modificationContext === undefined
            ? {}
            : { modificationContext: callOptions.modificationContext }),
        }),
        activeTools: policy.activeTools,
        toolChoice: policy.toolChoice,
        prepareStep: ({ steps }) => {
          const phase =
            options.resolvePhase?.({ options: callOptions, steps }) ??
            callOptions.phase;
          return toolPolicy(phase, callOptions.visualReviewConsent);
        },
      };
    },
  });
}

export type CadAgent = ReturnType<typeof createCadAgent>;

export function coordinatorPhase(state: CadWorkflowState): AgentPhase {
  if (
    state.status === 'received' ||
    state.status === 'researching' ||
    state.status === 'research_ready' ||
    state.status === 'research_skipped' ||
    state.status === 'research_failed' ||
    state.status === 'selecting_workflow' ||
    state.status === 'workflow_selected'
  ) {
    throw new Error(`State ${state.status} is not ready for CadAgent.`);
  }
  return state.status;
}
