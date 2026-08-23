import type { Express } from 'express';

import { BUNDLED_POMODORO_SESSION_ID } from '../../src/types.ts';
import {
  artifactContentType,
  createReadStream,
  resolveArtifactPath,
} from '../artifacts.ts';
import { bundledPomodoroArtifacts } from '../bundled-workspace.ts';
import {
  artifactsForSession,
  BUILTIN_POMODORO_SESSION,
  findUserSession,
  listSessionCatalog,
  readSessionMessages,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../sessions.ts';

export interface SessionRoutePaths {
  bundledPomodoroRoot: string;
  sessionRoot: string;
  workspaceRoot: string;
}

export function registerSessionRoutes(
  app: Express,
  paths: SessionRoutePaths,
): void {
  app.get('/api/sessions', async (_request, response) => {
    response.json(await listSessionCatalog(paths.sessionRoot));
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
