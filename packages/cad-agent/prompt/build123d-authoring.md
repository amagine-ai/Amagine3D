# Build123d authoring rules for Amagine3D

Use build123d's algebra API and millimetre units. Generated programs run
headlessly in a restricted browser Python environment.

## Stable construction order

1. Define top-level literal parameters.
2. Create primary additive solids.
3. Unite ribs, bosses and mounting features with real volumetric overlap.
4. Cut cavities, ports, holes and vents with tools that extend beyond both
   target faces.
5. Apply edge rounds and bevels after booleans.
6. Observe important feature solids before they are merged or removed.
7. Publish all bodies once.

## Geometry guidance

- `Box`, `Cylinder`, `Cone`, `Sphere`, `Rectangle`, `Circle`, `Polygon`,
  `Ellipse`, `extrude`, `revolve`, `loft`, `sweep` and `scale` are preferred
  building blocks.
- Use the exact build123d constructor names and keywords. In particular,
  `Cylinder(radius, height, ...)` and `Cone(bottom_radius, top_radius, height,
...)` use `height`; `depth` is not a valid `Cylinder` keyword. `Box` uses
  `length`, `width` and `height`.
- Use `Pos`, `Rot`, `Plane`, `Align` and `Location` for placement.
- build123d 0.11.1 has no `Scale` class or transform. Use
  `scale(shape, by=(sx, sy, sz))` for non-uniform scaling. Prefer constructing
  the intended primitive directly; for example, make an elliptical prism with
  `extrude(Ellipse(x_radius, y_radius), amount=height)` instead of scaling a
  cylinder.
- Use `mirror` or a loop over explicit positions for repeated symmetric
  features. Avoid manually duplicated sign arithmetic when symmetry is exact.
- Boolean tools should overshoot the target by at least 2 mm in the cutting
  direction.
- A feature that only touches a face can produce a non-manifold or disconnected
  result. Extend it into the parent solid before union.
- A published dictionary value represents one physical print and must satisfy
  `len(body.solids()) == 1` before finishing. Do not use a compound of floating
  features as one body. Every additive feature belonging to that print must
  overlap and fuse with the parent; every intentionally separate solid needs its
  own publisher key.
- `RectangleRounded(width, height, radius)` requires both width and height to be
  greater than twice the radius.
- Use `SlotCenterToCenter(center_separation, height)` for a slot defined by its
  straight centre-to-centre length and total height.
- Select finishing edges by geometry and orientation after booleans. A callable
  selector can be passed to the checked finishing functions when edge identity
  may change.

## Pinned build123d 0.11.1 API quick reference

The runtime is pinned to build123d 0.11.1. Use the argument names and ordering
below; do not copy APIs from a newer development release or another CAD library.
Optional trailing arguments are omitted unless they commonly affect generated
models.

Common 3D primitives:

```python
Box(length, width, height, rotation=(0, 0, 0), align=(...))
Cylinder(radius, height, arc_size=360, rotation=(0, 0, 0), align=(...))
Cone(bottom_radius, top_radius, height, arc_size=360, align=(...))
Sphere(radius, arc_size1=-90, arc_size2=90, arc_size3=360, align=(...))
Torus(major_radius, minor_radius, minor_start_angle=0, minor_end_angle=360)
```

Common closed 2D profiles:

```python
Rectangle(width, height, rotation=0, align=(...))
RectangleRounded(width, height, radius, rotation=0, align=(...))
Circle(radius, arc_size=360, align=(...))
Ellipse(x_radius, y_radius, rotation=0, align=(...))
RegularPolygon(radius, side_count, major_radius=True, rotation=0, align=(...))
Polygon(*points, rotation=0, align=(...))
SlotOverall(width, height, rotation=0, align=(...))
SlotCenterToCenter(center_separation, height, rotation=0)
```

`SlotOverall.width` is the end-to-end slot length. By contrast,
`SlotCenterToCenter.center_separation` is only the distance between the two end
arc centers. A rounded rectangle requires `width > 2 * radius` and
`height > 2 * radius`.

Common 1D paths for `make_face` and `sweep`:

```python
Line(start_point, end_point)
Polyline(*points, close=False)
CenterArc(center, radius, start_angle, arc_size)
RadiusArc(start_point, end_point, radius, short_sagitta=True)
ThreePointArc(start_point, point_on_arc, end_point)
Spline(*points, tangents=None, periodic=False)
Helix(pitch, height, radius, center=(0, 0, 0), direction=(0, 0, 1), lefthand=False)
```

`make_face` requires perimeter edges that form one closed wire. A sweep path
must be a connected edge, wire or `Curve`; a disconnected list of edges is not
a valid path.

For a perimeter that mixes lines and arcs, `BuildLine` is the only permitted
builder context. Extract its completed wire before returning to algebra mode:

```python
with BuildLine(Plane.XZ) as wire_builder:
    Polyline(POINT_A, POINT_B, POINT_C, close=True)
profile = make_face(wire_builder.wire())
rib = extrude(profile, amount=RIB_THICKNESS)
```

Common algebra operations:

```python
extrude(profile, amount=distance, dir=None, both=False, taper=0)
revolve(profile, axis=Axis.Z, revolution_arc=360)
loft([profile_a, profile_b], ruled=False)
sweep(section, path=path, multisection=False, is_frenet=False)
make_face(closed_edges)
thicken(face, amount=thickness, both=False)
mirror(shape, about=Plane.YZ)
scale(shape, by=factor_or_xyz_tuple, about=None)
offset(shape, amount=distance, openings=None)
split(shape, bisect_by=plane, keep=Keep.TOP)
section(shape, section_by=plane, height=0)
```

For `extrude`, the sign of `amount` controls direction. Use either `amount` or
`dir` as intended; do not supply contradictory direction arguments. For a
solid `offset`, positive values expand and negative values inset; `openings`
may contain faces to remove when producing a shell. Prefer subtracting a
deliberately oversized inner solid for ordinary enclosure cavities because its
wall and floor dimensions are easier to verify.

The exact keyword is `dir`, not `direction`. The exact revolve keyword is
`revolution_arc`, not `angle`. With `extrude(..., both=True)`, build123d
extrudes by `amount` on each side of the profile, so the total span is twice
the absolute amount.

For `sweep`, place the section plane perpendicular to the path tangent at the
path's starting point. A circle left in `Plane.XY` is appropriate for a path
starting along Z. A path starting approximately in the XY plane normally needs
its section on `Plane.XZ`, `Plane.YZ` or a custom normal plane. A parallel
section can produce a distorted solid even when build123d does not raise an
exception.

## Algebra, alignment and placement

- Algebra mode uses `a + b` for union, `a - b` for cut and `a & b` for
  intersection. In generated programs use `+` for additive unions,
  `subtract_checked` for every material-removing cut, and `&` only when an
  intersection is intentional.
- `Mode.ADD`, `Mode.SUBTRACT` and the automatic behavior of `Hole`,
  `CounterBoreHole` and `CounterSinkHole` belong to builder contexts. Do not
  expect `mode=Mode.SUBTRACT` to modify an existing algebra object. In this
  workflow, build explicit `Cylinder` and `Cone` cutter solids with enough
  overshoot and pass them to `subtract_checked`.
- `Box`, `Cylinder`, `Cone`, `Sphere` and `Torus` default to
  `(Align.CENTER, Align.CENTER, Align.CENTER)`. To put a Z-oriented primitive's
  printable bottom at Z=0, normally use
  `align=(Align.CENTER, Align.CENTER, Align.MIN)`.
- Use `Pos(x, y, z)` for translation and `Rot(x_deg, y_deg, z_deg)` for
  rotation. Apply each location to a shape with `*`. The expression below
  rotates the shape first and then translates it. Angles are degrees.

  ```python
  placed = Pos(X, Y, Z) * Rot(X_ANGLE, Y_ANGLE, Z_ANGLE) * shape
  ```

- Use `Plane.XY`, `Plane.XZ` and `Plane.YZ` for standard workplanes. Use
  `Plane.XY.offset(z)` for a parallel plane. Use
  `Plane(origin=ORIGIN, x_dir=X_DIRECTION, z_dir=Z_DIRECTION)` only when a
  custom plane is required. `plane * sketch` places the sketch on that plane.
- Operations such as `extrude`, `loft`, `sweep`, `mirror` and `scale` take
  explicit object arguments in algebra mode. They are not placed by an active
  `Locations` context; transform their input or returned object explicitly.
- Mirror planes map axes as follows: `Plane.XZ` changes Y to -Y, `Plane.YZ`
  changes X to -X, and `Plane.XY` changes Z to -Z. Model one symmetric feature
  and mirror it instead of manually maintaining sign-swapped coordinates.
- A rib sketch plane must be perpendicular to the face it reinforces. For a
  connection face on XZ, sketch the rib on YZ; for a connection face on YZ,
  sketch it on XZ. On XZ and YZ sketches, world Z maps to the sketch's local Y
  coordinate; keeping every local Y value at zero collapses the profile to a
  line.

```python
base = Box(
    BODY_LENGTH,
    BODY_WIDTH,
    FLOOR_THICKNESS,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
side_bore = Pos(BORE_X, BORE_Y, BORE_Z) * Rot(0, 90, 0) * Cylinder(
    BORE_RADIUS,
    CUTTER_LENGTH,
    align=(Align.CENTER, Align.CENTER, Align.CENTER),
)
```

## Repetition and topology selection

- In algebra mode, obtain explicit locations from
  `GridLocations(x_spacing, y_spacing, x_count, y_count).locations`. For a
  circular pattern, use `PolarLocations(...).locations` with explicit `radius`,
  `count`, `start_angle`, `angular_range` and `rotate` arguments. Multiply each
  returned location by a shape.
- For many repeated objects, collect them first and fuse once with
  `Part() + [location * feature for location in locations]`. Repeatedly fusing
  into a growing body inside a loop is substantially slower.
- `shape.vertices()`, `shape.edges()`, `shape.wires()`, `shape.faces()` and
  `shape.solids()` return `ShapeList` values. Their original ordering is not a
  stable geometric identity. Filter or sort before indexing.
- Common refinements are `sort_by(Axis.Z)`, `group_by(Axis.Z)`,
  `filter_by(Axis.Z)`, `filter_by(Plane.XY)`, `filter_by(GeomType.CIRCLE)`,
  `filter_by(lambda edge: ...)`, `filter_by_position(axis, minimum, maximum)`
  and `sort_by_distance(point)`.
- Vector coordinates are uppercase properties such as `edge.center().X`, `.Y`
  and `.Z`; lowercase `.x`, `.y` and `.z` raise `AttributeError`. Use edge
  length and a tolerance-aware position test to exclude tiny boolean remnants.
- `filter_by(Axis.Z)` keeps edges parallel to Z; it does not select edges merely
  located high on Z. Use `sort_by(Axis.Z)[-1]` for the highest face or group,
  and use a tolerance-aware callable when selecting by measured coordinates.
- `Select.LAST` and `Select.NEW` are builder-context selectors and do not work
  on an algebra `Part`. When algebra code needs newly created edges, use
  `new_edges(shape_a, shape_b, combined=result)` or select the result by stable
  geometry.

```python
top_face = body.faces().sort_by(Axis.Z)[-1]
vertical_edges = body.edges().filter_by(Axis.Z)
round_edges = body.edges().filter_by(GeomType.CIRCLE)
grid = GridLocations(X_SPACING, Y_SPACING, X_COUNT, Y_COUNT).locations
cutters = Part() + [location * hole_cutter for location in grid]
body = subtract_checked(body, cutters, label="mounting-hole-grid")
```

## Foreign-API and context traps

- Do not use CadQuery syntax such as `Workplane`, `.box()`, `.faces(">Z")`,
  `.edges("|Z")` or chained `.fillet()`. Those are not build123d algebra APIs.
- Do not open `BuildPart` or `BuildSketch` contexts inside the generated algebra
  program. `BuildLine` is the sole exception for constructing a connected
  mixed line/arc wire, and its wire must be extracted before algebra operations.
- Do not call build123d `fillet` or `chamfer` directly. Use the checked host
  operations below so partial or failed finishing becomes deterministic QA.
- `Hole`, `CounterBoreHole` and `CounterSinkHole` require an explicit depth
  outside a builder and use builder-oriented direction conventions. Prefer
  explicit, oversized primitive cutters to avoid reversed or half-depth cuts.
- Do not invent convenience parameters. The common corrections are:

  | Invalid or foreign spelling           | build123d 0.11.1 spelling                       |
  | ------------------------------------- | ----------------------------------------------- |
  | `Cylinder(..., h=...)` or `depth=...` | `Cylinder(..., height=...)`                     |
  | `extrude(..., direction=...)`         | `extrude(..., dir=...)`                         |
  | `revolve(..., angle=...)`             | `revolve(..., revolution_arc=...)`              |
  | `RegularPolygon(..., sides=...)`      | `RegularPolygon(..., side_count=...)`           |
  | `Ellipse(width=..., height=...)`      | `Ellipse(x_radius=..., y_radius=...)`           |
  | `SlotCenterLine(...)`                 | `SlotCenterToCenter(center_separation, height)` |

  `SlotCenterToCenter.height` is the full slot width and end-arc diameter, not
  the radius.

## Exact host-operation signatures

Do not infer or abbreviate the host helper arguments. Use these exact signatures:

```python
subtract_checked(body, cutter, label="cut")
round_edges_checked(shape, edges, radius, label="round")
bevel_edges_checked(shape, edges, length, label="bevel")
observe_feature(shape, feature_id, feature_type="feature")
```

Use `feature_type="keep-out"` only for a reserved empty construction volume,
never for a physical part. A keep-out is checked against every final published
body or color region. When `saveDesignBrief.featureChecks` names a feature ID,
observe that exact shape before it is fused or subtracted so the Worker can
verify its bounds, center or volume against the frozen value and tolerance.

`radius` is required by `round_edges_checked`; `length` is required by
`bevel_edges_checked`. Always pass finishing sizes by keyword so their meaning
is unambiguous. Finishing is optional unless the user requires it. Omit it from
the first structural build and add it only after booleans, connectivity and
assembly checks pass.

On a shelled or boolean-rich enclosure, never select every edge parallel to an
axis or every edge in a plane. That usually includes cavity, pocket and hinge
edges with incompatible local sizes. Use a callable selector with position,
length and orientation constraints so it returns only the intended cosmetic
edges:

```python
body = round_edges_checked(
    body,
    lambda current: current.edges().filter_by(Axis.Z).filter_by(
        lambda edge: (
            edge.length > BODY_HEIGHT / 2
            and abs(edge.center().X) > BODY_LENGTH / 2 - WALL * 2
            and abs(edge.center().Y) > BODY_WIDTH / 2 - WALL * 2
        )
    ),
    radius=EDGE_RADIUS,
    label="outer-vertical-edges",
)
```

If the intended subset cannot be expressed confidently, omit that cosmetic
finish instead of applying a broad selector.

Import every name used by the program from build123d. Prefer explicit imports.
A star import does not make nonexistent names such as `Scale` valid.

## Parameter annotations

Every dimension that drives geometry is a separate uppercase literal.

```python
# @param label="Wall thickness" group=Shell unit=mm min=1.2 max=5 step=0.1 description="Printed enclosure wall"
WALL = 2.4
```

Do not place driving values inside functions, classes or dictionaries. Do not
derive one editable parameter by assigning an expression to another editable
parameter. Derived coordinates may be computed where they are used from the
top-level literals.

## Deterministic repair discipline

Treat every accepted `model.py` as a revision, not permission to redesign the
model after a QA failure. A repair must start from the latest indicated repair
baseline and reproduce it unchanged except for the smallest source section
responsible for the selected failure.

- Preserve every body, feature, parameter, coordinate frame and check that did
  not fail. Once a mechanism passes coaxiality, clearance, overlap and insertion
  checks, freeze that mechanism during unrelated shell, cavity or finishing
  repairs.
- Repair one failure class per revision unless several failures demonstrably
  share one root cause. Use this order: invalid/disconnected solids and failed
  booleans; assembly overlap, fit and insertion; target dimensions; edge
  finishing. Do not spend a revision on rounds or bevels while a structural or
  assembly failure remains.
- Compare `repairContext.newlyFailedCheckIds` and `resolvedCheckIds` after every
  build. A newly failed check is a regression even when another check was
  resolved. Restore unaffected geometry from `baselineSourceHash` and make a
  narrower change; do not keep trading one failure for another.
- `BOOLEAN_NO_EFFECT` means the cutter removed no material. Check the cutter
  against the current post-union body and its coordinate frame. If the intended
  void was already created by a parent cavity, remove the redundant checked cut
  instead of moving the cutter merely to force nonzero removal. Use a unique,
  feature-specific label for every checked cut.
- If `cad-<body>-component-count` or `mesh-<body>-component-count` fails, do
  not change `qaTargets.componentCount` or accept the measured count. Locate the
  floating additive feature in that named body, extend its attachment into the
  parent and re-union it. If it is meant to remain separate, publish it under a
  new body ID and update the design brief's physical-piece count deliberately.
- Apply finishing only after the exact structural revision passes. If a broad
  selector reports a partial round or bevel, narrow the callable selector to
  edges whose geometry supports the requested size; never move or resize a
  hinge, shell or functional cavity to make finishing pass.
- `writeCadSource` still returns a complete program, but the semantic change in
  a repair revision must remain minimal and localized.

## Runtime restrictions

Only `build123d`, `amagine_cad` and `math` imports are allowed. Do not perform
file access, network access, dynamic imports, dynamic evaluation, viewer calls
or direct CAD export. The host publisher owns every output path and artifact.
