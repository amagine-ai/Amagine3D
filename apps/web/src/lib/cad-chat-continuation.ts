type TerminalCadPhase = 'completed' | 'failed' | 'cancelled';

export type CadChatContinuationInput = {
  started: boolean;
  runActive: boolean;
  readOnlyProject?: boolean;
  selectedProjectId?: string;
  hasController: boolean;
  current?: {
    runId: string;
    phase: string;
  };
  workspace?: {
    projectId: string;
    runId: string;
    source?: string;
    revisions: readonly { id: string }[];
  };
};

export type CadChatSubmissionPlan =
  | { kind: 'new-project' }
  | { kind: 'blocked' }
  | {
      kind: 'continue-project';
      projectId: string;
      parentRunId: string;
      mode: 'baseline' | 'modification';
      baseRevisionId?: string;
    };

const TERMINAL_PHASES = new Set<TerminalCadPhase>([
  'completed',
  'failed',
  'cancelled',
]);

export function planCadChatSubmission(
  input: CadChatContinuationInput,
): CadChatSubmissionPlan {
  const { current, selectedProjectId, workspace } = input;
  if (!input.started || input.readOnlyProject === true) {
    return { kind: 'new-project' };
  }
  if (
    input.runActive ||
    !input.hasController ||
    current === undefined ||
    !TERMINAL_PHASES.has(current.phase as TerminalCadPhase) ||
    selectedProjectId === undefined ||
    workspace === undefined ||
    workspace.projectId !== selectedProjectId
  ) {
    return { kind: 'blocked' };
  }

  const baseRevisionId = workspace.revisions.at(-1)?.id ?? workspace.runId;
  const canModify =
    current.phase === 'completed' &&
    workspace.source !== undefined &&
    workspace.source.trim().length > 0 &&
    baseRevisionId.length > 0;

  return {
    kind: 'continue-project',
    projectId: selectedProjectId,
    parentRunId: current.runId,
    mode: canModify ? 'modification' : 'baseline',
    ...(canModify ? { baseRevisionId } : {}),
  };
}
