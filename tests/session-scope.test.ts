import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { createSessionScope } from '../src/lib/session-scope.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test('a delayed artifact refresh cannot repopulate a new empty session', async () => {
  const scope = createSessionScope('previous');
  const response = deferred<string[]>();
  const isCurrentSession = scope.capture('previous');
  let artifacts = ['previous.glb'];
  const refresh = response.promise.then((next) => {
    if (isCurrentSession()) artifacts = next;
  });

  scope.activate('new');
  artifacts = [];
  response.resolve(['previous-refreshed.glb']);
  await refresh;

  assert.deepEqual(artifacts, []);
  assert.equal(scope.capture('new')(), true);
});

test('returning to a session rejects both data and errors from its earlier activation', async () => {
  const scope = createSessionScope('a');
  const staleSuccess = deferred<string>();
  const staleFailure = deferred<string>();
  const isPreviousActivation = scope.capture('a');
  let model = 'initial';
  let error: string | undefined;
  const oldRequests = Promise.all([
    staleSuccess.promise.then((next) => {
      if (isPreviousActivation()) model = next;
    }),
    staleFailure.promise.catch((reason: Error) => {
      if (isPreviousActivation()) error = reason.message;
    }),
  ]);

  scope.activate('b');
  scope.activate('a');
  const isCurrentActivation = scope.capture('a');
  await Promise.resolve('current model').then((next) => {
    if (isCurrentActivation()) model = next;
  });
  staleSuccess.resolve('stale model');
  staleFailure.reject(new Error('stale parameter error'));
  await oldRequests;

  assert.equal(model, 'current model');
  assert.equal(error, undefined);
});

test('a stale callback cannot start valid work for a different active session', () => {
  const scope = createSessionScope('a');
  scope.activate('b');
  // For example, a delete request for A completes after switching to B.
  const isStaleRefresh = scope.capture('a');
  assert.equal(isStaleRefresh(), false);
  scope.activate('a');
  assert.equal(isStaleRefresh(), false);
  assert.equal(scope.capture('a')(), true);
});
