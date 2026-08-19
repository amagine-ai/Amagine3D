import { describe, expect, it } from 'vitest';

import {
  createCadWorkflowState,
  getWorkflowSelection,
  isWorkflowFrozen,
  transitionCadWorkflow,
  type CadWorkflowState,
  type CadWorkflowTransitionEvent,
} from './workflow-state';

const singleColorSelection = {
  kind: 'single-color' as const,
  mode: 'automatic' as const,
  reason: 'No multi-color intent was present.',
};

const multiColorSelection = {
  kind: 'multi-color' as const,
  mode: 'user-override' as const,
  reason: 'The user selected a multi-color print.',
};

function apply(
  state: CadWorkflowState,
  events: CadWorkflowTransitionEvent[],
): CadWorkflowState {
  return events.reduce(transitionCadWorkflow, state);
}

const selectSingleColor: CadWorkflowTransitionEvent[] = [
  { type: 'skip_research' },
  { type: 'begin_workflow_selection' },
  { type: 'select_workflow', selection: singleColorSelection },
];

describe('CadWorkflowState', () => {
  it('runs the legal no-research workflow to completion', () => {
    const state = apply(createCadWorkflowState('run-1'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
      { type: 'brief_saved' },
      { type: 'source_written' },
      { type: 'build_succeeded' },
      {
        type: 'qa_passed',
        artifactId: 'artifact-1',
        visualReviewRequired: false,
      },
      { type: 'finish', artifactId: 'artifact-1' },
    ]);

    expect(state).toMatchObject({
      status: 'completed',
      workflowFrozen: true,
      selection: singleColorSelection,
      artifactId: 'artifact-1',
    });
  });

  it.each([
    ['research_ready', { type: 'research_succeeded' } as const],
    ['research_failed', { type: 'research_failed' } as const],
  ])('supports the %s research outcome', (expectedStatus, outcome) => {
    const state = apply(createCadWorkflowState('run-research'), [
      { type: 'begin_research' },
      outcome,
    ]);

    expect(state.status).toBe(expectedStatus);
    expect(
      transitionCadWorkflow(state, { type: 'begin_workflow_selection' }).status,
    ).toBe('selecting_workflow');
  });

  it('allows selection changes before briefing and freezes at briefing', () => {
    const selected = apply(createCadWorkflowState('run-2'), selectSingleColor);
    const changed = transitionCadWorkflow(selected, {
      type: 'change_workflow',
      selection: multiColorSelection,
    });

    expect(getWorkflowSelection(changed)).toEqual(multiColorSelection);
    expect(isWorkflowFrozen(changed)).toBe(false);

    const briefing = transitionCadWorkflow(changed, { type: 'start_briefing' });
    expect(isWorkflowFrozen(briefing)).toBe(true);
    expect(getWorkflowSelection(briefing)).toEqual(multiColorSelection);
  });

  it('rejects a workflow switch once briefing freezes the selection', () => {
    const briefing = apply(createCadWorkflowState('run-3'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
    ]);

    expect(() =>
      transitionCadWorkflow(briefing, {
        type: 'change_workflow',
        selection: multiColorSelection,
      }),
    ).toThrowError(expect.objectContaining({ code: 'WorkflowFrozen' }));
  });

  it('supports deterministic build and QA repair loops', () => {
    const coding = apply(createCadWorkflowState('run-4'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
      { type: 'brief_saved' },
    ]);

    const afterBuildRepair = apply(coding, [
      { type: 'source_written' },
      { type: 'build_failed' },
    ]);
    expect(afterBuildRepair.status).toBe('coding');

    const afterQaRepair = apply(afterBuildRepair, [
      { type: 'source_written' },
      { type: 'build_succeeded' },
      { type: 'qa_failed' },
    ]);
    expect(afterQaRepair.status).toBe('coding');
  });

  it('requires the explicit visual review path when requested', () => {
    const waiting = apply(createCadWorkflowState('run-5'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
      { type: 'brief_saved' },
      { type: 'source_written' },
      { type: 'build_succeeded' },
      {
        type: 'qa_passed',
        artifactId: 'preview-1',
        visualReviewRequired: true,
      },
    ]);

    expect(waiting.status).toBe('visual_review_waiting');
    const reviewing = transitionCadWorkflow(waiting, {
      type: 'start_visual_review',
    });
    expect(reviewing.status).toBe('visual_review');
    expect(
      transitionCadWorkflow(reviewing, { type: 'visual_review_passed' }).status,
    ).toBe('ready_to_finish');
  });

  it('returns to coding when visual review is rejected', () => {
    const reviewing = apply(createCadWorkflowState('run-6'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
      { type: 'brief_saved' },
      { type: 'source_written' },
      { type: 'build_succeeded' },
      {
        type: 'qa_passed',
        artifactId: 'preview-2',
        visualReviewRequired: true,
      },
      { type: 'start_visual_review' },
    ]);

    expect(
      transitionCadWorkflow(reviewing, { type: 'visual_review_rejected' })
        .status,
    ).toBe('coding');
  });

  it.each([
    { type: 'cancel', reason: 'User cancelled.' } as const,
    { type: 'fail', reason: 'Build budget exhausted.' } as const,
  ])('supports the terminal $type event from an active state', (event) => {
    const state = apply(createCadWorkflowState(`run-${event.type}`), [
      ...selectSingleColor,
      { type: 'start_briefing' },
    ]);
    const terminal = transitionCadWorkflow(state, event);

    expect(terminal.status).toBe(
      event.type === 'fail' ? 'failed' : 'cancelled',
    );
    expect(getWorkflowSelection(terminal)).toEqual(singleColorSelection);
  });

  it.each([
    ['received', { type: 'source_written' } as const],
    ['selecting_workflow', { type: 'start_briefing' } as const],
    ['coding', { type: 'qa_failed' } as const],
  ])('rejects illegal transitions from %s', (targetStatus, event) => {
    const states: Record<string, CadWorkflowState> = {
      received: createCadWorkflowState('run-illegal-1'),
      selecting_workflow: apply(createCadWorkflowState('run-illegal-2'), [
        { type: 'skip_research' },
        { type: 'begin_workflow_selection' },
      ]),
      coding: apply(createCadWorkflowState('run-illegal-3'), [
        ...selectSingleColor,
        { type: 'start_briefing' },
        { type: 'brief_saved' },
      ]),
    };

    expect(states[targetStatus]?.status).toBe(targetStatus);
    expect(() =>
      transitionCadWorkflow(states[targetStatus] as CadWorkflowState, event),
    ).toThrowError(
      expect.objectContaining({
        code: 'IllegalWorkflowTransition',
      }),
    );
  });

  it('rejects transitions out of terminal states', () => {
    const completed = apply(createCadWorkflowState('run-terminal'), [
      ...selectSingleColor,
      { type: 'start_briefing' },
      { type: 'brief_saved' },
      { type: 'source_written' },
      { type: 'build_succeeded' },
      {
        type: 'qa_passed',
        artifactId: 'artifact-terminal',
        visualReviewRequired: false,
      },
      { type: 'finish', artifactId: 'artifact-terminal' },
    ]);

    expect(() =>
      transitionCadWorkflow(completed, {
        type: 'cancel',
        reason: 'Too late.',
      }),
    ).toThrowError(/not allowed/u);
  });
});
