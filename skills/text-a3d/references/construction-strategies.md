# Construction strategies

Choose a geometry strategy from evidence type instead of forcing every request
through the same primitive stack.

| Evidence | Preferred construction | Avoid |
|---|---|---|
| exact dimensions/drawing | datum-driven solids and explicit cuts | estimating what is already specified |
| clean orthographic silhouette | traced profile, constrained extrusion, then depth features | many hand-placed boxes |
| pixel/icon source | deterministic occupied-cell union or relief | manually copied cells |
| single product photo | primary envelope, landmark solids, then restrained hidden-side inference | claiming unseen details are exact |
| organic/sculptural subject | a canonical watertight SDF mesh master driven by semantic landmarks | a scaled sphere/ellipsoid or hundreds of primitives |
| organic exterior plus precise interfaces | an organic mesh shell part assembled to independent BRep mechanical parts | fusing the parts and still claiming editable STEP authority |

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
when the user did not fix the final size; revise the driving envelope dimensions
before construction while preserving printable feature sizes. Use `multipart`
only when separate printed parts create a real manufacturing benefit: cleaner
support strategy, better
strength orientation, post-installed components, functional movement, or
separable covers, inserts, hinged joints, retained closures, or slides inferred
from the object. Model in dependency order:

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

Give every measured feature a stable ID. Use `checked_union()` for additive
features and `checked_cut()` for subtraction. Both measure the material effect;
the union also requires one connected solid. Use `observe()` for geometry that
needs separate evidence before it disappears into a later operation. A
declared cavity, pocket, recess, seat, or keepout is made by applying its cutter
to the owning body; observing the cutter alone does not create the feature.
For a functional port or connector opening serving an internal item, use one
cutter that creates a continuous path from the declared exterior face into the
target interior cavity or keepout. Extend it through the full wall thickness
and beyond both boundaries before `checked_cut()`; do not substitute a shallow
surface recess. The installed item may be display-only, but its opening belongs
to the manufactured body. After the cut, add support, stops, retention, and a
feasible insertion path when the intended assembly needs them. Failed
operations identify the caller-supplied feature and part instead of silently
continuing with an unchanged or disconnected body.

For multipart work, give each printed part its own envelope, features, and
mating-interface parameters. Every interface must be a printable connector or
locating surface, not a visual seam: tab-slot, peg-socket, pin-socket,
dovetail, snap-fit, press-fit, self-tapping-screw, threaded-insert, or
glue-face. Declare the
connection, assembly axis, clearance, engagement depth, and feature IDs for
the modeled connector geometry. If a printable connector cannot be made
reliable, change strategy: keep the object single-part, move the split, alter
orientation, use relief/engraving instead of a separate insert, or record a
non-printed fastening choice. Keep the parts as separate valid solids and
export with `export_assembly()`. It writes `NAME-PART.stl` for individual
print placement, `NAME-PART.step` for each BRep master, `NAME.stl` for
print-bed layout, `NAME-assemble.step` for whole-assembly QA, and
`NAME-display.glb` for user preview. Pass
`part_name=` to every `observe()`, checked cut, and checked finish so per-part
QA reads only its own evidence.

Read `multipart-basics.md` for every multipart design. Load the detailed
`multipart-connections.md` recipe only when the design uses direct fastening
into printed plastic or the positive serviceable-enclosure default applies.

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

If build123d cannot represent an identity-bearing organic surface faithfully,
use `organic_shell.build_organic_shell(...)`. SDF means **Signed Distance
Field**: a function returning distance in millimetres, positive inside the
form, zero on its boundary, and negative outside. It can encode any asymmetric
user-driven form; it is not an ellipse type. Keep precise mechanical structure
as independent BRep-master parts and assemble it to the shell. The mesh remains
the physical authority for the shell, so do not fabricate a faceted STEP or
maintain a separate polished visual proxy.

Choose the cavity while constructing the shell. Use an exterior-connected
opening for serviceable cavities. For a deliberately sealed void printed in
+Z, use the self-supporting cavity helper so the footprint shrinks on each layer
to a sloped apex instead of ending in a flat suspended ceiling. The helper also
requires mesh edge length no greater than half the requested wall thickness;
do not generate a coarse mesh and attempt to heal it later.
