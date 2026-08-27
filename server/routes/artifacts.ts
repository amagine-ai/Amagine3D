import type { Express } from 'express';

import { BUNDLED_POMODORO_SESSION_ID } from '../../src/types.ts';
import { createArtifactArchive } from '../artifact-archive.ts';
import {
  artifactContentType,
  createReadStream,
  resolveArtifactPath,
} from '../artifacts.ts';
import { sessionWorkspaceRoot } from '../sessions.ts';
import {
  artifactArchiveInputSchema,
  artifactFileInputSchema,
  bundledArtifactFileInputSchema,
} from '../trpc/schemas.ts';

export interface ArtifactRoutePaths {
  bundledPomodoroRoot: string;
  workspaceRoot: string;
}

export function registerArtifactRoutes(
  app: Express,
  paths: ArtifactRoutePaths,
): void {
  app.get('/api/sessions/:sessionId/artifacts/file', async (request, response) => {
    const input = artifactFileInputSchema.safeParse({
      path: request.query.path,
      sessionId: request.params.sessionId,
    });
    if (!input.success) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const workspaceRoot = sessionWorkspaceRoot(
      paths.workspaceRoot,
      input.data.sessionId,
    );
    const artifactPath = workspaceRoot
      ? await resolveArtifactPath(workspaceRoot, input.data.path)
      : undefined;
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
      const input = artifactArchiveInputSchema.safeParse({
        paths: request.body?.paths,
        sessionId: request.params.sessionId,
      });
      if (!input.success) {
        response
          .status(400)
          .json({ message: 'At least two valid artifact paths are required.' });
        return;
      }
      const workspaceRoot =
        input.data.sessionId === BUNDLED_POMODORO_SESSION_ID
          ? paths.bundledPomodoroRoot
          : sessionWorkspaceRoot(paths.workspaceRoot, input.data.sessionId);
      if (!workspaceRoot) {
        response.status(400).json({ message: 'Invalid session id.' });
        return;
      }
      const archive = await createArtifactArchive(
        workspaceRoot,
        input.data.paths,
      );
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

  app.get('/api/bundled-artifacts/file', async (request, response) => {
    const input = bundledArtifactFileInputSchema.safeParse({
      path: request.query.path,
    });
    if (!input.success) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const artifactPath = await resolveArtifactPath(
      paths.bundledPomodoroRoot,
      input.data.path,
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
