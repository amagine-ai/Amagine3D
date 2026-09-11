# Construction strategies

Select the BRep construction with `a3d guide strategy`. This reference
owns the downstream feature graph and constructive geometry rules.

## Frame and feature graph

An unbound draft may establish silhouette, proportion and primary volumes before
these declarations. Before contract-bound final geometry, declare the fixed
object semantic frame in intent: `+X` is user right, `+Y` is object back, `+Z` is
object top, front is `Y-min`, and bottom is `Z-min`. Declare flat semantic feature
fields for every final port, hole, slot, cutout, window, cavity or recess:
`kind`, `face`, `direction`, and `edge_crossing`. Declare `manufacturing.mode`
before fixing final part topology. Use `a3d guide multipart` only when separate
manufacture, assembly, disassembly, maintenance or a real mating interface is
relevant.
`edge_crossing` refers to crossing an edge between exterior
faces, not piercing wall thickness; a through opening contained within one face
uses `forbidden`.

Build in dependency order:

1. primary envelope
2. identity-bearing additive volumes
3. functional openings/recesses
4. small controls/details
5. finishes

For appearance-led work, review the primary envelope and defining volumes as soon
as they can be rendered, using the current preview or diagnostic preview. Compare
their silhouette and proportions with the brief and actually viewed references
before developing dependent openings and details. Follow the main skill's
conditional reference-image guidance; a dimension-driven part does not need
this visual-reference workflow.

For an LED/LCD or another installed display component, read
`installed-displays.md`. Do not load that component-specific guidance for tasks
without an installed display.

For replica or exact-match requests, build the object first and the print
placement second. A support-free bed pose is not permission to flatten the
source model, delete underside volume, or make a relief while declaring
`full-3d`. Model meaningful bottom, back, side, handle, and underside geometry
from evidence or explicit assumptions, then rotate the finished body for
printing if that improves support behavior.

Give every measured feature a stable ID. Use
`checked_union()` for additions, `checked_cut()` for subtraction, and `observe()`
for geometry that needs separate evidence before a later operation. The checked
operations measure the material effect; the union requires one connected solid.
Bind those same BRep objects as physical nodes in `write_scene(...)`. A cavity,
pocket, recess, seat, or keepout comes from applying its cutter to that body.

For a functional port or connector opening serving an internal item, use one
cutter that creates a continuous path from the declared exterior face into the
target interior cavity or keepout. Extend it through the full wall thickness
and beyond both boundaries, then apply it with `checked_cut()`.
The installed item may be display-only, but its opening belongs to the
manufactured body. After the cut, add support, stops, retention, and a
feasible insertion path when the intended assembly needs them. Failed
operations identify the caller-supplied feature and part instead of silently
continuing with an unchanged or disconnected body.

For multipart work, give each printed part its own envelope, features, and
mating-interface parameters. Use `a3d guide multipart` for interface selection
and clearance semantics. If a printable connector cannot be made reliable,
change the split, orientation, or fastening strategy. Keep the parts as separate
valid BRep solids. Use `export_assembly()` and pass `part_name=` to each
`observe()`, checked cut, and checked finish. Declare each part and its bound
nodes through `write_scene(...)`. The public compile path provides genuine
STEP masters, individual print meshes, the print-bed layout, 3MF and display GLB.

Load `multipart-connections.md` only for direct fastening into printed plastic
or the serviceable-enclosure closure described there.

## build123d guardrails

- Primitive alignment is explicit. Print artifacts are normalized to Z0 by the
  exporter; assembly STEP and display GLB files preserve object/assembly intent.
- Cutting tools extend beyond both target faces to avoid coplanar ambiguity.
- Define named datum variables for semantic faces, such as `FRONT_Y`,
  `BACK_Y`, `BOTTOM_Z`, and `TOP_Z`, then derive cut positions from those
  names. Do not scatter unexplained signed coordinates through the source.
- Select finish edges by semantic geometry or position. `checked_fillet()` and
  `checked_chamfer()` are strict by default; allow reduction only when the
  contract permits it, then report the actual size. Keep operation failures
  visible; do not wrap a failed finish in an exception handler that returns the
  unchanged input solid. Correct its edge selection or construction and check
  the resulting shape.
- Express intended symmetry through mirrored geometry or shared parameters.
- Keep source parameters tied to evidence IDs so repairs remain traceable to the
  affected design relationships, including when rebuilding dependent features.

For a thin extruded cutter, distinguish its in-plane corner radius from blends
across its thickness. A rounded footprint can come from a rounded profile or
fillets on extrusion-direction edges. An all-edge fillet may fail because of the
thickness even when the intended footprint is feasible. Choose a construction
that retains the required profile rather than automatically shrinking its radius
or squaring its corners.

## Representation checks

`full-3d` needs plausible side/top/bottom depth and no facade-only bulk.
`relief` and `orthographic-solid` intentionally prioritize one view but must
state thickness. `surface-led` is appropriate when the recognizable form depends
on a controlled outer surface more than internal mechanics.

For an appearance-led envelope, start with a few key sections and a BRep loft;
`surface-design.md` covers section controls and transitions. Ruled lofts and
joined simpler BRep volumes are suitable when a coarser surface preserves the
intended form. An unstable construction may need different profile controls,
corrected section correspondence, a different solid decomposition, or a rebuilt
surface strategy. Choose by the resulting contours and transitions, retaining
the geometry and QA targets tied to the original landmarks.

Develop the cavity with the outer form. A separate inner loft can be subtracted
from the outer solid; validate actual wall thickness rather than treating
section inset as a normal-offset guarantee. An exterior-connected opening
provides service access. For a sealed void intended for support-free printing,
shape the internal roof with suitable slopes or arches and check the chosen
print orientation.
