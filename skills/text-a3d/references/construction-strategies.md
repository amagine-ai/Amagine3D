# Construction strategies

Choose a geometry strategy from evidence type instead of forcing every request
through the same primitive stack.

| Evidence | Preferred construction | Avoid |
|---|---|---|
| exact dimensions/drawing | datum-driven solids and explicit cuts | estimating what is already specified |
| clean orthographic silhouette | traced profile, constrained extrusion, then depth features | many hand-placed boxes |
| pixel/icon source | deterministic occupied-cell union or relief | manually copied cells |
| single product photo | primary envelope, landmark solids, then restrained hidden-side inference | claiming unseen details are exact |
| organic/sculptural subject | a small set of lofted profiles or surface-led approximation | hundreds of primitives that create a lumpy silhouette |

## Frame and feature graph

Declare the fixed object semantic frame in the intent contract: `+X` is user
right, `+Y` is object back, `+Z` is object top, front is `Y-min`, and bottom is
`Z-min`. Declare flat semantic feature fields before modeling: `kind`, `face`,
`direction`, and `edge_crossing` for every port, hole, slot, cutout, window,
cavity, or recess. Declare `manufacturing.mode` before modeling. Use
`single-part` unless the user asked for or the object requires separate
same-material pieces such as a lid, cover, insert, hinge leaf, latch, or
sliding member. Model in dependency order:

1. primary envelope
2. identity-bearing additive volumes
3. functional openings/recesses
4. small controls/details
5. finishes

Give every measured or subtractive feature a stable ID. Call `observe()` before
union and `checked_cut()` for subtraction. Failed operations raise immediately;
do not continue with an unchanged body.

For multipart work, give each printed part its own envelope, features, and
mating-interface parameters. Keep the parts as separate valid solids and export
with `export_assembly()`. It writes `NAME-PART.stl` for individual print
placement, `NAME.stl` for print-bed layout, `NAME-assemble.step` for physical
assembly QA, and `NAME-display.glb` for user preview. Pass `part_name=` to
every `observe()`, checked cut, and checked finish so per-part QA reads only
its own evidence. Do not union separate requested parts only because the
material is single color.

## build123d guardrails

- Primitive alignment is explicit. Print artifacts are normalized to Z0 by the
  exporter; assembly STEP and display GLB files preserve object/assembly intent.
- Cutting tools extend beyond both target faces to avoid coplanar ambiguity.
- Define named datum variables for semantic faces, such as `FRONT_Y`,
  `BACK_Y`, `BOTTOM_Z`, and `TOP_Z`, then derive cut positions from those
  names. Do not scatter unexplained signed coordinates through the source.
- Select finish edges by semantic geometry or position. `checked_fillet()` and
  `checked_chamfer()` are strict by default; allow reduction only when the
  contract permits it, then report the actual size.
- Preserve symmetry through mirrored geometry or shared parameters.
- Keep source parameters tied to evidence IDs so a repair changes one declared
  cause instead of patching unrelated coordinates.

## Representation checks

`full-3d` needs plausible side/top depth and no facade-only bulk. `relief` and
`orthographic-solid` intentionally prioritize one view but must state thickness.
`surface-led` is appropriate when the recognizable form depends on a controlled
outer surface more than internal mechanics.

If build123d cannot represent the requested organic surface faithfully, stop at
an honest failed visual validation rather than hiding the limitation behind a
watertight STL.
