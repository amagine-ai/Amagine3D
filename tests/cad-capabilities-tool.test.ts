import { strict as assert } from 'node:assert';
import { chmod, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  CAD_CAPABILITIES_SCHEMA,
  CAD_CAPABILITIES_TOOL_NAME,
  createCadCapabilitiesTool,
  type CadCapabilitiesResult,
} from '@amagine3d/a3d-runtime';

const fakePythonSource = `#!/usr/bin/env node
const fs = require('node:fs');

const script = process.argv[2];
const argv = process.argv.slice(3);
if (fs.readFileSync(script, 'utf8').includes('invalid-schema')) {
  process.stdout.write(JSON.stringify({ schema: 'unsupported/v1' }));
  process.exit(0);
}
const symbols = [];
for (let index = 0; index < argv.length; index += 1) {
  if (argv[index] === '--symbol') symbols.push(argv[index + 1]);
}
process.stdout.write(JSON.stringify({
  api: { library: 'build123d', publicSymbols: ['Box', 'Sphere'] },
  query: Object.fromEntries(symbols.map((symbol) => [symbol, symbol === 'Sphere'])),
  schema: 'evidence-cad-capabilities/v1',
}));
`;

interface Fixture {
  cleanup(): Promise<void>;
  projectRoot: string;
  script: string;
}

async function createFixture(): Promise<Fixture> {
  const temporary = await mkdtemp(join(tmpdir(), 'amagine-capabilities-tool-'));
  const projectRoot = join(temporary, 'project');
  const executable =
    process.platform === 'win32'
      ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
      : join(projectRoot, '.venv', 'bin', 'python');
  const script = join(
    projectRoot,
    'skills',
    'text-a3d',
    'capability_manifest.py',
  );
  await mkdir(join(executable, '..'), { recursive: true });
  await mkdir(join(script, '..'), { recursive: true });
  await writeFile(executable, fakePythonSource);
  if (process.platform !== 'win32') await chmod(executable, 0o755);
  await writeFile(script, '# capability manifest\n');
  return {
    async cleanup() {
      await rm(temporary, { force: true, recursive: true });
    },
    projectRoot,
    script,
  };
}

test(
  'cad_capabilities returns a version-bound advisory manifest and symbol queries',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      const tool = createCadCapabilitiesTool(fixture.projectRoot);
      assert.equal(tool.name, CAD_CAPABILITIES_TOOL_NAME);
      assert.equal(tool.executionMode, 'parallel');
      assert.match(
        (tool.promptGuidelines ?? []).join('\n'),
        /advisory[\s\S]*does not impose stages/u,
      );

      const result = await tool.execute(
        'capabilities-call',
        { symbols: ['Sphere', 'Ellipsoid'] },
        undefined,
        undefined,
        {} as never,
      );
      const details = result.details as CadCapabilitiesResult;
      assert.equal(details.schema, CAD_CAPABILITIES_SCHEMA);
      assert.deepEqual(details.query, { Ellipsoid: false, Sphere: true });
      assert.deepEqual(
        JSON.parse(
          result.content[0]?.type === 'text' ? result.content[0].text : '',
        ),
        details,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);

test(
  'cad_capabilities rejects an analyzer response with an unsupported schema',
  { skip: process.platform === 'win32' },
  async () => {
    const fixture = await createFixture();
    try {
      await writeFile(fixture.script, 'invalid-schema\n');
      const tool = createCadCapabilitiesTool(fixture.projectRoot);
      await assert.rejects(
        tool.execute(
          'capabilities-call',
          {},
          undefined,
          undefined,
          {} as never,
        ),
        /unsupported capability schema/u,
      );
    } finally {
      await fixture.cleanup();
    }
  },
);
