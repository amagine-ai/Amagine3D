# Construction strategies

Select the high-level representation with `a3d guide strategy`. This reference
owns the downstream feature graph and constructive geometry rules.

## Frame and feature graph

Declare the fixed object semantic frame in the intent contract: `+X` is user
right, `+Y` is object back, `+Z` is object top, front is `Y-min`, and bottom is
`Z-min`. Declare flat semantic feature fields before modeling: `kind`, `face`,
`direction`, and `edge_crossing` for every port, hole, slot, cutout, window,
cavity, or recess. Declare `manufacturing.mode` before modeling. Use
`a3d guide multipart` when separate manufacture or assembly is relevant. Model
in dependency order:

1. primary envelope
2. identity-bearing additive volumes
3. functional openings/recesses
4. small controls/details
5. finishes

For an LED/LCD or another installed display component, read
`installed-displays.md`. Do not load that component-specific guidance for tasks
without an installed display.

For replica or exact-match requests, build the object first and the print
placement second. A support-free bed pose is not permission to flatten the
source model, delete underside volume, or make a relief while declaring
`full-3d`. Model meaningful bottom, back, side, handle, and underside geometry
from evidence or explicit assumptions, then rotate the finished body for
printing if that improves support behavior.

Give every measured feature a stable ID. For BRep construction, use
`checked_union()` for additions, `checked_cut()` for subtraction, and `observe()`
for geometry that needs separate evidence before a later operation. The checked
operations measure the material effect; the union requires one connected solid.
For a mesh-master part, bind its mesh body and any mesh or BRep additions/cutters
as physical nodes in `write_scene(...)`; hybrid compile applies those operations
to the owning body. In either path, a cavity, pocket, recess, seat, or keepout
comes from applying its cutter to that body.

For a functional port or connector opening serving an internal item, use one
cutter that creates a continuous path from the declared exterior face into the
target interior cavity or keepout. Extend it through the full wall thickness
and beyond both boundaries, then apply it through the chosen BRep or mesh path.
The installed item may be display-only, but its opening belongs to the
manufactured body. After the cut, add support, stops, retention, and a
feasible insertion path when the intended assembly needs them. Failed
operations identify the caller-supplied feature and part instead of silently
continuing with an unchanged or disconnected body.

For multipart work, give each printed part its own envelope, features, and
mating-interface parameters. Use `a3d guide multipart` for interface selection
and clearance semantics. If a printable connector cannot be made reliable,
change the split, orientation, or fastening strategy. Keep the parts as separate
valid solids. In the BRep helper authoring path, use `export_assembly()` and pass
`part_name=` to each `observe()`, checked cut, and checked finish. In a hybrid
scene, declare each part and its bound nodes through `write_scene(...)` and let
`a3d compile` publish the artifacts. Both paths provide individual print meshes,
the print-bed layout, 3MF and display GLB; BRep masters also provide STEP.

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
  contract permits it, then report the actual size.
- Express intended symmetry through mirrored geometry or shared parameters.
- Keep source parameters tied to evidence IDs so a repair changes one declared
  cause instead of patching unrelated coordinates.

## Representation checks

`full-3d` needs plausible side/top/bottom depth and no facade-only bulk.
`relief` and `orthographic-solid` intentionally prioritize one view but must
state thickness. `surface-led` is appropriate when the recognizable form depends
on a controlled outer surface more than internal mechanics.

When an organic shell benefits from distance-field controls, use
`organic_shell.build_organic_shell(...)`. SDF means **Signed Distance
Field**: a function returning distance in millimetres, positive inside the
form, zero on its boundary, and negative outside. It can encode any asymmetric
user-driven form; it is not an ellipse type. Keep precise mechanical structure
as build123d geometry. Bind a feature directly into the mesh-master part when it
must be fused or cut; keep it as an independent BRep master only when it is a
separate printed part. The fused mesh remains the physical authority, so do not
fabricate a faceted STEP or maintain a separate polished visual proxy.

For a hollow construction, develop the cavity with the outer form. An
exterior-connected opening provides access to a serviceable cavity. For a sealed
void intended for support-free printing in +Z,
`organic_shell.self_supporting_cavity(...)` shapes the footprint to shrink on
each layer toward a sloped apex. `build_organic_shell(...)` requires mesh edge
length no greater than half the requested wall thickness to resolve that wall.
