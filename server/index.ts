import 'dotenv/config';

import { PiRuntime } from '@amagine3d/a3d-runtime';

import { createApp } from './app.ts';
import { errorMessage } from './http-utils.ts';
import { serverPaths } from './paths.ts';
import { activateProjectPython } from './python-runtime.ts';

async function main(): Promise<void> {
  const paths = serverPaths();
  const python = activateProjectPython(paths.projectRoot);
  let runtime: PiRuntime | undefined;
  let runtimeError: string | undefined;
  try {
    runtime = await PiRuntime.create(paths.projectRoot);
  } catch (error) {
    runtimeError = errorMessage(error);
    console.error(
      `Amagine3D Agent initialization failed: ${runtimeError}`,
    );
  }

  const port = Number(process.env.PORT ?? 6161);
  const server = createApp({ paths, python, runtime, runtimeError }).listen(
    port,
    '127.0.0.1',
    () => console.log(`Amagine3D API: http://127.0.0.1:${port}`),
  );
  server.on('error', (error) => {
    console.error(`Could not start Amagine3D API: ${error.message}`);
    process.exitCode = 1;
  });
}

await main();
