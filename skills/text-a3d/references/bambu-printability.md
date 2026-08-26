# Bambu FDM printability

Use the resolved Bambu profile as a manufacturing constraint, not as a report
label. Preserve user dimensions and the selected profile throughout a repair
loop.

## Profile decisions

- Resolve one machine, nozzle, standard process, and tool before geometry.
- Prefer an explicit user or project selection. Otherwise use A1 mini with a
  0.4 mm nozzle as a conservative default and record the assumption.
- For dual-tool machines, use the selected tool's polygon and height rather
  than the union of both tool envelopes.
- Never switch profiles, lower limits, or scale user dimensions to clear QA.

## Design targets

The profile exposes two different width limits:

- `single_line_floor_mm` is the smallest ordinary classic-wall line width.
  Features below it may disappear.
- `process_wall_target_mm` is the outer line plus the requested inner wall
  loops. Use it as the minimum planned shell thickness.

Treat load-bearing walls as a separate functional decision; meeting the
process wall target proves slicer compatibility, not strength. Give every
wall, pin, hole, slot, embossed stroke, recess, chamfer, and fillet a named
parameter tied to a contract feature ID. Call `observe()` on additive feature
solids before union. Use checked operations so subtractive tool bounds and
finish sizes enter the build report.

For same-material multipart assemblies, keep every printed part as one valid
solid, export them with `export_assembly()`, audit each part STL individually,
then run a topology-only component audit on `<name>-combined.stl` and use
`assembly_check.py` for report integrity. A part STL may retain an assembly-space
translation, so evaluate its orientation and dimensions but require Z0 only when
the authored coordinates already describe bed placement. The combined STL is
assembly evidence, not a print-board or per-part printability result.

## Support-free construction

The profile's support angle is measured up from the horizontal plane: 0
degrees is a horizontal underside, and 90 degrees is a vertical wall. Prefer
support-free geometry because STL and STEP do not carry a Bambu Studio support
plan.

- Reorient the build without changing required dimensions.
- Replace shelf undersides with slopes at or above the profile threshold.
- Use chamfers or arches under ledges and teardrop profiles for horizontal
  holes.
- Split the model when the intent contract permits assembly; prefer this over
  hiding unavoidable overhangs in a one-piece body.
- When geometry cannot be made support-free, set `support_policy` to
  `supports-required` and disclose the reported regions.

## Repair QA evidence

Read every failed or warning check and use its `repair` object. Repair the
reported feature IDs or risk bounds, then rebuild and rerun QA.

- `printability_bed_fit`: try the reported XY rotation or a permitted build
  orientation; otherwise request a larger supported Bambu machine.
- `printability_feature_resolution`: widen the named source feature to at
  least `single_line_floor_mm`.
- `printability_wall_thickness`: increase the responsible shell parameter or
  reduce a decorative recess before changing outer dimensions.
- `printability_overhang`: reorient, slope, chamfer, arch, or explicitly require
  supports.
- `not_evaluated`: restore the missing profile, report, or valid watertight
  geometry. Never treat it as a pass.

At most three evidence-repair passes are allowed. At the limit, preserve the
latest evidence and report `pass_with_warnings` or `fail` honestly.
