import { initTRPC, TRPCError } from '@trpc/server';

import {
  API_VERSION,
  BUNDLED_POMODORO_SESSION_ID,
  type HealthResponse,
} from '../../src/types.ts';
import { moveArtifactsToTrash } from '../artifact-trash.ts';
import { bundledPomodoroArtifacts } from '../bundled-workspace.ts';
import {
  ParameterBuildError,
  parameterModelsForWorkspace,
  rebuildModelWithParameters,
} from '../model-parameters.ts';
import { acquireSessionActivity } from '../session-activity.ts';
import {
  artifactsForSession,
  BUILTIN_POMODORO_SESSION,
  findUserSession,
  listSessionCatalog,
  listWorkspaceStorage,
  readSessionMessages,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../sessions.ts';
import { moveSessionsToTrash } from '../session-trash.ts';
import type { TrpcContext } from './context.ts';
import {
  rebuildParametersInputSchema,
  sessionInputSchema,
  trashArtifactsInputSchema,
  trashStorageInputSchema,
} from './schemas.ts';

const t = initTRPC.context<TrpcContext>().create();

function healthResponse(context: TrpcContext): HealthResponse {
  const { python, runtime, runtimeError } = context;
  return {
    apiVersion: API_VERSION,
    configured: Boolean(process.env.LLM_API_KEY?.trim()),
    model: process.env.LLM_MODEL?.trim() || 'openai/gpt-5.5',
    python,
    ...(runtimeError ? { runtimeError } : {}),
    runtimeReady: Boolean(runtime),
    skills: runtime ? [...runtime.skills] : [],
    webSearchConfigured: Boolean(process.env.TAVILY_API_KEY?.trim()),
    workspace: 'workspace/',
  };
}

function internalError(error: unknown, fallback: string): never {
  if (error instanceof TRPCError) throw error;
  throw new TRPCError({
    cause: error,
    code: 'INTERNAL_SERVER_ERROR',
    message: error instanceof Error ? error.message : fallback,
  });
}

function parameterBuildError(error: unknown): never {
  if (!(error instanceof ParameterBuildError)) {
    internalError(error, 'Parameter build failed.');
  }
  const code =
    error.status === 400
      ? 'BAD_REQUEST'
      : error.status === 409
        ? 'CONFLICT'
        : error.status === 422
          ? 'UNPROCESSABLE_CONTENT'
          : 'INTERNAL_SERVER_ERROR';
  throw new TRPCError({ cause: error, code, message: error.message });
}

const sessionsRouter = t.router({
  artifacts: t.procedure
    .input(sessionInputSchema)
    .query(async ({ ctx, input }) => {
      const collection = await artifactsForSession(
        ctx.paths.workspaceRoot,
        ctx.paths.bundledPomodoroRoot,
        input.sessionId,
      );
      if (!collection) {
        throw new TRPCError({
          code: 'BAD_REQUEST',
          message: 'Invalid session id.',
        });
      }
      return collection;
    }),

  catalog: t.procedure.query(({ ctx }) =>
    listSessionCatalog(ctx.paths.sessionRoot),
  ),

  detail: t.procedure
    .input(sessionInputSchema)
    .query(async ({ ctx, input }) => {
      if (input.sessionId === BUNDLED_POMODORO_SESSION_ID) {
        return {
          ...(await bundledPomodoroArtifacts(ctx.paths.bundledPomodoroRoot)),
          messages: [],
          session: BUILTIN_POMODORO_SESSION,
        };
      }
      const session = await findUserSession(
        ctx.paths.sessionRoot,
        input.sessionId,
      );
      if (!session) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Session not found.',
        });
      }
      const artifacts = await userSessionArtifacts(
        ctx.paths.workspaceRoot,
        input.sessionId,
      );
      const catalog = await listSessionCatalog(ctx.paths.sessionRoot);
      const summary = catalog.sessions.find(({ id }) => id === input.sessionId);
      if (!artifacts || !summary) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Session not found.',
        });
      }
      return {
        ...artifacts,
        messages: await readSessionMessages(session.path),
        session: summary,
      };
    }),

  parameters: t.procedure
    .input(sessionInputSchema)
    .query(async ({ ctx, input }) => {
      if (input.sessionId === BUNDLED_POMODORO_SESSION_ID) {
        return { models: [] };
      }
      const workspaceRoot = sessionWorkspaceRoot(
        ctx.paths.workspaceRoot,
        input.sessionId,
      );
      if (!workspaceRoot) {
        throw new TRPCError({
          code: 'BAD_REQUEST',
          message: 'Invalid session id.',
        });
      }
      if (!ctx.python.ready || !ctx.python.executable) {
        throw new TRPCError({
          code: 'SERVICE_UNAVAILABLE',
          message: 'Python CAD runtime is not ready.',
        });
      }
      try {
        const collection = await userSessionArtifacts(
          ctx.paths.workspaceRoot,
          input.sessionId,
        );
        return {
          models: await parameterModelsForWorkspace(
            workspaceRoot,
            ctx.python.executable,
            collection?.artifacts,
          ),
        };
      } catch (error) {
        internalError(error, 'Unable to inspect model parameters.');
      }
    }),

  rebuildParameters: t.procedure
    .input(rebuildParametersInputSchema)
    .mutation(async ({ ctx, input }) => {
      const { sessionId, ...request } = input;
      if (sessionId === BUNDLED_POMODORO_SESSION_ID) {
        throw new TRPCError({
          code: 'FORBIDDEN',
          message: 'Built-in projects are read-only.',
        });
      }
      const workspaceRoot = sessionWorkspaceRoot(
        ctx.paths.workspaceRoot,
        sessionId,
      );
      if (!workspaceRoot) {
        throw new TRPCError({
          code: 'BAD_REQUEST',
          message: 'Invalid session id.',
        });
      }
      if (!ctx.python.ready || !ctx.python.executable) {
        throw new TRPCError({
          code: 'SERVICE_UNAVAILABLE',
          message: 'Python CAD runtime is not ready.',
        });
      }
      const releaseSession = acquireSessionActivity(sessionId);
      if (!releaseSession) {
        throw new TRPCError({
          code: 'CONFLICT',
          message: 'This session already has an active CAD operation.',
        });
      }
      try {
        await rebuildModelWithParameters({
          pythonExecutable: ctx.python.executable,
          request,
          workspaceRoot,
        });
        const collection = await userSessionArtifacts(
          ctx.paths.workspaceRoot,
          sessionId,
        );
        if (!collection) {
          throw new ParameterBuildError('Invalid session id.', 400);
        }
        return {
          ...collection,
          models: await parameterModelsForWorkspace(
            workspaceRoot,
            ctx.python.executable,
            collection.artifacts,
          ),
        };
      } catch (error) {
        parameterBuildError(error);
      } finally {
        releaseSession();
      }
    }),

  storage: t.procedure.query(({ ctx }) =>
    listWorkspaceStorage(
      ctx.paths.sessionRoot,
      ctx.paths.workspaceRoot,
      ctx.paths.bundledPomodoroRoot,
    ),
  ),

  trashArtifacts: t.procedure
    .input(trashArtifactsInputSchema)
    .mutation(async ({ ctx, input }) => {
      if (input.sessionId === BUNDLED_POMODORO_SESSION_ID) {
        throw new TRPCError({
          code: 'FORBIDDEN',
          message: 'Built-in project files are read-only.',
        });
      }
      const workspaceRoot = sessionWorkspaceRoot(
        ctx.paths.workspaceRoot,
        input.sessionId,
      );
      if (!workspaceRoot) {
        throw new TRPCError({
          code: 'BAD_REQUEST',
          message: 'Invalid session id.',
        });
      }
      const trashed = await moveArtifactsToTrash(workspaceRoot, input.paths);
      if (trashed === undefined) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Artifact not found.',
        });
      }
      return { trashed };
    }),

  trashStorage: t.procedure
    .input(trashStorageInputSchema)
    .mutation(async ({ ctx, input }) => {
      const trashed = await moveSessionsToTrash(
        ctx.paths.sessionRoot,
        ctx.paths.workspaceRoot,
        input.sessionIds,
      );
      if (trashed === undefined) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Session not found.',
        });
      }
      return { trashed };
    }),
});

export const appRouter = t.router({
  health: t.procedure.query(({ ctx }) => healthResponse(ctx)),
  sessions: sessionsRouter,
});

export type AppRouter = typeof appRouter;
