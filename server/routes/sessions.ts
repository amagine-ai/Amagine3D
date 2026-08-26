import type { Express } from 'express';

import {
  BUNDLED_POMODORO_SESSION_ID,
  type PythonHealth,
} from '../../src/types.ts';
import {
  createArtifactArchive,
  MAX_ARCHIVE_FILES,
} from '../artifact-archive.ts';
import {
  MAX_TRASH_FILES,
  moveArtifactsToTrash,
} from '../artifact-trash.ts';
import {
  artifactContentType,
  createReadStream,
  resolveArtifactPath,
} from '../artifacts.ts';
import { bundledPomodoroArtifacts } from '../bundled-workspace.ts';
import {
  ParameterBuildError,
  parameterModelsForWorkspace,
  parseParameterBuildRequest,
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
import {
  MAX_TRASH_SESSIONS,
  moveSessionsToTrash,
} from '../session-trash.ts';

export interface SessionRoutePaths {
  bundledPomodoroRoot: string;
  sessionRoot: string;
  workspaceRoot: string;
}

export function registerSessionRoutes(
  app: Express,
  paths: SessionRoutePaths,
  python: PythonHealth,
): void {
  app.get('/api/sessions', async (_request, response) => {
    response.json(await listSessionCatalog(paths.sessionRoot));
  });

  app.get('/api/sessions/storage', async (_request, response) => {
    response.json(
      await listWorkspaceStorage(
        paths.sessionRoot,
        paths.workspaceRoot,
        paths.bundledPomodoroRoot,
      ),
    );
  });

  app.post('/api/sessions/storage/trash', async (request, response) => {
    const requestedSessionIds: unknown = request.body?.sessionIds;
    if (
      !Array.isArray(requestedSessionIds) ||
      requestedSessionIds.length === 0 ||
      requestedSessionIds.length > MAX_TRASH_SESSIONS ||
      requestedSessionIds.some((sessionId) => typeof sessionId !== 'string')
    ) {
      response
        .status(400)
        .json({ message: 'At least one valid session id is required.' });
      return;
    }
    const sessionIds = requestedSessionIds as string[];
    const trashed = await moveSessionsToTrash(
      paths.sessionRoot,
      paths.workspaceRoot,
      sessionIds,
    );
    if (trashed === undefined) {
      response.status(404).json({ message: 'Session not found.' });
      return;
    }
    response.json({ trashed });
  });

  app.get('/api/sessions/:sessionId', async (request, response) => {
    const { sessionId } = request.params;
    if (sessionId === BUNDLED_POMODORO_SESSION_ID) {
      response.json({
        ...(await bundledPomodoroArtifacts(paths.bundledPomodoroRoot)),
        messages: [],
        session: BUILTIN_POMODORO_SESSION,
      });
      return;
    }
    const session = await findUserSession(paths.sessionRoot, sessionId);
    if (!session) {
      response.status(404).json({ message: 'Session not found.' });
      return;
    }
    const artifacts = await userSessionArtifacts(paths.workspaceRoot, sessionId);
    if (!artifacts) {
      response.status(400).json({ message: 'Invalid session id.' });
      return;
    }
    const catalog = await listSessionCatalog(paths.sessionRoot);
    const summary = catalog.sessions.find(({ id }) => id === sessionId);
    if (!summary) {
      response.status(404).json({ message: 'Session not found.' });
      return;
    }
    response.json({
      ...artifacts,
      messages: await readSessionMessages(session.path),
      session: summary,
    });
  });

  app.get('/api/sessions/:sessionId/artifacts', async (request, response) => {
    const collection = await artifactsForSession(
      paths.workspaceRoot,
      paths.bundledPomodoroRoot,
      request.params.sessionId,
    );
    if (!collection) {
      response.status(400).json({ message: 'Invalid session id.' });
      return;
    }
    response.json(collection);
  });

  app.get('/api/sessions/:sessionId/parameters', async (request, response) => {
    if (request.params.sessionId === BUNDLED_POMODORO_SESSION_ID) {
      response.json({ models: [] });
      return;
    }
    const workspaceRoot = sessionWorkspaceRoot(
      paths.workspaceRoot,
      request.params.sessionId,
    );
    if (!workspaceRoot) {
      response.status(400).json({ message: 'Invalid session id.' });
      return;
    }
    if (!python.ready || !python.executable) {
      response.status(503).json({ message: 'Python CAD runtime is not ready.' });
      return;
    }
    try {
      const collection = await userSessionArtifacts(
        paths.workspaceRoot,
        request.params.sessionId,
      );
      response.json({
        models: await parameterModelsForWorkspace(
          workspaceRoot,
          python.executable,
          collection?.artifacts,
        ),
      });
    } catch (error) {
      response.status(500).json({
        message:
          error instanceof Error
            ? error.message
            : 'Unable to inspect model parameters.',
      });
    }
  });

  app.post(
    '/api/sessions/:sessionId/parameters/rebuild',
    async (request, response) => {
      if (request.params.sessionId === BUNDLED_POMODORO_SESSION_ID) {
        response.status(403).json({ message: 'Built-in projects are read-only.' });
        return;
      }
      const workspaceRoot = sessionWorkspaceRoot(
        paths.workspaceRoot,
        request.params.sessionId,
      );
      const buildRequest = parseParameterBuildRequest(request.body);
      if (!workspaceRoot || !buildRequest) {
        response.status(400).json({ message: 'Invalid parameter build request.' });
        return;
      }
      if (!python.ready || !python.executable) {
        response.status(503).json({ message: 'Python CAD runtime is not ready.' });
        return;
      }
      const releaseSession = acquireSessionActivity(request.params.sessionId);
      if (!releaseSession) {
        response.status(409).json({
          message: 'This session already has an active CAD operation.',
        });
        return;
      }
      try {
        await rebuildModelWithParameters({
          pythonExecutable: python.executable,
          request: buildRequest,
          workspaceRoot,
        });
        const collection = await userSessionArtifacts(
          paths.workspaceRoot,
          request.params.sessionId,
        );
        if (!collection) {
          throw new ParameterBuildError('Invalid session id.', 400);
        }
        response.json({
          ...collection,
          models: await parameterModelsForWorkspace(
            workspaceRoot,
            python.executable,
            collection.artifacts,
          ),
        });
      } catch (error) {
        response.status(error instanceof ParameterBuildError ? error.status : 500).json({
          message:
            error instanceof Error ? error.message : 'Parameter build failed.',
        });
      } finally {
        releaseSession();
      }
    },
  );

  app.get('/api/sessions/:sessionId/artifacts/file', async (request, response) => {
    const scopedWorkspaceRoot = sessionWorkspaceRoot(
      paths.workspaceRoot,
      request.params.sessionId,
    );
    const requestedPath = request.query.path;
    if (
      !scopedWorkspaceRoot ||
      typeof requestedPath !== 'string' ||
      requestedPath.length > 1_024
    ) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const artifactPath = await resolveArtifactPath(
      scopedWorkspaceRoot,
      requestedPath,
    );
    if (!artifactPath) {
      response.status(404).json({ message: 'Artifact not found.' });
      return;
    }
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Content-Type', artifactContentType(artifactPath));
    createReadStream(artifactPath).pipe(response);
  });

  app.post(
    '/api/sessions/:sessionId/artifacts/archive',
    async (request, response) => {
      const requestedPaths: unknown = request.body?.paths;
      if (
        !Array.isArray(requestedPaths) ||
        requestedPaths.length < 2 ||
        requestedPaths.length > MAX_ARCHIVE_FILES ||
        requestedPaths.some(
          (path) => typeof path !== 'string' || path.length > 1_024,
        )
      ) {
        response
          .status(400)
          .json({ message: 'At least two valid artifact paths are required.' });
        return;
      }
      const workspaceRoot =
        request.params.sessionId === BUNDLED_POMODORO_SESSION_ID
          ? paths.bundledPomodoroRoot
          : sessionWorkspaceRoot(paths.workspaceRoot, request.params.sessionId);
      if (!workspaceRoot) {
        response.status(400).json({ message: 'Invalid session id.' });
        return;
      }
      const archive = await createArtifactArchive(workspaceRoot, requestedPaths);
      if (!archive) {
        response.status(404).json({ message: 'Artifact not found.' });
        return;
      }
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader(
        'Content-Disposition',
        'attachment; filename="amagine3d-files.zip"',
      );
      response.type('application/zip').send(Buffer.from(archive));
    },
  );

  app.post(
    '/api/sessions/:sessionId/artifacts/trash',
    async (request, response) => {
      if (request.params.sessionId === BUNDLED_POMODORO_SESSION_ID) {
        response
          .status(403)
          .json({ message: 'Built-in project files are read-only.' });
        return;
      }
      const requestedPaths: unknown = request.body?.paths;
      if (
        !Array.isArray(requestedPaths) ||
        requestedPaths.length === 0 ||
        requestedPaths.length > MAX_TRASH_FILES ||
        requestedPaths.some(
          (path) => typeof path !== 'string' || path.length > 1_024,
        )
      ) {
        response
          .status(400)
          .json({ message: 'At least one valid artifact path is required.' });
        return;
      }
      const workspaceRoot = sessionWorkspaceRoot(
        paths.workspaceRoot,
        request.params.sessionId,
      );
      if (!workspaceRoot) {
        response.status(400).json({ message: 'Invalid session id.' });
        return;
      }
      const trashed = await moveArtifactsToTrash(
        workspaceRoot,
        requestedPaths,
      );
      if (trashed === undefined) {
        response.status(404).json({ message: 'Artifact not found.' });
        return;
      }
      response.json({ trashed });
    },
  );

  app.get('/api/bundled-artifacts/file', async (request, response) => {
    const requestedPath = request.query.path;
    if (typeof requestedPath !== 'string' || requestedPath.length > 1_024) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const artifactPath = await resolveArtifactPath(
      paths.bundledPomodoroRoot,
      requestedPath,
    );
    if (!artifactPath) {
      response.status(404).json({ message: 'Bundled artifact not found.' });
      return;
    }
    response.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    response.setHeader('Content-Type', artifactContentType(artifactPath));
    createReadStream(artifactPath).pipe(response);
  });
}
