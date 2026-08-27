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
then audit `<name>.stl` as the arranged print-bed layout and use
`assembly_check.py` for report integrity. Every printed STL that leaves the
helper is in print coordinates with `Z-min = 0`; `NAME-assemble.step` preserves
physical mating positions and `NAME-display.glb` preserves the display model
instead of acting as printability evidence.

## Support-free construction

The profile's support angle is measured up from the horizontal plane: 0
degrees is a horizontal underside, and 90 degrees is a vertical wall. Prefer a
support-free result only after the requested shape is preserved; STL and STEP do
not carry a Bambu Studio support plan, but printability cannot redefine the
object.

- Reorient the build without changing required dimensions.
- Evaluate all six bed-facing orientations, including a top-down 180-degree
  flip. Profile fit is a hard gate; among fitting poses, support burden and
  stable contact outrank minimizing print height. Candidate evidence records
  the uniform scale required to fit the selected profile; use it only when
  dimensions are inferred and update the intent, semantic model, and parameters
  together before rebuilding.
- Add slopes, chamfers, or arches only when they are faithful to the requested
  object or explicitly accepted as a manufacturing compromise.
- Use chamfers or arches under ledges and teardrop profiles for horizontal
  holes.
- Split the model when the intent contract permits assembly; prefer this over
  hiding unavoidable overhangs in a one-piece body.
- When geometry cannot be made support-free, set `support_policy` to
  `supports-required` and disclose the reported regions.
- Never make a full-3D replica flat-backed, remove underside detail, or alter a
  handle/head cross-section solely to clear overhang checks.

## Repair QA evidence

Read every failed or warning check and use its `repair` object as diagnostic
input, not as an automatic command to change geometry. Printability advisories
exist to catch coarse process risks. They should drive source repair only when
the observed issue is broad or severe enough that the print process is likely
to fail.

- `printability_bed_fit`: try the reported XY rotation or a permitted build
  orientation; otherwise request a larger supported Bambu machine.
- `printability_feature_resolution`: widen the named source feature to at
  least `single_line_floor_mm` when the feature is critical or intentionally
  manufactured; disclose tiny cosmetic detail risk instead of redesigning the
  object.
- `printability_wall_thickness`: repair globally thin shells, broad sampled
  violations, load-bearing walls, or critical features. Local cosmetic risk
  may remain a warning.
- `printability_overhang`: repair only when support-free output is a user
  requirement or support demand is excessive. Ordinary local overhangs should
  become a disclosed support requirement, not a reason to flatten, move,
  thicken, or simplify the object.
- `not_evaluated`: restore the missing profile, report, or valid watertight
  geometry. Never treat it as a pass.

Do not optimize for warning-free QA. Preserve identity-bearing geometry,
expected feature relationships, and visual landmarks over eliminating advisory
warnings. At most three evidence-repair passes are allowed. At the limit,
preserve the latest evidence and report `pass_with_warnings` or `fail`
honestly.
