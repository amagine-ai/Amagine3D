#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
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
  compare: 'visual_compare.py',
  compile: 'cad_compile.py',
  draft: 'cad_draft.py',
  intent: 'intent_contract.py',
  layout: 'plate_layout.py',
  mark: 'freshness_check.py',
  measure: 'brep_measurements.py',
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
- Display-only color belongs only in the GLB and must not be claimed as printed color.
- Treat 3MF as the preferred manufactured-color deliverable.
Read $AMAGINE3D_SKILL_DIR/color/BACKEND.md only for multiple material regions inside one part or uncommon region topology.`,
  multipart: `Multipart construction
- Make parts separate only when they are separately manufactured or assembled.
- Existing part boundaries may carry distinct proposed manufacturing colors; do not add parts solely to create a palette.
- Give every printed part a declared connection with owned endpoint feature IDs and acceptance evidence, or declare intentional loose installation. Contact interfaces and engaged fits use their own capability requirements.
- Derive female geometry from the male. A declared clearance is the full female-minus-male size delta; a radial recipe gap is per side, so its diameter delta is twice that value.
- Choose the connection from assembly and service behavior: locator plus fasteners, snap, hinge, retained slider, inset pocket, adhesive, or intentional loose fit.
- Derive envelopes, walls, component stacks, and mating planes from named datums. Describe how each separate part enters its final position and preserves required service access.
- Resolve the printer and plan proposed per-part print bounds before detailed geometry. a3d layout tries rigid XY turns and explicit plate grouping; never change dimensions or the selected printer to silence a packing failure.
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
- Author manufactured geometry as BRep solids with editable source and genuine STEP.
- Product envelopes favor lofts through meaningful sections with independently controlled width, depth, shape and placement.
- Use simple sections and ruled or segmented lofts when the intended form permits coarser transitions; validate the solid and actual wall thickness.
- Analytic profiles, extrusions, revolutions and sweeps remain appropriate for simpler geometry; construct cavities and mechanical interfaces in the same BRep body.
- STL, 3MF and GLB meshes are derived exports. Autonomous mesh/SDF master authoring is not supported.
- Clean orthographic evidence may drive constrained profiles and extrusions; pixel art may drive occupied-cell geometry or relief.
- A photograph establishes envelope, landmarks and uncertainty; use them to define editable BRep controls.
Choose from geometry requirements, not the product name or input file type.`,
};

function help() {
  console.log(`a3d — Amagine3D CAD command line

Usage:
  a3d capabilities [--symbol NAME]... (query related symbols together)
  a3d diagnose COMPILE_RESULT.json [--id ID | --code CODE | --severity LEVEL] [--offset N --limit N]
  a3d diagnose COMPILE_RESULT.json --id ID --field FIELD [--offset N --limit N]
  a3d diagnose COMPILE_RESULT.json [selectors] [--field FIELD] --full
  a3d guide [strategy|pressable-control|multipart|color]
  a3d mark --mark FILE
  a3d profile [bambu_profile.py arguments]
  a3d intent INTENT.json
  a3d layout BOUNDS.json --profile PROFILE.json [--max-plates N --spacing-mm N --edge-margin-mm N --out PLAN.json]
  a3d scene SCENE.json
  a3d reference IMAGE [--out REPORT.json]
  a3d draft SOURCE.py [--intent INTENT.json] [--timeout-seconds 120]
  a3d measure MODEL.step [--section-z MM] [--section-x MM] [--section-y MM] [--out FILE]
  a3d compare BEFORE AFTER --view front [--out FILE] [--report FILE]
  a3d compile SCENE.json --intent INTENT.json --source BUILD.py [--output-dir DIR] [--marker FILE]

All paths are resolved inside the current session workspace. Run the generated
final build source through \`a3d compile\`; do not execute it separately. Concise CAD
authoring guidance is at $AMAGINE3D_SKILL_DIR/SKILL.md. \`a3d draft\` previews a
BuildSession source before complete intent and feature registration. Use --intent
for an existing contract-bound source; low-level export_draft remains available.
Draft files are unvalidated and never replace final artifacts or publication.
Compile records stable input hashes for each run and owns output freshness.
No generation marker is required. Optional --marker keeps an existing legacy
file as provenance only; its timestamp does not authorize or reject inputs.

Diagnostics default to at most 5 issues and 12000 serialized characters.
Field pages contain JSON text in data (default 2000 UTF-16 code units); follow
nextOffset, concatenate data, then parse JSON. --full explicitly disables the
output budget and cannot be combined with --offset or --limit.`);
}

function fail(message) {
  console.error(message);
  process.exit(2);
}

const DIAGNOSTIC_BUDGET = 12_000;
const DIAGNOSTIC_PAGE_SIZE = 5;
const DIAGNOSTIC_FIELD_SIZE = 2_000;
const DIAGNOSTIC_KEYS = [
  'id', 'code', 'message', 'severity', 'check', 'stage', 'part', 'featureId',
  'nodeId', 'interfaceId', 'offenderId', 'ownerPartId', 'blockedBy', 'source',
  'file', 'line', 'field', 'status', 'repairHint', 'actual', 'observed', 'expected',
  'bounds', 'featureBounds', 'ownerBounds', 'componentCount', 'coordinateFrame',
];

function diagnosticJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function shortenDiagnostic(value, limit, state) {
  if (value.length <= limit) return value;
  state.truncated = true;
  const head = Math.floor((limit - 3) / 3);
  return `${value.slice(0, head)}\n…\n${value.slice(-(limit - head - 3))}`;
}

function projectDiagnostic(issue, level = 0) {
  const state = { truncated: false };
  const options = [
    { depth: 3, items: 4, keys: 8, string: 240, message: 700 },
    { depth: 2, items: 2, keys: 5, string: 120, message: 350 },
    { depth: 1, items: 1, keys: 3, string: 64, message: 180 },
  ][level];
  function project(value, depth, key, top = false) {
    if (typeof value === 'string') {
      return shortenDiagnostic(value, key === 'message' ? options.message : options.string, state);
    }
    if (value === null || typeof value !== 'object') return value;
    if (depth < 0) {
      state.truncated = true;
      return { omitted: Array.isArray(value) ? 'nested array' : 'nested object', size: Object.keys(value).length };
    }
    if (Array.isArray(value)) {
      if (value.length > options.items) state.truncated = true;
      return value.slice(0, options.items).map((item) => project(item, depth - 1));
    }
    const keys = Object.keys(value).sort((left, right) => {
      const rank = (name) => {
        const known = DIAGNOSTIC_KEYS.indexOf(name);
        return known >= 0 ? known : typeof value[name] === 'number' ? 100 : 200;
      };
      return rank(left) - rank(right);
    });
    const selected = keys.filter((name) => name.length <= 100).slice(0, top ? 28 : options.keys);
    if (selected.length !== keys.length) state.truncated = true;
    return Object.fromEntries(selected.map((name) => [name, project(value[name], depth - 1, name)]));
  }
  const value = project(issue, options.depth, '', true);
  return { value, truncated: state.truncated };
}

function emitDiagnosticPage(issues, path, options) {
  const offset = options.offset ?? 0;
  const limit = Math.min(options.limit ?? DIAGNOSTIC_PAGE_SIZE, DIAGNOSTIC_PAGE_SIZE);
  const projected = [];
  let projectionTruncated = false;
  const result = () => {
    const next = Math.min(offset + projected.length, issues.length);
    const hasMore = next < issues.length;
    return {
      schema: 'a3d-diagnostics/v1', fullResult: path, mode: 'issues',
      total: issues.length, count: projected.length, offset, limit,
      nextOffset: hasMore ? next : null, hasMore,
      truncated: projectionTruncated || offset > 0 || hasMore, projectionTruncated,
      issues: projected,
    };
  };
  for (const issue of issues.slice(offset, offset + limit)) {
    let accepted = false;
    for (let level = 0; level < 3; level += 1) {
      const candidate = projectDiagnostic(issue, level);
      const previousTruncated = projectionTruncated;
      projected.push(candidate.value);
      projectionTruncated ||= candidate.truncated;
      if (diagnosticJson(result()).length <= DIAGNOSTIC_BUDGET) {
        accepted = true;
        break;
      }
      projected.pop();
      projectionTruncated = previousTruncated;
      // Preserve a useful first record, then paginate instead of degrading
      // every record merely to fill the requested page size.
      if (projected.length) break;
    }
    if (!accepted) {
      if (!projected.length) fail('Diagnostic metadata cannot fit the output budget. Use --full for explicit unbounded output.');
      break;
    }
  }
  const output = diagnosticJson(result());
  if (output.length > DIAGNOSTIC_BUDGET) fail('Diagnostic metadata exceeds the output budget.');
  process.stdout.write(output);
}

function emitDiagnosticField(issue, path, options) {
  if (!Object.hasOwn(issue, options.field)) fail('The selected diagnostic does not have that top-level field.');
  const serialized = JSON.stringify(issue[options.field]);
  if (options.full) {
    process.stdout.write(diagnosticJson({
      schema: 'a3d-diagnostics/v1', fullResult: path, mode: 'field', full: true,
      id: issue.id, field: options.field, total: serialized.length, count: serialized.length,
      offset: 0, nextOffset: null, hasMore: false, truncated: false, value: issue[options.field],
    }));
    return;
  }
  const offset = options.offset ?? 0;
  const requested = Math.min(options.limit ?? DIAGNOSTIC_FIELD_SIZE, Math.max(0, serialized.length - offset));
  const state = { truncated: false };
  const id = shortenDiagnostic(String(issue.id), 240, state);
  const field = shortenDiagnostic(options.field, 240, state);
  const result = (count) => {
    const next = Math.min(offset + count, serialized.length);
    const hasMore = next < serialized.length;
    return {
      schema: 'a3d-diagnostics/v1', fullResult: path, mode: 'field',
      id, field, identityTruncated: state.truncated, encoding: 'json', offsetUnit: 'utf16-code-units',
      total: serialized.length, offset, count, nextOffset: hasMore ? next : null, hasMore,
      truncated: state.truncated || offset > 0 || hasMore,
      data: serialized.slice(offset, offset + count),
    };
  };
  // JSON escaping can expand one input character sixfold. Budget the final
  // response, not the raw field or a guessed token count.
  let low = 0;
  let high = requested;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (diagnosticJson(result(middle)).length <= DIAGNOSTIC_BUDGET) low = middle;
    else high = middle - 1;
  }
  const output = diagnosticJson(result(low));
  if (output.length > DIAGNOSTIC_BUDGET || (requested > 0 && low === 0)) {
    fail('Diagnostic metadata cannot fit the output budget. Use --full for explicit unbounded output.');
  }
  process.stdout.write(output);
}

function diagnose(args) {
  const [input, ...selectors] = args;
  if (!input) fail('a3d diagnose requires a compile-result JSON file.');
  // cwd is already resolved by the OS. Resolving its absolute path again
  // lstats ancestors that a session sandbox deliberately denies access to.
  const root = process.cwd();
  let path;
  try {
    // Native realpath starts a relative lookup at cwd, without revisiting its
    // parents, and still resolves symlinks before checking the file boundary.
    path = realpathSync.native(relative(root, resolve(root, input)) || '.');
  } catch (error) {
    fail(`Cannot read compile result: ${error.message}`);
  }
  const fromRoot = relative(root, path);
  if (fromRoot === '..' || fromRoot.startsWith(`..${sep}`) || isAbsolute(fromRoot)) {
    fail('a3d diagnose only reads files inside the current session workspace.');
  }
  const filters = {};
  const options = {};
  const seen = new Set();
  for (let index = 0; index < selectors.length; index += 1) {
    const flag = selectors[index];
    if (seen.has(flag)) fail('Duplicate diagnose option.');
    seen.add(flag);
    if (flag === '--full') {
      options.full = true;
      continue;
    }
    if (!['--code', '--id', '--severity', '--field', '--offset', '--limit'].includes(flag)) {
      fail('Unknown diagnose option; use selectors, --offset/--limit, --field, or --full.');
    }
    const value = selectors[++index];
    if (!value || value.startsWith('--')) fail('A diagnose option is missing its value.');
    if (flag === '--offset' || flag === '--limit') {
      if (!/^(0|[1-9]\d*)$/u.test(value) || !Number.isSafeInteger(Number(value)) || (flag === '--limit' && Number(value) === 0)) {
        fail('Offsets must be nonnegative safe integers and limits must be positive safe integers.');
      }
      options[flag.slice(2)] = Number(value);
    } else if (flag === '--field') {
      options.field = value;
    } else {
      if (flag === '--severity' && !['error', 'warning'].includes(value)) fail('Severity must be error or warning.');
      filters[flag.slice(2)] = value;
    }
  }
  if (options.full && (options.offset !== undefined || options.limit !== undefined)) fail('--full cannot be combined with pagination options.');
  if (options.field && !filters.id) fail('--field requires --id to select exactly one diagnostic.');
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
  if (options.field) {
    if (issues.length !== 1) fail('--field requires exactly one matching diagnostic.');
    emitDiagnosticField(issues[0], path, options);
  } else if (options.full) {
    process.stdout.write(diagnosticJson({
      schema: 'a3d-diagnostics/v1', fullResult: path, mode: 'issues', full: true,
      total: issues.length, count: issues.length, hasMore: false, nextOffset: null,
      truncated: false, projectionTruncated: false, issues,
    }));
  } else {
    emitDiagnosticPage(issues, path, options);
  }
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
const workspaceCommands = new Set(['compile', 'draft', 'measure', 'compare']);
if (workspaceCommands.has(command) && args.some((arg) => arg === '--workspace' || arg.startsWith('--workspace='))) {
  console.error(`a3d ${command} fixes --workspace to the current session directory.`);
  process.exit(2);
}

const scriptArgs = [join(skillRoot, commands[command]), ...args];
if (workspaceCommands.has(command)) scriptArgs.push('--workspace', process.cwd());
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
