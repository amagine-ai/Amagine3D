# Amagine3D single-material enclosure workflow

This profile produces one or more separately printable bodies for a functional
hardware enclosure. The deliverable is editable build123d source plus STEP and
STL artifacts that pass the host's deterministic checks.

## Design contract

Before writing geometry, turn the request into a design brief that distinguishes
four kinds of information.

- User constraints are authoritative dimensions or requirements stated by the
  user.
- Agent assumptions fill gaps and must include a reason.
- Research hints are advisory and never override a user constraint.
- Verification targets are quantities the deterministic runtime can measure.

Use millimetres. Place the footprint centre at X=0 and Y=0, with the printable
bottom at Z=0. List every separately printable body and describe how it mates
with the others.

The body count is the number of physical pieces that will be printed
separately, not a count observed from faulty geometry. A removable-pin hinge is
normally three bodies: base, lid and pin. Record that intended count before
modeling and do not change it to match a failed build.

Use a success-first complexity budget. The first build should contain only the
primary shell bodies, the selected closure interface and cavities explicitly
needed for fit. Do not invent battery bays, circuit channels, magnet pockets,
contact sockets, decorative seams or broad edge finishing merely because the
product category suggests them. After structural and assembly QA passes, add
optional feature families one at a time without changing the proven closure.

## Printability rules

- Structural walls should normally be 2 to 3 mm and must not be thinner than
  1.2 mm without an explicit user requirement.
- Add 0.3 to 0.5 mm clearance per mating side for ordinary FDM assembly.
- Split lids, pins, gears, captive parts and moving joints into separate bodies
  when printing them together would fuse the mechanism.
- When the request includes a lid, door, hinge, latch or another opening/closing
  interface, follow the host-supplied printable-closure guide. Record the chosen
  mechanism and any substitution as an agent assumption. Freeze the complete
  body partition, ordered rigid motions and measurable running-clearance pairs
  in `mechanisms`; closure QA is incomplete without those entries.
- Avoid sealed support cavities and unsupported overhangs above roughly 45
  degrees unless the brief specifies a support strategy.
- Use clearance holes appropriate to the intended fastener. Typical printable
  starting points are 3.4 mm for M3, 4.5 mm for M4 and 5.5 mm for M5.
- Keep antenna volumes, connector insertion paths, cable bends and button travel
  free of enclosure material.

Freeze exact hole, interface and datum dimensions as `featureChecks`, then call
`observe_feature` with the matching feature ID before its cutter or construction
solid loses identity. Model every reserved connector insertion, cable bend,
antenna or button-travel volume as a construction solid and record it with
`feature_type="keep-out"`; the Worker intersects that volume with every final
published body and fails any overlap above 0.01 mm³.

## Source contract

Write one complete `model.py`. Import build123d names explicitly or with its
public star import. Import host operations from `amagine_cad`.

```python
from build123d import Align, Box
from amagine_cad import (
    bevel_edges_checked,
    observe_feature,
    publish_model,
    round_edges_checked,
    subtract_checked,
)
```

Every geometry-driving number must be a top-level uppercase literal with an
adjacent `# @param` annotation. Build additive geometry first, perform
subtractions next, and apply rounds or bevels last. Use `observe_feature` before
a feature loses its identity in a boolean operation. Observation IDs referenced
by frozen `featureChecks` must match exactly.

Use `subtract_checked` for cuts. Use `round_edges_checked` and
`bevel_edges_checked` for finishing. Finish with exactly one call to
`publish_model`.

```python
publish_model(body, "sensor-enclosure", out_dir="cad_out")
```

For an assembly, pass stable machine-safe body IDs.

```python
publish_model(
    {"base": base, "lid": lid, "hinge-pin": pin},
    "sensor-enclosure",
    out_dir="cad_out",
)
```

Each dictionary value must be exactly one connected, printable, watertight
solid in both the build123d shape and its exported STL. A dictionary entry named
`base` that contains several disconnected shells is not one valid base. Fuse
every lug, boss, rib and hinge knuckle into its owning body with positive-volume
overlap; if a component is intentionally separate, give it another dictionary
key and export it as another printable body. Do not combine bodies that must
move or be assembled later.

For `buildAndCheck`, `qaTargets.componentCount` is the intended number of named
printable bodies passed to `publish_model`, not `len(shape.solids())` measured
from the generated result. Every named body still has an independent expected
connected-component count of one. For a closure, the host overrides this target
with the frozen mechanism partition's body count and independently compares the
published IDs, so an accidentally fused or omitted part cannot self-validate.

## Repair policy

Treat boolean no-effect, boolean failure, failed or partial finishing, invalid
shape, open mesh, wrong component count and dimension mismatch as repairable
failures. Use the host's measured values and issue codes to change the source.
For a per-body component-count failure, repair only the additive attachment that
failed to fuse or publish the intentionally separate component under its own
name. Never raise the expected count to accept floating geometry.
Do not claim success until the host accepts the exact artifact set.
