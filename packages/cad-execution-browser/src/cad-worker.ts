/// <reference lib="webworker" />

import { PyodideCadRuntime } from './pyodide-runtime';
import { CadWorkerServer } from './worker-server';

declare const self: DedicatedWorkerGlobalScope;

new CadWorkerServer(self, new PyodideCadRuntime());

export {};
