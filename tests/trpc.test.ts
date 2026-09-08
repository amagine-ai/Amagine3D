import { strict as assert } from 'node:assert';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  createTRPCClient,
  httpBatchLink,
  TRPCClientError,
} from '@trpc/client';

import { createApp } from '../server/app.ts';
import { appRouter, type AppRouter } from '../server/trpc/router.ts';
import { API_VERSION, BUNDLED_POMODORO_SESSION_ID } from '../src/types.ts';

test('serves the JSON contract through tRPC and removes the old REST API', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-trpc-'));
  const sessionRoot = join(root, 'sessions');
  const workspaceRoot = join(root, 'workspace');
  const bundledPomodoroRoot = join(root, 'bundled');
  await Promise.all([
    mkdir(sessionRoot),
    mkdir(workspaceRoot),
    mkdir(bundledPomodoroRoot),
  ]);

  const server = createApp({
    paths: {
      bundledPomodoroRoot,
      distPath: join(root, 'missing-dist'),
      projectRoot: root,
      sessionRoot,
      workspaceRoot,
    },
    python: { executable: null, ready: false, version: null },
    runtime: undefined,
    runtimeError: 'Runtime unavailable in test.',
  }).listen(0, '127.0.0.1');

  try {
    await new Promise<void>((resolvePromise, rejectPromise) => {
      server.once('listening', resolvePromise);
      server.once('error', rejectPromise);
    });
    const { port } = server.address() as AddressInfo;
    const origin = `http://127.0.0.1:${port}`;
    const client = createTRPCClient<AppRouter>({
      links: [httpBatchLink({ url: `${origin}/trpc` })],
    });

    const health = await client.health.query();
    assert.equal(health.apiVersion, API_VERSION);
    assert.equal(health.runtimeReady, false);
    assert.equal(health.webSearchConfigured, false);
    assert.equal(health.webSearchEnabled, false);
    assert.equal(health.webSearchVerification, 'untested');

    const catalog = await client.sessions.catalog.query();
    assert.equal(catalog.initialSessionId, BUNDLED_POMODORO_SESSION_ID);
    assert.deepEqual(
      catalog.sessions.map(({ kind }) => kind),
      ['builtin'],
    );

    await assert.rejects(
      client.sessions.detail.query({ sessionId: '../invalid' }),
      (error: unknown) =>
        error instanceof TRPCClientError && error.data?.code === 'BAD_REQUEST',
    );
    await assert.rejects(
      client.sessions.trashArtifacts.mutate({
        paths: ['pomodoro.stl'],
        sessionId: BUNDLED_POMODORO_SESSION_ID,
      }),
      (error: unknown) =>
        error instanceof TRPCClientError && error.data?.code === 'FORBIDDEN',
    );

    const legacyHealth = await fetch(`${origin}/api/health`);
    assert.equal(legacyHealth.status, 404);
  } finally {
    await new Promise<void>((resolvePromise, rejectPromise) => {
      server.close((error) => {
        if (error) rejectPromise(error);
        else resolvePromise();
      });
    });
    await rm(root, { force: true, recursive: true });
  }
});

test('health reports search configuration without claiming provider verification', async () => {
  for (const configured of [false, true]) {
    for (const webSearchEnabled of [false, true]) {
      const caller = appRouter.createCaller({
        paths: {
          bundledPomodoroRoot: '/unused', distPath: '/unused', projectRoot: '/unused',
          sessionRoot: '/unused', workspaceRoot: '/unused',
        },
        python: { executable: null, ready: false, version: null },
        runtimeError: undefined,
        runtime: {
          configured, modelName: 'test-model', runtimeReady: true, skills: [],
          skillDiagnostics: [], stateRoot: '/unused', workspaceRoot: '/unused', webSearchEnabled,
          async runTurn() { throw new Error('Health must not invoke the provider.'); },
        },
      });
      const health = await caller.health();
      assert.equal(health.webSearchConfigured, configured);
      assert.equal(health.webSearchEnabled, webSearchEnabled);
      assert.equal(health.webSearchVerification, 'untested');
    }
  }
});
