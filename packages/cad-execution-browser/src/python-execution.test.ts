import { describe, expect, it } from 'vitest';

import { PYTHON_BUILD_SCRIPT } from './python-execution';

describe('trusted Python execution envelope', () => {
  it('enters a workspace pre-created for profile assets', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'workspace.mkdir(parents=True, exist_ok=True)',
    );
  });

  it('uses frozen region IDs as multi-color machine keys', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'expected_regions = {item["id"]: item for item in color_plan.get("regions", [])}',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('"regionName": region_id');
  });

  it('treats failed or partial finishing operations as build failures', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain('"finishing-issues"');
    expect(PYTHON_BUILD_SCRIPT).toContain('issue.startswith("ROUND_FAILED")');
    expect(PYTHON_BUILD_SCRIPT).toContain('issue.startswith("BEVEL_PARTIAL")');
  });

  it('exports every body of a multi-body single-color design', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain('parts = report.get("parts")');
    expect(PYTHON_BUILD_SCRIPT).toContain('"assembly-components"');
    expect(PYTHON_BUILD_SCRIPT).toContain('"assembly-body-overlap"');
    expect(PYTHON_BUILD_SCRIPT).toContain('"assembly-overlap-known"');
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'issue.startswith("OVERLAP_UNKNOWN")',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('f"cad-{part_name}-component-count"');
    expect(PYTHON_BUILD_SCRIPT).toContain('warning=cad_solid_count != 1');
    expect(PYTHON_BUILD_SCRIPT).toContain('f"cad-{part_name}-shape-valid"');
    expect(PYTHON_BUILD_SCRIPT).toContain('stl_path,\n                1,');
    expect(PYTHON_BUILD_SCRIPT).not.toContain('part.get("solid_count") or 1');
    expect(PYTHON_BUILD_SCRIPT).toContain('f"mesh-{part_name}-{item[\'id\']}"');
    expect(PYTHON_BUILD_SCRIPT).toContain(
      '"fileName": Path(part["stl_file"]).name',
    );
  });

  it('audits frozen mechanism bodies across sampled exported-STEP motion', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'mechanism_specs = json.loads(_amagine_mechanisms_json)',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('motion_sample_count(motion)');
    expect(PYTHON_BUILD_SCRIPT).toContain('transform_motion(');
    expect(PYTHON_BUILD_SCRIPT).toContain('pairwise_collision_volume(');
    expect(PYTHON_BUILD_SCRIPT).toContain('distance_to(');
    expect(PYTHON_BUILD_SCRIPT).toContain('"worstCollision"');
    expect(PYTHON_BUILD_SCRIPT).toContain('"minimumPose"');
    expect(PYTHON_BUILD_SCRIPT).toContain('"maximumPose"');
    expect(PYTHON_BUILD_SCRIPT).toContain('"motionIndex": motion_index');
    expect(PYTHON_BUILD_SCRIPT).toContain('"mechanismReports"');
    expect(PYTHON_BUILD_SCRIPT).toContain('-motion-collision');
    expect(PYTHON_BUILD_SCRIPT).toContain('-clearance-');
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'actual_body_ids == expected_body_ids',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('_trusted_import_step');
  });

  it('checks frozen feature measurements and observed keep-out volumes', () => {
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'feature_specs = json.loads(_amagine_feature_checks_json)',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('observed_feature_metric(');
    expect(PYTHON_BUILD_SCRIPT).toContain('f"feature-{target[\'id\']}"');
    expect(PYTHON_BUILD_SCRIPT).toContain(
      'report.get("keep_outs", {}).items()',
    );
    expect(PYTHON_BUILD_SCRIPT).toContain('f"keep-out-{keep_out_id}"');
  });
});
