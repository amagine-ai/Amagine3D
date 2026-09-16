#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const python =
  process.platform === 'win32'
    ? join(root, '.venv', 'Scripts', 'python.exe')
    : join(root, '.venv', 'bin', 'python');

const moduleName = (name) => `tests.python.${name}`;
const methodName = (name) =>
  `${moduleName('test_authoring_example')}.PublicAuthoringExampleTests.${name}`;

const unitA = [
  'test_brep_measurements',
  'test_build_manifest',
  'test_cad_compile',
  'test_cad_draft',
  'test_display_glb_normals',
  'test_interface_recipes',
  'test_mesh_topology',
  'test_render_preview_materials',
  'test_source_preflight',
  'test_text_a3d_printability',
  'test_text_a3d_scene_contract',
];
const unitB = [
  'test_assembly_orientation',
  'test_brep_envelope',
  'test_brep_stl_normalization',
  'test_brep_tessellation',
  'test_build_session',
  'test_cad_diagnostics',
  'test_capability_manifest',
  'test_display_components',
  'test_freshness_check',
  'test_installation_audit',
  'test_installation_check',
  'test_intent_revisions',
  'test_interface_geometry',
  'test_mesh_normalization',
  'test_opening_placement',
  'test_plate_layout',
  'test_print_orientation',
  'test_print_plates',
  'test_self_tapping_connections',
  'test_skill_shared_files',
  'test_source_diagnostics',
  'test_text_a3d_authoring',
  'test_text_a3d_color_guardrails',
  'test_visual_compare',
];

const suites = {
  'cad-basic': [
    methodName('test_example_compiles_with_measured_interface_and_five_views'),
    methodName('test_module_resize_keeps_locator_in_material_and_screws_at_corners'),
    methodName('test_single_part_starter_exports_a_real_blind_pocket'),
  ],
  'cad-module': [
    methodName('test_cover_thickness_edit_keeps_through_holes_and_interface_on_current_control'),
    methodName('test_installation_controls_import_without_geometry_and_do_not_rewrite_targets'),
    methodName('test_installed_module_exports_two_parts_with_installation_and_screw_proofs'),
  ],
  'cad-surface': [
    methodName('test_surface_shell_recompiles_changed_walls_without_rewriting_intent'),
  ],
  'cpu-renderer': [moduleName('test_cpu_z_buffer')],
  'unit-a': unitA.map(moduleName),
  'unit-b': unitB.map(moduleName),
};

function validateCoverage() {
  const files = readdirSync(join(root, 'tests', 'python'))
    .filter((name) => /^test_.*\.py$/u.test(name))
    .map((name) => basename(name, '.py'))
    .sort();
  const assigned = [...unitA, ...unitB, 'test_authoring_example', 'test_cpu_z_buffer'];
  const duplicates = assigned.filter((name, index) => assigned.indexOf(name) !== index);
  const missing = files.filter((name) => !assigned.includes(name));
  const unknown = assigned.filter((name) => !files.includes(name));
  if (duplicates.length || missing.length || unknown.length) {
    throw new Error(
      `Python suite manifest is invalid: ${JSON.stringify({ duplicates, missing, unknown })}`,
    );
  }
}

validateCoverage();

const args = process.argv.slice(2);
let pythonArgs;
if (args.length === 0) {
  pythonArgs = ['-m', 'unittest', 'discover', '-s', 'tests/python', '-p', 'test_*.py'];
} else if (args.length === 2 && args[0] === '--suite' && suites[args[1]]) {
  pythonArgs = ['-m', 'unittest', ...suites[args[1]]];
} else if (args.length === 1 && args[0] === '--list-suites') {
  console.log(Object.keys(suites).sort().join('\n'));
  process.exit(0);
} else {
  console.error(`Usage: node scripts/test-python.mjs [--suite ${Object.keys(suites).sort().join('|')}]`);
  process.exit(2);
}

const result = spawnSync(python, pythonArgs, { cwd: root, stdio: 'inherit' });
if (result.error) {
  console.error(`Python tests could not start: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
