import { constants } from 'node:fs';
import { access } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import { defineTool } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';

import { runPythonJsonProcess } from './python-json-process.ts';

export const CAD_CAPABILITIES_TOOL_NAME = 'cad_capabilities';
export const CAD_CAPABILITIES_SCHEMA = 'evidence-cad-capabilities/v1';

const parameters = Type.Object({
  symbols: Type.Optional(
    Type.Array(
      Type.String({
        description:
          'Optional build123d symbol to query in the installed runtime.',
        maxLength: 120,
        minLength: 1,
      }),
      { maxItems: 40, uniqueItems: true },
    ),
  ),
});

export interface CadCapabilitiesResult {
  schema: typeof CAD_CAPABILITIES_SCHEMA;
  [key: string]: unknown;
}

function parseResult(stdout: string): CadCapabilitiesResult {
  let value: unknown;
  try {
    value = JSON.parse(stdout);
  } catch (error) {
    throw new Error(
      `cad_capabilities emitted invalid JSON: ${(error as Error).message}`,
    );
  }
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    (value as { schema?: unknown }).schema !== CAD_CAPABILITIES_SCHEMA
  ) {
    throw new Error('cad_capabilities emitted an unsupported capability schema.');
  }
  return value as CadCapabilitiesResult;
}

export function createCadCapabilitiesTool(projectRoot: string) {
  const root = resolve(projectRoot);
  const python =
    process.platform === 'win32'
      ? join(root, '.venv', 'Scripts', 'python.exe')
      : join(root, '.venv', 'bin', 'python');
  const script = join(root, 'skills', 'text-a3d', 'capability_manifest.py');

  return defineTool({
    name: CAD_CAPABILITIES_TOOL_NAME,
    label: 'Inspect CAD Capabilities',
    description:
      'Return a compact, version-bound manifest of the installed build123d API, supported text-a3d artifact families, generic modeling recipes, and connector helpers. This is advisory context for the existing Agent loop.',
    promptSnippet:
      'Inspect the installed CAD authoring capabilities without reading compiler internals',
    promptGuidelines: [
      'Use cad_capabilities before guessing build123d APIs or reading compiler implementation files.',
      'Query uncertain symbols explicitly; choose construction from user evidence and controlling dimensions, not from a product-specific template.',
      'The manifest is advisory and does not impose stages or replace Agent judgment.',
    ],
    parameters,
    executionMode: 'parallel',
    async execute(_toolCallId, params, signal) {
      await Promise.all([
        access(python, constants.X_OK),
        access(script, constants.R_OK),
      ]);
      const argv = [script];
      for (const symbol of params.symbols ?? []) {
        argv.push('--symbol', symbol);
      }
      const processResult = await runPythonJsonProcess(python, argv, {
        cwd: root,
        signal,
        timeoutMs: 15_000,
      });
      if (processResult.exitCode !== 0) {
        throw new Error(
          `cad_capabilities failed: ${processResult.stderr || processResult.stdout}`,
        );
      }
      const result = parseResult(processResult.stdout);
      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        details: result,
      };
    },
  });
}
