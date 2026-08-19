import { describe, expect, it } from 'vitest';

import { planCadChatSubmission } from './cad-chat-continuation';

const completedProject = {
  started: true,
  runActive: false,
  selectedProjectId: 'project-1',
  hasController: true,
  current: { runId: 'run-1', phase: 'completed' },
  workspace: {
    projectId: 'project-1',
    runId: 'run-1',
    source: 'from build123d import *',
    revisions: [{ id: 'revision-1' }],
  },
} as const;

describe('CAD chat continuation planning', () => {
  it('continues a completed chat as a modification in the same project', () => {
    expect(planCadChatSubmission(completedProject)).toEqual({
      kind: 'continue-project',
      projectId: 'project-1',
      parentRunId: 'run-1',
      mode: 'modification',
      baseRevisionId: 'revision-1',
    });
  });

  it.each(['failed', 'cancelled'] as const)(
    'continues a %s chat as a baseline run in the same project',
    (phase) => {
      expect(
        planCadChatSubmission({
          ...completedProject,
          current: { runId: 'run-1', phase },
        }),
      ).toEqual({
        kind: 'continue-project',
        projectId: 'project-1',
        parentRunId: 'run-1',
        mode: 'baseline',
      });
    },
  );

  it('keeps a completed chat in the project when no model source exists', () => {
    expect(
      planCadChatSubmission({
        ...completedProject,
        workspace: {
          projectId: 'project-1',
          runId: 'run-1',
          revisions: [{ id: 'revision-1' }],
        },
      }),
    ).toEqual({
      kind: 'continue-project',
      projectId: 'project-1',
      parentRunId: 'run-1',
      mode: 'baseline',
    });
  });

  it('starts a new project only after the active chat has been cleared', () => {
    expect(
      planCadChatSubmission({
        started: false,
        runActive: false,
        hasController: false,
      }),
    ).toEqual({ kind: 'new-project' });
  });

  it('starts a new project when the selected project is read-only', () => {
    expect(
      planCadChatSubmission({
        ...completedProject,
        readOnlyProject: true,
      }),
    ).toEqual({ kind: 'new-project' });
  });

  it('does not continue when the selected project and workspace disagree', () => {
    expect(
      planCadChatSubmission({
        ...completedProject,
        selectedProjectId: 'project-2',
      }),
    ).toEqual({ kind: 'blocked' });
  });
});
