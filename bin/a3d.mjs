#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const skillRoot = join(projectRoot, 'skills', 'text-a3d');
const python =
  process.env.AMAGINE3D_PYTHON?.trim() ||
  join(
    projectRoot,
    '.venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );

const commands = {
  capabilities: 'capability_manifest.py',
  compile: 'cad_compile.py',
  intent: 'intent_contract.py',
  mark: 'freshness_check.py',
  profile: 'bambu_profile.py',
  reference: 'reference_analyze.py',
  scene: 'scene_contract.py',
};

const guides = {
  color: `Manufactured color
- A physical part is separately printable; a region is a permanent material assignment inside one part.
- Declare stable regions once in the immutable intent and bind them in the scene.
- A manufactured-color 3MF must bind colors to closed whole parts or closed volumetric regions; display tint alone is insufficient.
- Multipart enclosures without a declared palette get stable, distinct proposed whole-part materials and a printable 3MF.
- A single unpartitioned part gets one printable whole-part material assignment, not a false multicolor claim. Add more colors only as real volumetric regions with meaningful boundaries; never invent structural splits just to add color.
- Whole-part BRep assignments are automatic in export_part()/export_assembly(); part_colors may verify declared assembly colors.
- Multiple permanent regions in one BRep body: color.cad_helpers.export_regions().
- Multiple regions in a mesh master: Hybrid volumetric region assignments; surface-only paint is display appearance.
- Display-only color belongs only in the GLB and must not be claimed as printed color.
- Treat 3MF as the preferred manufactured-color deliverable.
Read $AMAGINE3D_SKILL_DIR/color/BACKEND.md only for multiple material regions inside one part or uncommon region topology.`,
  multipart: `Multipart construction
- Make parts separate only when they are separately manufactured or assembled.
- Existing part boundaries may carry distinct proposed manufacturing colors; do not add parts solely to create a palette.
- Give every printed part a declared connection, assembly axis, engagement depth, male/female feature IDs, and acceptance evidence unless adhesive or loose installation is explicit.
- Derive female geometry from the male. A declared clearance is the full female-minus-male size delta; a radial recipe gap is per side, so its diameter delta is twice that value.
- Choose the connection from assembly and service behavior: locator plus fasteners, snap, hinge, retained slider, inset pocket, adhesive, or intentional loose fit.
- Derive envelopes, walls, component stacks, and mating planes from named datums. Describe how each separate part enters its final position and preserves required service access.
- Do not split solely to fit the default printer when the final size is not fixed.
- A printable interface proves a static manufactured relationship, not a motion path or full-travel clearance.
- Inspect the actual mating subfeatures; an unmeasured fit is unknown, not passed. Read references/design-review.md for component support, assembly paths, and repair reasoning.
- Use a3d capabilities --symbol NAME for an exact helper signature.
Read $AMAGINE3D_SKILL_DIR/references/multipart-connections.md only for direct fastening into printed plastic or a serviceable-enclosure closure.`,
  'pressable-control': `Pressable or sliding control
- Use interface_recipes.retained_slider() only for guided single-axis translation that needs mechanical retention; otherwise model the actual pivot, flexure, membrane, or installed mechanism.
- Declare radial clearance, guide depth, travel, axis, limits, retention, and the supporting/contact behavior explicitly.
- Place the returned male and female cutter from one shared datum; do not hand-copy mating dimensions.
- Plan how the retained member is inserted before retention is completed. A solid with enlarged ends and a closed guide is not automatically assemblable.
- Derive rest/contact/stop positions and signed travel from the installed mechanism. Observe the mating shaft separately from its larger cap for dimension evidence.
- A travel declaration does not prove full-travel collision clearance, return force, or electrical actuation; model and inspect those requirements when the user needs them.
- Bind permanent button color through intent and scene material/color records, then verify 3MF and the preview.`,
  strategy: `Geometry strategy
- Treat a user-requested representation as the target; report incompatibility rather than silently substitute.
- Exact dimensions, analytic profiles, mating surfaces, or mechanical interfaces favor datum-driven BRep.
- Identity controlled by a continuous freeform surface favors one watertight mesh master.
- One physical body needing both uses a mesh master with directly bound BRep additions and cutters; independent manufactured parts may remain BRep masters.
- Clean orthographic evidence may drive constrained profiles and extrusions; pixel art may drive occupied-cell geometry or relief.
- A photograph establishes envelope, landmarks, and uncertainty but does not select a master by itself.
Choose from geometry requirements, not the product name or input file type.`,
};

function help() {
  console.log(`a3d — Amagine3D CAD command line

Usage:
  a3d capabilities [--symbol NAME]
  a3d diagnose COMPILE_RESULT.json [--id ID | --code CODE | --severity LEVEL]
  a3d guide [strategy|pressable-control|multipart|color]
  a3d mark --mark FILE
  a3d profile [bambu_profile.py arguments]
  a3d intent INTENT.json
  a3d scene SCENE.json
  a3d reference IMAGE [--out REPORT.json]
  a3d compile SCENE.json --marker FILE --intent INTENT.json --source BUILD.py [--output-dir DIR]

All paths are resolved inside the current session workspace. Run the generated
build source through \`a3d compile\`; do not execute it separately. Concise CAD
authoring guidance is at $AMAGINE3D_SKILL_DIR/SKILL.md.`);
}

function fail(message) {
  console.error(message);
  process.exit(2);
}

function diagnose(args) {
  const [input, ...selectors] = args;
  if (!input) fail('a3d diagnose requires a compile-result JSON file.');
  const root = realpathSync(process.cwd());
  let path;
  try {
    path = realpathSync(resolve(root, input));
  } catch (error) {
    fail(`Cannot read compile result: ${error.message}`);
  }
  const fromRoot = relative(root, path);
  if (fromRoot.startsWith('..') || isAbsolute(fromRoot)) {
    fail('a3d diagnose only reads files inside the current session workspace.');
  }
  const filters = {};
  for (let index = 0; index < selectors.length; index += 2) {
    const flag = selectors[index];
    const value = selectors[index + 1];
    if (!['--code', '--id', '--severity'].includes(flag) || !value) {
      fail('Use --id ID, --code CODE, or --severity LEVEL to select diagnostics.');
    }
    filters[flag.slice(2)] = value;
  }
  if (Object.keys(filters).length === 0) filters.severity = 'error';
  let payload;
  try {
    payload = JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    fail(`Compile result is not valid JSON: ${error.message}`);
  }
  if (payload?.schema !== 'evidence-cad-compile-result/v1' || !Array.isArray(payload.issues)) {
    fail('File is not an evidence-cad-compile-result/v1 document.');
  }
  const issues = payload.issues.filter((issue) =>
    Object.entries(filters).every(([key, value]) => issue?.[key] === value),
  );
  console.log(
    JSON.stringify(
      {
        count: issues.length,
        fullResult: path,
        issues,
        schema: 'a3d-diagnostics/v1',
      },
      null,
      2,
    ),
  );
}

const [command, ...args] = process.argv.slice(2);
if (!command || command === 'help' || command === '--help' || command === '-h') {
  help();
  process.exit(0);
}
if (command === 'guide') {
  const topic = args[0];
  if (!topic) {
    console.log(`Available a3d guides: ${Object.keys(guides).join(', ')}`);
    process.exit(0);
  }
  if (!(topic in guides) || args.length > 1) {
    console.error(`Unknown a3d guide: ${args.join(' ')}`);
    console.error(`Available guides: ${Object.keys(guides).join(', ')}`);
    process.exit(2);
  }
  console.log(guides[topic]);
  process.exit(0);
}
if (command === 'diagnose') {
  diagnose(args);
  process.exit(0);
}
if (!(command in commands)) {
  console.error(`Unknown a3d command: ${command}`);
  help();
  process.exit(2);
}
if (!existsSync(python)) {
  console.error('Managed Python is missing. Run npm run python:setup.');
  process.exit(2);
}
if (command === 'compile' && args.includes('--workspace')) {
  console.error('a3d compile fixes --workspace to the current session directory.');
  process.exit(2);
}

const scriptArgs = [join(skillRoot, commands[command]), ...args];
if (command === 'compile') scriptArgs.push('--workspace', process.cwd());
const child = spawn(python, scriptArgs, {
  cwd: process.cwd(),
  env: {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONNOUSERSITE: '1',
  },
  stdio: 'inherit',
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
child.once('error', (error) => {
  console.error(error.message);
  process.exitCode = 2;
});
child.once('exit', (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
