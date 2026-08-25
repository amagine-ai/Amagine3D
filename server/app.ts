import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { PiRuntime } from '@amagine3d/a3d-runtime';
import express, { type Express } from 'express';

import { API_VERSION, type HealthResponse, type PythonHealth } from '../src/types.ts';
import type { ServerPaths } from './paths.ts';
import { registerChatRoute } from './routes/chat.ts';
import { registerSessionRoutes } from './routes/sessions.ts';

export interface AppDependencies {
  paths: ServerPaths;
  python: PythonHealth;
  runtime: PiRuntime | undefined;
  runtimeError: string | undefined;
}

function healthResponse(
  runtime: PiRuntime | undefined,
  runtimeError: string | undefined,
  python: PythonHealth,
): HealthResponse {
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

export function createApp(dependencies: AppDependencies): Express {
  const { paths, python, runtime, runtimeError } = dependencies;
  const app = express();
  app.disable('x-powered-by');
  app.use((_request, response, next) => {
    response.setHeader('Referrer-Policy', 'same-origin');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('X-Frame-Options', 'DENY');
    next();
  });
  app.use(express.json({ limit: '18mb' }));

  app.get('/api/health', (_request, response) => {
    response.json(healthResponse(runtime, runtimeError, python));
  });
  registerSessionRoutes(app, paths, python);
  registerChatRoute(app, { python, runtime, runtimeError });

  if (existsSync(paths.distPath)) {
    app.use(express.static(paths.distPath));
    app.get('*splat', (_request, response) => {
      response.sendFile(join(paths.distPath, 'index.html'));
    });
  }

  return app;
}
