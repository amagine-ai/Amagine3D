# Printable closure and moving-mechanism design

Apply this guide only when the requested model contains a lid, door, cover,
hinge, latch or another opening/closing interface. These rules supplement the
shared build123d authoring guide.

## Common assembly rules

- Do not model a nominally movable interface as one fused solid. If a joint
  would be locked by fused contact, trapped support material or inaccessible
  assembly geometry, replace it with a post-print assembly and record that
  decision in the design brief.
- Keep a canonical assembled pose for interface validation. Pairwise
  intersection volume in that pose must remain at or below 0.01 mm³; a body can
  be individually watertight yet still be impossible to assemble because it
  collides with its neighbor.
- `buildAndCheck.qaTargets.sizeX`, `sizeY` and `sizeZ` describe the bounding box
  of the complete assembled publication, including every separate body,
  external knuckle, pin head, latch and other protrusion. Never pass a shell-only
  dimension as an assembly target. If the user specifies only the shell size,
  keep it as a named shell parameter or observed feature and either derive an
  explicit full-envelope target or omit that assembly target.
- Treat assembly pose and print pose separately. After interface validation,
  orient every exported body for printing and place its selected printable
  bottom at Z=0. Record any transform between assembly and print poses.
- Check the complete insertion, sliding or rotation path, not only the final
  closed pose. Keep tools, pins, fasteners and removable parts accessible in the
  required assembly order.
- Freeze every rigid path in the design brief's `mechanisms` array. The union of
  `movingBodyIds` and `stationaryBodyIds` must exactly equal the IDs passed to
  the publisher. Describe rotation, translation and screw travel as ordered
  `motions`; the Worker applies them to the exported STEP bodies and samples
  exact intersection volume along the complete path.
- Add `clearanceChecks` for body pairs whose running gap is measurable without
  including an intentional seating or hard-stop contact. Use `poseScope` of
  `intermediate` when the closed and open endpoints intentionally touch. A
  nominally collision-free path without the required FDM running gap is not a
  passing printed mechanism.
- Every rigid mechanism needs at least one clearance check. A revolute joint
  additionally needs `maximumMm` on an axis-adjacent body pair; this upper bound
  proves the moving body remains anchored at the joint instead of rotating
  collision-free around an invented remote axis.
- Expose every fit-driving clearance as a parameter. Starting clearances below
  are ordinary FDM assumptions and require process-specific fit coupons when the
  fit matters.

## Closure selection

Choose the closure from the required access frequency, load, available assembly
space, print material and whether the lid must remain attached while open. State
the selected pattern in the design brief.

When the user asks for a generic hinged enclosure without choosing a mechanism,
default to a separately printed removable-pin hinge. It has a rigid motion that
can be checked deterministically, uses ordinary FDM clearances and can be
assembled without relying on material flex. Prefer a slide-on lid when the lid
may detach and product height is constrained, a bayonet for a round removable
lid, and a magnetic lid only for low structural load. Do not default to a snap or
living hinge when material, layer direction and allowable deflection are unknown.

| Pattern                        | Best use                                            | Separately printed or purchased pieces            | Main verification                                                   |
| ------------------------------ | --------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------- |
| Removable pin hinge            | Repeated opening with an attached lid               | Base, lid and removable pin                       | Coaxial bores, radial clearance, axial gaps and pin insertion path  |
| Screw or metal-rod hinge       | Higher cycle count or load                          | Base, lid and screw/rod with retainer             | Tool access, hardware clearance, retention and rotation envelope    |
| Slide-on lid                   | Low-profile enclosure; lid may detach               | Base and lid                                      | Open rail entry, sliding clearance, end stop and removal access     |
| Cantilever snap lid            | Tool-free occasional access                         | Base and lid, optionally replaceable latch        | Root strain, release access, engagement and layer orientation       |
| Bayonet lid                    | Fast removable circular or compact lid              | Base and lid                                      | Lug/track clearance, insertion stroke, twist envelope and hard stop |
| Threaded lid                   | Sealed or adjustable round closure                  | Base and lid                                      | Thread clearance, lead-in, runout relief and handedness             |
| Magnetic lid                   | Clean removable cover with low structural load      | Base, lid and post-installed magnets              | Pocket retention, polarity, locating lip and pry access             |
| Living hinge or flexible strap | Lightweight high-cycle closure in suitable material | One flexible body, or two rigid bodies plus strap | Material, bend radius, layer direction and fatigue allowance        |

## Design from the mechanism outward

The frozen mechanism is a construction recipe, not a test invented after the
model exists. Establish the functional datums and fit equations first, generate
the mating geometry from them, and use the same evaluated datums in
`mechanisms`. Do not build a visually plausible lid and then guess an axis or
motion that might let it pass QA.

Before calling `saveDesignBrief`, use `features` and `derivationNotes` to record
this construction worksheet:

1. Selected closure pattern and the reason it matches the required access,
   load, material and available envelope.
2. Exact published body IDs and feature ownership, for example `base` owns the
   two outer knuckles, `lid` owns the centre knuckle and `hinge-pin` owns the
   shaft and head.
3. Functional datum equations. For a hinge, derive the axis from shell datums;
   for a slide, derive the rail centre planes and travel direction; for a
   bayonet or thread, derive the common axis. Record both the equation and its
   evaluated millimetre value.
4. Fit budget equations. Separate nominal mating size, per-side FDM clearance,
   axial clearance, elephant-foot relief and any intentional endpoint contact.
   Never encode all of them as one unexplained gap.
5. Print pose for every body, support-removal access and the post-print assembly
   sequence. The design is incomplete if the pin, rail entry, magnet or
   retaining feature cannot be reached in that order.
6. Canonical assembled pose, full ordered opening path and the moving envelope
   that must remain free of shell walls, lips, stops and nearby product features.

Every interface-driving value named in that worksheet must be represented by a
top-level annotated source parameter or by an explicit equation using those
parameters. Use one source of truth: the bore is derived from pin diameter plus
radial clearance; a groove is derived from rail size plus sliding clearance; a
female thread is derived from the male profile plus flank/radial clearance.
Independent magic numbers for mating geometry are prohibited.

Generate a closure in these source checkpoints. Complete checkpoints 1–3 in the
first source revision, then call `buildAndCheck` on that complete minimum
mechanism. Do not intentionally publish an incomplete body set to learn from a
predictable failure. Advance to checkpoints 4–5 only after the minimum mechanism
passes, rebuilding after each later feature family:

1. Primary base/lid solids in the canonical assembled pose.
2. Minimum closure attachments fused to their owners, with all mating voids cut
   last from the post-union bodies.
3. Separately printable pin, retainer or other required printed hardware.
4. Only after connectivity, assembly overlap and motion/clearance pass, add the
   requested internal fit features.
5. Add cosmetic rounds, bevels and seams last without changing a proven closure
   datum or fit parameter.

This sequence is deliberately constructive. Do not submit an intentionally
under-specified model merely to discover every requirement from QA.

## Success-first closure workflow

Choose one closure pattern and implement its minimum viable topology before
adding secondary product details. Do not combine a hinge, slide, snap, bayonet,
thread and magnets in the first revision unless the user explicitly requires a
hybrid closure.

1. In the design brief, list every published printed body, every purchased item
   and the owner of each interface feature. Purchased magnets, screws and rods
   do not count toward `qaTargets.componentCount` unless their geometry is
   intentionally published as a printable artifact.
2. Build the primary bodies and closure attachments with positive-volume unions.
   Cut only voids that intersect material in the current post-union body.
3. Publish the canonical assembled pose with the intended printed-body count.
   Omit shell-only dimensions from assembly targets.
4. Freeze a mechanism body partition and the complete ordered motion path in the
   design brief. The host derives the closure's component-count target from that
   partition; never weaken it in `buildAndCheck` to match faulty geometry.
5. Pass body connectivity, pairwise overlap, sampled motion collision and
   required running-clearance checks before adding internal pockets, cosmetic
   seams, rounds or bevels.
6. Add one optional feature family per later revision and keep the proven closure
   coordinates unchanged.

For a simple removable-pin hinge, freeze two mechanism definitions. The first
moves `lid` by the required opening angle while `base` and `hinge-pin` remain
stationary. The second translates `hinge-pin` fully out of the assembled
knuckle row while `base` and `lid` remain stationary. Both definitions must
partition the same published body IDs. This makes rotation and post-print pin
assembly independently mandatory rather than relying on one closed-pose check.

Do not claim deterministic validation for deformation-dependent snaps or living
hinges. If elastic strain, material fatigue or layer adhesion is essential,
select a rigid post-print mechanism or state that a physical coupon remains a
blocking requirement; a rigid-body motion sweep cannot certify flexible motion.

Use the following minimum topology rather than improvising extra parts:

| Pattern                | Published printed bodies    | Minimum viable topology                                                                   |
| ---------------------- | --------------------------- | ----------------------------------------------------------------------------------------- |
| Removable printed pin  | `base`, `lid`, `hinge-pin`  | Tabs and hollow knuckles fuse to their owner; one final through-bore; pin is one solid    |
| Purchased screw or rod | `base`, `lid`               | Same hollow knuckles; keep hardware and tool path clear; do not publish purchased metal   |
| Slide-on lid           | `base`, `lid`               | Rails fuse to one body; matching grooves are open-ended cuts in the other                 |
| Cantilever snap lid    | `base`, `lid`               | Arm is fused only at its root; catch fuses to the other body; release path stays open     |
| Bayonet lid            | `base`, `lid`               | Lugs fuse to one body; axial entries and twist tracks are cuts in the other               |
| Threaded lid           | `base`, `lid`               | Male thread belongs to one body; female thread is a cleared cut with lead-in and runout   |
| Magnetic lid           | `base`, `lid`               | Accessible magnet pockets plus a cleared locating lip; magnets are purchased items        |
| One-piece living hinge | one connected flexible body | Continuous thin web joins both rigid regions; use only with suitable material/orientation |
| Replaceable flex strap | `base`, `lid`, `flex-strap` | Strap is one body and its mounts fuse into the two rigid owners                           |

For any pattern, a component-count failure means an attachment failed to fuse
to its owner or an intentionally separate piece was hidden under the wrong body
ID. An overlap failure means material from the named pair occupies the same
space. Repair only that interface; do not redesign a different mechanism.

## Removable pin hinge

- Use three separately printable bodies: two mating parts with alternating
  coaxial knuckles, plus one removable cylindrical pin inserted after printing.
  Every knuckle must be an annular sleeve with a real through-bore, not a solid
  cylinder that merely shares an axis with the pin. Keep the bore continuous and
  accessible from an end.
- The normal publication contains three named printable bodies: `base`, `lid`
  and `hinge-pin`, with exactly one connected solid in each value. All base
  knuckles and their tabs must fuse into `base`; all lid knuckles and their tabs
  must fuse into `lid`; the pin shaft, head and printable retainer must fuse into
  one `hinge-pin`. Never hide disconnected knuckles, caps or inserts inside one
  of these dictionary values.
- Start with 0.3 to 0.5 mm radial clearance per side between pin and bore and
  0.3 to 0.5 mm axial gaps between neighboring knuckles. The bore diameter must
  equal `PIN_DIAMETER + 2 * HINGE_RADIAL_CLEARANCE`; expose the pin diameter and
  radial clearance as separate parameters.
- Retain the pin with a printable head, end cap, clip or intentional friction fit
  that remains assemblable. Do not make the pin captive during printing unless
  the user explicitly requests a print-in-place mechanism and its orientation,
  support strategy, clearance and free motion are verifiable.

### Default parameter recipe

For an ordinary enclosure, start with these independent parameters and derive
the mating geometry from them. Adjust the ranges only when the user, material or
printer profile requires it.

| Source parameter           | Ordinary starting value | Drives                                                       |
| -------------------------- | ----------------------- | ------------------------------------------------------------ |
| `PIN_DIAMETER`             | 3.0–5.0 mm              | Printed pin shaft                                            |
| `HINGE_RADIAL_CLEARANCE`   | 0.3–0.5 mm per side     | Bore radius = pin radius + radial clearance                  |
| `HINGE_AXIAL_CLEARANCE`    | 0.3–0.5 mm              | Gap between neighbouring knuckle end faces                   |
| `KNUCKLE_WALL`             | at least 1.6 mm         | Outer radius = bore radius + knuckle wall                    |
| `HINGE_ATTACHMENT_OVERLAP` | at least 0.6 mm         | Positive overlap of each tab with its owner and knuckle wall |
| `PIN_END_ALLOWANCE`        | 1.0–2.0 mm              | Shaft extension beyond the knuckle row                       |
| `PIN_HEAD_THICKNESS`       | at least 1.2 mm         | Accessible retention head, fused to the shaft                |
| `HINGE_OPEN_ANGLE`         | 95–110 degrees          | Required lid rotation unless the request specifies otherwise |

Use `BORE_RADIUS = PIN_DIAMETER / 2 + HINGE_RADIAL_CLEARANCE` and
`KNUCKLE_OUTER_RADIUS = BORE_RADIUS + KNUCKLE_WALL` wherever those dimensions
are consumed. The arithmetic belongs in `derivationNotes`; source may compute it
directly from the annotated literals. Do not expose a second independent bore
diameter that can drift away from the pin and clearance.

Place the common hinge axis from the base/lid mating datums before creating any
knuckle. For a conventional rear hinge whose axis is parallel to X, every outer
knuckle, bore cutter and pin must be created from the same `(AXIS_Y, AXIS_Z)`
expression. Use a helper with this construction shape rather than repeating
transforms with unrelated coordinates:

```python
def cylinder_on_hinge_axis(x_center, length, radius):
    return (
        Pos(x_center, AXIS_Y, AXIS_Z)
        * Rot(0, 90, 0)
        * Cylinder(radius, length)
    )
```

Split the available knuckle-row length into two base-owned outer spans and one
lid-owned centre span, subtracting two explicit axial gaps. Build attachment
tabs so they overlap both their shell and the annular sleeve wall but do not
cross the future bore. Fuse shells, tabs, sleeves and structural ribs first;
then subtract one shared oversized bore cutter from both owners. This produces
three connected prints by construction instead of repairing disconnected
knuckles after the fact.

Reserve the lid's swept rear-edge envelope before adding rear walls or lips. If
the first motion sweep collides, preserve the declared axis and opening angle;
increase the outboard axis offset or trim only the colliding rear-edge/lip
material. Recalculate the knuckle tabs from the moved axis so they still retain
positive overlap with their owners.

### Required hollow-knuckle construction order

1. Create the outer knuckle cylinders and overlap them volumetrically with their
   lid/base attachment tabs.
2. Complete every additive union for the knuckles, tabs, ribs and parent body.
3. Create one coaxial bore cutter sized from the pin and radial clearance. Extend
   it at least 1 to 2 mm beyond both ends of the complete knuckle row.
4. Use `subtract_checked` to cut that bore from both mating bodies after the
   additive unions:

   ```python
   base = subtract_checked(base, bore_cutter, label="base-hinge-bore")
   lid = subtract_checked(lid, bore_cutter, label="lid-hinge-bore")
   ```

5. Do not add material across the hinge axis after the bore cut. If a later
   feature must cross that region, repeat the final bore subtraction.
6. Publish the pin as a separate body positioned inside the finished bore for
   assembled-pose QA. A through-hole remains a closed, watertight boundary; do
   not reject it merely because the body is hollow around the hinge axis.

The knuckle wall thickness is half the difference between outer-knuckle and bore
diameters. Verify it against the structural wall requirement. In the assembled
pose, `base & pin`, `lid & pin` and `base & lid` must each have intersection
volume at or below 0.01 mm³. The bore must also remain unobstructed along the
entire pin insertion path.

The lid-rotation mechanism should normally declare an intermediate `base`/`lid`
or `hinge-pin`/`lid` running-clearance check with both `minimumMm` and
`maximumMm`. The pin-insertion mechanism should declare applicable
`hinge-pin`/`base` and `hinge-pin`/`lid` minimum clearances when the pin head or
retainer does not create an intentional contact on that body pair.

If `buildAndCheck` reports `BODY_OVERLAP` for a pair containing the pin, first
check for a missing bore, a bore refilled by a later union, incorrect bore
diameter, mismatched axes or a cutter that did not pass through every knuckle.
Do not resolve the error by moving the pin out of the assembled position.
After all three hinge-body overlap checks and the insertion path pass, freeze
the hinge axis, knuckle spans, bore, pin and attachment tabs. A later repair to
an earbud well, battery cavity, shell dimension or edge finish must not rebuild
or reposition that proven hinge.

If the three-part hinge reports both an assembly component-count failure and a
per-body component-count failure, keep the intended assembly count at three.
Repair the named body that split by increasing only the positive-volume overlap
between its attachment tab and parent shell. Do not bridge across the hinge bore
or join base to lid. If only `BODY_OVERLAP [base/lid]` fails, remove material
from the colliding interface or restore the designed clearance; do not separate
knuckles from their owning body to make the overlap disappear.

## Screw or metal-rod hinge

- Reuse the alternating-knuckle layout of the removable pin hinge, but size the
  real hollow bore for measured hardware rather than assuming a nominal screw
  diameter. Follow the same post-union through-bore construction order.
- Keep the screw head, nut, clip or bent-wire retainer accessible after assembly.
  Put axial clamping load on shoulders or washers, not directly on rotating
  printed knuckles.
- Prefer metal hardware when the hinge is load-bearing, frequently cycled, warm
  in service or too small for a durable printed pin. Do not model fine printed
  threads as a substitute for ordinary small hardware.

## Slide-on lid

- Use two parallel rails with an opening at one end. Add a lead-in chamfer, a
  positive end stop and either a reachable detent or a separate retaining screw.
- Start with 0.25 to 0.4 mm clearance per mating side for ordinary FDM, then tune
  from a fit coupon. Add 0.3 to 0.5 mm bottom-edge relief where elephant-foot
  expansion could jam a rail.
- Do not use a fully enclosed blind channel that cannot be cleaned or assembled.
  Avoid redundant locating faces that make a long slide bind when slightly
  warped.
- Create the two rails from one shared rail section and mirror them. Derive each
  groove width and depth from the rail dimensions plus per-side clearance and
  bottom relief; do not size the two grooves independently. Cut both grooves
  from the final post-union lid or base body and keep the entry end open.
- Set the frozen translation distance to at least the engaged rail length plus
  the length needed for the retaining lip to clear the end stop. Build the end
  stop only after that removal path is proven.

## Cantilever snap lid

- Put a generous fillet at the cantilever root, limit travel with a hard stop and
  provide a tool or finger opening for deliberate release. A hidden hook with no
  release path is not a reusable closure.
- Parameterize arm length, arm thickness, root radius, hook undercut and assembly
  clearance. Treat allowable deflection as material- and layer-dependent; do not
  claim a universal snap geometry.
- Prefer a separately replaceable latch when breakage would discard the entire
  enclosure. Avoid a repeatedly flexed thin PLA snap unless the user accepts the
  limited fatigue life and print orientation is verified.

## Bayonet lid

- Use two or more lugs entering open axial slots and rotating into
  circumferential tracks. Add lead-ins, a defined rotation stop and a finger grip
  that does not collide with the enclosure through the full twist.
- Start with 0.3 to 0.5 mm clearance on each printed mating side. Keep the slot
  roof printable in the selected orientation, or split the track into an
  assemblable insert instead of trapping support material.
- Check both the insertion path and the swept rotation volume. A collision-free
  final pose alone does not prove that the lid can reach it.
- Generate one lug and one cleared L-shaped track from the same lug width,
  thickness, axial clearance and flank clearance, then pattern them around the
  common axis. The frozen mechanism must contain axial insertion/removal and
  rotation as two ordered motions derived from the track stroke and angle.

## Threaded lid

- Reserve printed threads for sufficiently large closures. Use a coarse profile,
  rounded or truncated crests, entry chamfers and a runout relief rather than a
  sharp fine machine thread.
- Parameterize pitch, engagement length and flank/radial clearance. Start around
  0.3 to 0.5 mm clearance per printed mating side and validate with a short thread
  coupon before committing to a tall enclosure.
- Verify handedness, start position and that the lid cannot bottom out before its
  intended sealing or locating face engages.
- Generate the male and female profiles from one pitch, flank angle and crest
  truncation. Offset the female profile by the declared printed clearance rather
  than authoring a visually similar second thread. The frozen screw travel uses
  the same handedness, turns and lead as the modeled helix.

## Magnetic lid

- Use a printed locating lip, pins or shallow tongue-and-groove to carry lateral
  shear; magnets should provide closing force rather than be the only alignment
  feature.
- Install magnets after printing. Keep every pocket accessible, parameterize
  diameter/depth and adhesive allowance, record polarity, and add a pry notch or
  finger recess so the lid can be opened intentionally.
- Add a printable retaining cap or controlled press fit only when assembly order
  remains possible. Never seal a magnet into an unreachable cavity during an
  ordinary FDM print.
- Generate all magnet pockets from one measured magnet diameter/thickness plus
  separate adhesive or press-fit allowance. Pattern pocket centres from shared
  X/Y offsets, mirror them between base and lid, and keep a polarity/indexing
  note in the assembly sequence. Build and verify the locating lip before adding
  pockets so magnets do not compensate for poor lateral alignment.

## Living hinge or flexible strap

- Use a one-piece living hinge only with a material and process appropriate for
  repeated flexing, an explicit print orientation and a tested bend radius.
  Treat it as a material-specific exception, not the default enclosure hinge.
- When the rigid shell material is unsuitable, design a replaceable TPU strap or
  fabric-like flexible insert fastened to two rigid printed bodies. Keep its
  fasteners and replacement path accessible.
- Require a test coupon before promising fatigue life. If material, layer
  direction or cycle requirement is unknown, select a removable pin, metal-rod
  hinge or detachable lid instead.

## Use QA to converge the generated design

QA is a repair signal after a credible construction, not the design method. On
failure, keep the selected closure pattern and change the smallest responsible
fit or datum parameter:

| Failure                              | Constructive repair                                                                                                              |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Body disconnected                    | Increase only the owner-tab/rib volumetric overlap; keep movable bodies separate                                                 |
| Boolean no effect / obstructed bore  | Rebuild the cutter from the shared datum, overshoot both ends and apply it after all unions                                      |
| Motion collision                     | Use the reported body pair and pose; reserve that swept envelope by moving the functional axis or trimming the local obstruction |
| Clearance below minimum              | Increase the relevant bore/groove/track/profile allowance, normally in 0.1 mm steps                                              |
| Clearance above revolute maximum     | Restore coaxial/anchored geometry from the shared axis; do not relax the maximum to bless a remote pivot                         |
| Insertion path blocked               | Open the entry, extend the lead-in or postpone the retainer/stop until after the insertion path is clear                         |
| Overall dimension changed by closure | Recompute the full assembly envelope from the proven closure protrusions; do not shrink functional hardware                      |

After one repair, rebuild immediately and preserve every check that was already
passing. Change closure family only when the requested envelope or manufacturing
constraints make the selected family infeasible, and record that decision in a
new design brief rather than silently redesigning during QA.
