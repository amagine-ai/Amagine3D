import { z } from 'zod';

import { CadDomainError } from './errors';
import {
  SCHEMA_VERSION,
  workflowSelectionSchema,
  type WorkflowSelection,
} from './schemas';

const stateBaseSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  runId: z.string().trim().min(1),
});

const unfrozenState = <Status extends string>(status: Status) =>
  stateBaseSchema.extend({
    status: z.literal(status),
    workflowFrozen: z.literal(false),
  });

const frozenState = <Status extends string>(status: Status) =>
  stateBaseSchema.extend({
    status: z.literal(status),
    workflowFrozen: z.literal(true),
    selection: workflowSelectionSchema,
  });

export const cadWorkflowStateSchema = z.discriminatedUnion('status', [
  unfrozenState('received'),
  unfrozenState('researching'),
  unfrozenState('research_ready'),
  unfrozenState('research_skipped'),
  unfrozenState('research_failed'),
  unfrozenState('selecting_workflow'),
  unfrozenState('workflow_selected').extend({
    selection: workflowSelectionSchema,
  }),
  frozenState('briefing'),
  frozenState('coding'),
  frozenState('building'),
  frozenState('qa'),
  frozenState('visual_review_waiting').extend({
    artifactId: z.string().min(1),
  }),
  frozenState('visual_review').extend({ artifactId: z.string().min(1) }),
  frozenState('ready_to_finish').extend({ artifactId: z.string().min(1) }),
  frozenState('completed').extend({ artifactId: z.string().min(1) }),
  stateBaseSchema.extend({
    status: z.literal('failed'),
    workflowFrozen: z.boolean(),
    selection: workflowSelectionSchema.optional(),
    reason: z.string().min(1),
    lastArtifactId: z.string().min(1).optional(),
  }),
  stateBaseSchema.extend({
    status: z.literal('cancelled'),
    workflowFrozen: z.boolean(),
    selection: workflowSelectionSchema.optional(),
    reason: z.string().min(1),
    lastArtifactId: z.string().min(1).optional(),
  }),
]);
export type CadWorkflowState = z.infer<typeof cadWorkflowStateSchema>;

export const cadWorkflowTransitionEventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('begin_research') }),
  z.object({ type: z.literal('skip_research') }),
  z.object({ type: z.literal('research_succeeded') }),
  z.object({ type: z.literal('research_failed') }),
  z.object({ type: z.literal('begin_workflow_selection') }),
  z.object({
    type: z.literal('select_workflow'),
    selection: workflowSelectionSchema,
  }),
  z.object({
    type: z.literal('change_workflow'),
    selection: workflowSelectionSchema,
  }),
  z.object({ type: z.literal('start_briefing') }),
  z.object({ type: z.literal('brief_saved') }),
  z.object({ type: z.literal('source_written') }),
  z.object({ type: z.literal('build_succeeded') }),
  z.object({ type: z.literal('build_failed') }),
  z.object({
    type: z.literal('qa_passed'),
    artifactId: z.string().min(1),
    visualReviewRequired: z.boolean(),
  }),
  z.object({ type: z.literal('qa_failed') }),
  z.object({ type: z.literal('start_visual_review') }),
  z.object({ type: z.literal('visual_review_passed') }),
  z.object({ type: z.literal('visual_review_rejected') }),
  z.object({
    type: z.literal('finish'),
    artifactId: z.string().min(1),
  }),
  z.object({
    type: z.literal('fail'),
    reason: z.string().min(1),
    lastArtifactId: z.string().min(1).optional(),
  }),
  z.object({
    type: z.literal('cancel'),
    reason: z.string().min(1),
    lastArtifactId: z.string().min(1).optional(),
  }),
]);
export type CadWorkflowTransitionEvent = z.infer<
  typeof cadWorkflowTransitionEventSchema
>;

export function createCadWorkflowState(runId: string): CadWorkflowState {
  return cadWorkflowStateSchema.parse({
    schemaVersion: SCHEMA_VERSION,
    runId,
    status: 'received',
    workflowFrozen: false,
  });
}

function illegalTransition(
  state: CadWorkflowState,
  event: CadWorkflowTransitionEvent,
): never {
  const frozenSwitch = event.type === 'change_workflow' && state.workflowFrozen;

  throw new CadDomainError(
    frozenSwitch ? 'WorkflowFrozen' : 'IllegalWorkflowTransition',
    frozenSwitch
      ? `Workflow selection is frozen in state ${state.status}. Start a new run to switch workflows.`
      : `Event ${event.type} is not allowed from state ${state.status}.`,
    {
      category: 'workflow',
      retryable: false,
      operation: 'transitionCadWorkflow',
      details: { state: state.status, event: event.type },
    },
  );
}

function terminalState(
  state: CadWorkflowState,
  event: Extract<CadWorkflowTransitionEvent, { type: 'cancel' | 'fail' }>,
): CadWorkflowState {
  const base = {
    schemaVersion: SCHEMA_VERSION,
    runId: state.runId,
    status:
      event.type === 'fail' ? ('failed' as const) : ('cancelled' as const),
    workflowFrozen: state.workflowFrozen,
    reason: event.reason,
    ...(event.lastArtifactId === undefined
      ? {}
      : { lastArtifactId: event.lastArtifactId }),
  };

  return 'selection' in state ? { ...base, selection: state.selection } : base;
}

export function transitionCadWorkflow(
  inputState: CadWorkflowState,
  inputEvent: CadWorkflowTransitionEvent,
): CadWorkflowState {
  const state = cadWorkflowStateSchema.parse(inputState);
  const event = cadWorkflowTransitionEventSchema.parse(inputEvent);

  if (
    state.status === 'completed' ||
    state.status === 'failed' ||
    state.status === 'cancelled'
  ) {
    return illegalTransition(state, event);
  }

  if (event.type === 'fail' || event.type === 'cancel') {
    return terminalState(state, event);
  }

  switch (state.status) {
    case 'received':
      if (event.type === 'begin_research') {
        return { ...state, status: 'researching' };
      }
      if (event.type === 'skip_research') {
        return { ...state, status: 'research_skipped' };
      }
      break;
    case 'researching':
      if (event.type === 'research_succeeded') {
        return { ...state, status: 'research_ready' };
      }
      if (event.type === 'research_failed') {
        return { ...state, status: 'research_failed' };
      }
      break;
    case 'research_ready':
    case 'research_skipped':
    case 'research_failed':
      if (event.type === 'begin_workflow_selection') {
        return { ...state, status: 'selecting_workflow' };
      }
      break;
    case 'selecting_workflow':
      if (event.type === 'select_workflow') {
        return {
          ...state,
          status: 'workflow_selected',
          selection: event.selection,
        };
      }
      break;
    case 'workflow_selected':
      if (event.type === 'change_workflow') {
        return { ...state, selection: event.selection };
      }
      if (event.type === 'start_briefing') {
        return {
          ...state,
          status: 'briefing',
          workflowFrozen: true,
        };
      }
      break;
    case 'briefing':
      if (event.type === 'brief_saved') {
        return { ...state, status: 'coding' };
      }
      break;
    case 'coding':
      if (event.type === 'source_written') {
        return { ...state, status: 'building' };
      }
      break;
    case 'building':
      if (event.type === 'build_succeeded') {
        return { ...state, status: 'qa' };
      }
      if (event.type === 'build_failed') {
        return { ...state, status: 'coding' };
      }
      break;
    case 'qa':
      if (event.type === 'qa_failed') {
        return { ...state, status: 'coding' };
      }
      if (event.type === 'qa_passed') {
        return event.visualReviewRequired
          ? {
              ...state,
              status: 'visual_review_waiting',
              artifactId: event.artifactId,
            }
          : {
              ...state,
              status: 'ready_to_finish',
              artifactId: event.artifactId,
            };
      }
      break;
    case 'visual_review_waiting':
      if (event.type === 'start_visual_review') {
        return { ...state, status: 'visual_review' };
      }
      break;
    case 'visual_review':
      if (event.type === 'visual_review_passed') {
        return { ...state, status: 'ready_to_finish' };
      }
      if (event.type === 'visual_review_rejected') {
        return {
          schemaVersion: state.schemaVersion,
          runId: state.runId,
          status: 'coding',
          workflowFrozen: true,
          selection: state.selection,
        };
      }
      break;
    case 'ready_to_finish':
      if (event.type === 'finish') {
        return { ...state, status: 'completed', artifactId: event.artifactId };
      }
      break;
  }

  return illegalTransition(state, event);
}

export function isWorkflowFrozen(state: CadWorkflowState): boolean {
  return state.workflowFrozen;
}

export function getWorkflowSelection(
  state: CadWorkflowState,
): WorkflowSelection | undefined {
  return 'selection' in state ? state.selection : undefined;
}
