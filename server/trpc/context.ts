import type { CodexRuntimeLike } from '@amagine3d/a3d-runtime';

import type { PythonHealth } from '../../src/types.ts';
import type { ServerPaths } from '../paths.ts';

export interface TrpcContext {
  paths: ServerPaths;
  python: PythonHealth;
  runtime: CodexRuntimeLike | undefined;
  runtimeError: string | undefined;
}
