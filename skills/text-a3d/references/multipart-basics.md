# Multipart construction basics

Read this reference only when the immutable intent selects
`manufacturing.mode: "multipart"`.

Give every printed part a real assembly relationship before geometry. Choose
the connection from how the object will be assembled and serviced; do not make
parts separate merely because a visual seam exists. Every interface needs a
declared connection, assembly axis, engagement depth, distinct
male/female feature IDs, and acceptance evidence.

Use the construction that matches the requested assembly behavior:

| Need | Preferred construction |
|---|---|
| locating two removable parts | collar/socket or two spaced pin/socket pairs |
| tool-free opening | snap-fit or dovetail with a deliberate release path |
| fitted printable panel, lens, or bezel | inset pocket |
| permanent cosmetic insert | fitted pocket plus explicitly declared adhesive |
| frequent service or higher clamp load | locator plus machine screw and threaded insert |
| direct fastening into printed plastic | read `multipart-connections.md` |

`adhesive` and `loose` are intentional installation choices, not substitutes
for a missing connector. A snap needs a printable flex arm, lead-in, retention
shoulder, and release path. A dovetail needs a clear insertion direction and end
stop. When the registered capability checks clearance, derive female geometry
from the male geometry plus the immutable `clearances_mm` mapping rather than
entering the two sides independently. Each mapping key names the scene
dimension being derived, and each value is that dimension's full
female-minus-male size delta. A radial recipe input is a per-side gap, so its
diameter delta is twice that value. This permits radial/side and axial fit
dimensions to differ without introducing product-specific rules. A connection
without a clearance proof, such as `glue-face`, omits the mapping and does not
invent a dummy derived dimension.

Every printed part must participate in a declared interface unless its adhesive
or loose installation is explicit. Keep each part as a separate valid body,
preserve its intent feature ownership in observations and operations, and prove
that the assembled relationship matches the semantic scene.

This reference defines static manufactured relationships only. A hinge pin or
slider recipe describes printable mating geometry; it does not introduce a
motion path, controller, kinematic state, or full-travel validation.
