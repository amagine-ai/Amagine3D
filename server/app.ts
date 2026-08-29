import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { createExpressMiddleware } from '@trpc/server/adapters/express';
import express, { type Express } from 'express';

import { registerArtifactRoutes } from './routes/artifacts.ts';
import { registerChatRoute } from './routes/chat.ts';
import type { TrpcContext } from './trpc/context.ts';
import { appRouter } from './trpc/router.ts';

export type AppDependencies = TrpcContext;

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

  app.use(
    '/trpc',
    createExpressMiddleware({
      createContext: () => dependencies,
      router: appRouter,
    }),
  );
  registerArtifactRoutes(app, paths);
  registerChatRoute(app, { python, runtime, runtimeError });

  if (existsSync(paths.distPath)) {
    app.use(express.static(paths.distPath));
    app.get('*splat', (_request, response) => {
      response.sendFile(join(paths.distPath, 'index.html'));
    });
  }

  return app;
}
