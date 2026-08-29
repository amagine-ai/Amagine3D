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
`single-part` when the object can be one reliable manufacturing body. A
printed prop or figure may still have semantic sub-parts, such as a handle and
head, without becoming a multipart print if those volumes are fused with
adequate section and fillets. Do not split solely to fit a default printer
when the user did not fix the final size; scale the whole model first while
preserving printable feature sizes. Use `multipart` only when separate printed
parts create a real manufacturing benefit: cleaner support strategy, better
strength orientation, post-installed components, functional movement, or
separable covers, inserts, hinges, latches, or slides inferred from the
object. Model in dependency order:

1. primary envelope
2. identity-bearing additive volumes
3. functional openings/recesses
4. small controls/details
5. finishes

For replica or exact-match requests, build the object first and the print
placement second. A support-free bed pose is not permission to flatten the
source model, delete underside volume, or make a relief while declaring
`full-3d`. Model meaningful bottom, back, side, handle, and underside geometry
from evidence or explicit assumptions, then rotate the finished body for
printing if that improves support behavior.

Give every measured or subtractive feature a stable ID. Call `observe()` before
union and `checked_cut()` for subtraction. Failed operations raise immediately;
do not continue with an unchanged body.

For multipart work, give each printed part its own envelope, features, and
mating-interface parameters. Every interface must be a printable connector or
locating surface, not a visual seam: tab-slot, peg-socket, pin-socket,
dovetail, snap-fit, press-fit, threaded-insert, or glue-face. Declare the
connection, assembly axis, clearance, engagement depth, and feature IDs for
the modeled connector geometry. If a printable connector cannot be made
reliable, change strategy: keep the object single-part, move the split, alter
orientation, use relief/engraving instead of a separate insert, or record a
non-printed fastening choice. Keep the parts as separate valid solids and
export with `export_assembly()`. It writes `NAME-PART.stl` for individual
print placement, `NAME.stl` for print-bed layout, `NAME-assemble.step` for
physical assembly QA, and `NAME-display.glb` for user preview. Pass
`part_name=` to every `observe()`, checked cut, and checked finish so per-part
QA reads only its own evidence.

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

`full-3d` needs plausible side/top/bottom depth and no facade-only bulk.
`relief` and `orthographic-solid` intentionally prioritize one view but must
state thickness. `surface-led` is appropriate when the recognizable form depends
on a controlled outer surface more than internal mechanics.

If build123d cannot represent the requested organic surface faithfully, stop at
an honest failed visual validation rather than hiding the limitation behind a
watertight STL.
