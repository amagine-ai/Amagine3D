# Bambu FDM printability

Use the resolved Bambu profile as a manufacturing constraint, not as a report
label. Preserve user dimensions and the selected profile throughout a repair
loop.

This workflow validates CAD geometry and manufacturing exports. Run an actual
slicer only when requested; one plate is not a requirement unless specified.

## Profile decisions

- Resolve one machine, nozzle, standard process, and tool before geometry.
- Prefer an explicit user or project selection. Otherwise use the common A1
  with a 0.4 mm nozzle and record the assumption.
- For dual-tool machines, use the selected tool's polygon and height rather
  than the union of both tool envelopes.
- Never switch profiles, lower limits, or scale user dimensions to clear QA.

Before detailed construction, plan the proposed **per-part bounds in a selected
print orientation** against that profile, including exclusions, clearance
between parts, edge margin and allowed plate count. Keep three measurements
distinct: assembled semantic envelope, each printable part, and arranged plate.
Use `a3d layout BOUNDS.json --profile PROFILE.json --max-plates N
--edge-margin-mm N --out PLAN.json`; the bounds file maps part IDs to
`{"min":[x,y,z],"max":[x,y,z]}`. Plan estimates early and recheck measured geometry
at export. A successful footprint plan is not a manufacturing audit.

The planner tries translations and 90-degree turns about the build axis, then
proposes separate plates up to the stated limit. It distinguishes a single
part exceeding the usable volume from a heuristic that has not found a layout.
The latter is not proof of impossibility. The current `export_assembly` complete
manufacturing package still requires one plate; a multi-plate plan alone does
not constitute exported, audited multi-plate 3MF delivery. A failed export keeps
hash-bound semantic STEP/GLB and a diagnostic preview where generation succeeds,
without declaring manufacture complete. Use that evidence to improve packing
or plan the required export work, keeping the selected printer and target form.

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
helper is in print coordinates with `Z-min = 0`; every `NAME-PART.step` and the
required `NAME-assemble.step` preserve physical mating positions, while
`NAME-display.glb` preserves the display model instead of acting as
printability evidence.

## BRep enclosure shells

Choose print direction and cavity topology while developing the BRep solid.
Use a few key loft sections for the outer form and a separately controlled inner
loft or valid BRep offset for the cavity.

- Make a housing's service opening connect the cavity to the exterior so
  supports and loose debris can be removed. Keep a separately printed cover as
  its own BRep part.
- Inspect measured wall thickness at shoulders, corners and the floor. An inset
  within each section is not constant 3D normal thickness. If the inner form
  collapses or leaves thin walls, adjust its profiles, radii or section spacing.
- For a deliberately sealed void, shape its roof with slopes or arches suited
  to the selected support angle and print orientation. A valid solid alone does
  not prove its internal ceiling is printable without supports.
- Choose a broad, intentional bed-contact region or a permitted assembly split.
  Do not flatten identity-bearing outer geometry merely to obtain first-layer
  contact.
- Construct sockets, locating faces, bosses, covers, and other
  tolerance-bearing features as BRep geometry. Use checked unions and cuts on
  the owning solid, then verify its exported STEP and derived print mesh.

## Support-free construction

The profile's support angle is measured up from the horizontal plane: 0 degrees
is a horizontal underside, and 90 degrees is a vertical wall. This section owns
print-pose and support-risk decisions; choose the semantic geometry separately
with `a3d guide strategy`.

- Reorient the build without changing required dimensions.
- Evaluate all six bed-facing orientations, including a top-down 180-degree
  flip. Profile fit is a hard gate; among fitting poses, bed contact and a
  supported center of mass outrank support burden and print height. Assembly
  export retries alternate stable poses when preferred poses cannot share a
  plate; a bounded search failure still does not prove impossibility.
  Candidate evidence records
  the uniform scale required to fit the selected profile as diagnostic evidence;
  it does not authorize resizing. An inferred dimension remains fixed unless an
  explicit allowed range was declared. Adjust within that range or make a
  justified requirement revision; do not rewrite the target to match failure.
- Add slopes, chamfers, or arches only when they are faithful to the requested
  object or explicitly accepted as a manufacturing compromise.
- Use chamfers or arches under ledges and teardrop profiles for horizontal
  holes.
- Split the model when the intent contract permits assembly; prefer this over
  hiding unavoidable overhangs in a one-piece body.
- When geometry cannot be made support-free, set `support_policy` to
  `supports-required` and disclose the reported regions.

## Repair QA evidence

Read every failed or warning check and use its `repair` object as diagnostic
input, not as an automatic command to change geometry. Printability advisories
exist to catch coarse process risks. They should drive source repair only when
the observed issue is broad or severe enough that the print process is likely
to fail.

- `printability_bed_fit`: try the reported XY rotation, a permitted build
  orientation or plate grouping on the selected machine. A genuine single-part
  size conflict requires a justified design/requirement decision, not an
  automatic printer change.
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
warnings. Use repeated or regressed diagnostics to reconsider the construction
strategy, but do not impose a fixed retry count that can truncate a complex
model. Preserve and report the latest evidence honestly.
