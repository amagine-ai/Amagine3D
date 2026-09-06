# Evidence contract

The intent contract is the independent target used to judge the model. Write
it before geometry and validate it with `intent_contract.py`. Never rewrite
targets merely to match a generated artifact.

## Required structure

```json
{
  "schema": "evidence-cad-intent/v5",
  "part": "part-name",
  "task_mode": "reference-reproduction",
  "representation": "full-3d",
  "reference_files": [
    {"path": "/absolute/reference.png", "sha256": "...", "role": "front appearance"}
  ],
  "coordinate_system": {
    "x_positive": "right",
    "y_positive": "back",
    "z_positive": "top",
    "front": "y-min",
    "back": "y-max",
    "left": "x-min",
    "right": "x-max",
    "bottom": "z-min",
    "top": "z-max"
  },
  "dimensions_mm": {
    "x": {"value": 120, "source": "user", "confidence": "high"},
    "y": {"value": 55, "source": "inferred", "confidence": "low"},
    "z": {"value": 28, "source": "reference", "confidence": "medium"}
  },
  "features": [
    {
      "id": "screen-recess",
      "kind": "recess",
      "face": "front",
      "direction": "-Y",
      "edge_crossing": "forbidden",
      "evidence": "dark centered rectangle in front reference",
      "acceptance": "centered; width 72 ± 1 mm; depth 1.2 ± 0.2 mm"
    }
  ],
  "manufacturing": {
    "mode": "single-part"
  },
  "printability": {
    "profile": {
      "path": "part-name_printer-profile.json",
      "sha256": "..."
    },
    "build_axis": "+Z",
    "bed_contact": "z-min",
    "support_policy": "support-free",
    "minimum_wall_target_mm": 0.9,
    "critical_features": ["screen-recess"]
  },
  "visual": {
    "required": true,
    "reference_view": "front",
    "landmarks": ["screen centered", "button above screen", "knob on right"]
  },
  "assumptions": ["rear surface inferred flat because no rear view was supplied"]
}
```

Allowed task modes are `specification`, `reference-reproduction`,
`reference-inspired`, `recognizable-form`, and `inspect`. Representations are
`full-3d`, `orthographic-solid`, `relief`, and `surface-led`.

`dimensions_mm` is the X/Y/Z size of the complete physical assembly envelope
in semantic coordinates. It is not a per-part size and not the rotated or
packed plate envelope. Build reports independently measure the final physical
part union as `backendData.semanticAssembly.boundsMm`; they must never copy the
intent target into that evidence. Each `parts[part].semantic.boundsMm` records
only that physical part. Intent-to-semantic envelope comparison uses a
0.5 mm tolerance, while representation readback of an exported STEP/STL uses a
separate 0.05 mm tolerance.

The parameter panel does not amend or regenerate this immutable intent. Direct
parameter rebuilds are valid only while the complete semantic X/Y/Z envelope
continues to satisfy `dimensions_mm`. A requested adjustment that changes that
overall envelope starts a new CAD task with a new intent contract; do not hide
it inside the existing report or consume the tolerance as a resize allowance.

When the user asks to replicate, reproduce, or exactly match a named real,
catalog, branded, or fictional object, preserve that identity as the target.
Use `reference-reproduction` when supplied or discoverable evidence supports it.
If no reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, record inferred landmarks and dimensions, and report that
the result is inspired by the named object rather than an exact replica.

The printability profile must come from this skill's `bambu_profile.py`. Its
hash locks the machine, selected tool, nozzle, standard process, printable
polygon, wall targets, and support threshold used for the run. The minimum
wall target must meet the resolved process wall target. Use `support-free`
only when it does not change the requested geometry; otherwise preserve the
object and set `supports-required` or disclose support warnings. Support
avoidance must not flatten an underside, remove back-side details, or turn a
full-3D object into a relief.

Matched visual views may be `front`, `side`, `top`, `bottom`, or `isometric`;
use `bottom` when the appearance-bearing face is intentionally printed at Z0.

The coordinate system is fixed for generated geometry: `+X` means user right,
`+Y` means object back, and `+Z` means object top. Describe ports, holes,
buttons, seams, and logos by semantic face and insertion direction before
using numeric offsets. For ports, holes, slots, cutouts, windows, cavities, and
recesses, put flat fields directly on the feature: `kind`, `face`,
`direction`, and `edge_crossing`. A bottom opening is allowed, but an opening
that crosses the front/bottom edge must be declared explicitly.

For a functional port or connector opening that serves an internal item, write
its acceptance in terms of the complete passage: name the exterior face and the
target interior cavity or component keepout. The physical cut must cross the
full wall thickness. Describe a deliberately blind cosmetic depression as a
recess instead of declaring it as a functional port.

Use these semantic feature values:

- `kind`: `port`, `hole`, `slot`, `cutout`, `window`, `cavity`, `recess`,
  `button`, `seam`, `logo`, `interface`, `region`, `envelope`, `surface`,
  `detail`, `additive`, `part`, `control`, `fastener`, `mount`, or
  `clearance`.
- `face`: `front`, `back`, `left`, `right`, `top`, `bottom`, `internal`, or
  `multiple`.
- `direction`: `+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`, `through-X`, `through-Y`,
  `through-Z`, `surface-normal`, `none`, or `multiple`.
- `edge_crossing`: `forbidden`, `allowed`, `required`, or `not-applicable`.

For a feature on a single outside face, the direction must follow the semantic
normal or pass through that axis: bottom uses `-Z` or `through-Z`, front uses
`-Y` or `through-Y`, and so on. Set `edge_crossing` to `forbidden` unless a
feature is intentionally on an edge or corner.

## Manufacturing structure

Always declare `manufacturing`. Use `single-part` for one reliable printed
body. A model may have semantic sub-parts without becoming multipart when they
can be fused as one printable body. Do not split only because the default
printer profile is small; if the user did not fix the final size, revise the
intent dimensions and rebuild the source at unit scale before geometry exists.
Use `multipart` only when separate printed parts create a
real manufacturing benefit such as cleaner support strategy, better strength
orientation, post-installed components, functional movement, or separable
covers, inserts, hinged joints, retained closures, or slides inferred from the object.

Multipart contracts must declare every printed part and assembly interface:

```json
"manufacturing": {
  "mode": "multipart",
  "parts": [
    {
      "name": "lower-shell",
      "role": "main protective sleeve",
      "acceptance": "open cavity, bottom port opening, and retention lip"
    },
    {
      "name": "top-lid",
      "role": "separate cap over the original device lid",
      "acceptance": "covers the lid area and preserves 0.3 mm assembly clearance"
    }
  ],
  "interfaces": [
    {
      "id": "lid-tab-slot",
      "between": ["lower-shell", "top-lid"],
      "connection": "tab-slot",
      "assembly_axis": "+Z",
      "clearances_mm": {"width": 0.3},
      "engagement_mm": 2.0,
      "features": ["lid-tab", "lid-slot"],
      "acceptance": "2 mm printable tab enters the lid slot with 0.3 mm clearance"
    }
  ]
}
```

When an interface capability includes a clearance proof, `clearances_mm` is a
non-empty mapping from each derived scene dimension to its exact
female-minus-male size delta. Declare different entries when transverse and
axial fits differ; for example, `{"diameter": 0.4, "length": 0.2}` describes a
0.4 mm diametral delta and a 0.2 mm axial delta. Because radial helper inputs
are per-side gaps, the matching `diameter` delta is twice the radial input. The
scene must derive exactly the same field set with the same values. Capabilities
without a clearance check, such as `glue-face`, omit `clearances_mm` (or use an
empty object) and declare no derived clearance fields. Self-tapping screw
interfaces instead use their explicit `fastening` diameters and depths.

Every `features[]` record in a multipart intent must include a `part` equal to
one `manufacturing.parts[].name`. Interface feature owners must be one of the
two parts named by that interface's `between` field. In a single-part intent,
`features[].part` may be omitted or must equal the top-level `part`.

## Color regions

The same v5 intent owns color and material evidence. Do not create a separate
color-intent document. Every `color_regions[]` record requires `name`, owning
`part`, `hex`, `purpose`, `boundary`, and `evidence`; optional material data may
declare `transmission` and a user-selected `filament`. Also record the
`palette_reduction` decision.

- For `single-part`, two or more regions may share the top-level physical part;
  use `print_package_mode: "co_print_body"`.
- For `multipart`, every region name is globally unique and `part` identifies
  its owning physical part. A mesh-master part may own several volumetric
  regions while another part owns one whole-part region. Use
  `print_package_mode: "separate_parts"` for the physical-part package.

When `color_regions` is present, `print_package_mode` is required explicitly.
Writers, command-line entrypoints, and QA never infer it from manufacturing
mode, archive topology, or a build report.

```json
"color_regions": [
  {
    "name": "body",
    "part": "product",
    "hex": "#E8E4DC",
    "purpose": "continuous structural body",
    "boundary": "parent volume excluding the shallow accent inset",
    "evidence": "the requested body is warm ivory",
    "continuity": "continuous-core",
    "material": {"transmission": "opaque"}
  },
  {
    "name": "accent",
    "part": "product",
    "hex": "#171A1D",
    "purpose": "identity-bearing front accent",
    "boundary": "shallow front inset",
    "evidence": "the requested front accent is dark graphite",
    "continuity": "surface-detail"
  }
],
"palette_reduction": {
  "applied": false,
  "reason": "Two semantic colors map directly to two printable regions."
}
```

Each multipart `parts[].name` becomes an exported STL suffix. By default a part
has `"installation": "interface"` and must appear in at least one declared
interface before geometry is written. Use `"installation": "adhesive"` or
`"installation": "loose"` only when that exception is intentional and stated
in the part acceptance; this makes an omitted button guide, insert pocket, or
lid connector fail at intent time rather than after modeling. Do not convert a
separate requested lid or cover into an open-top single body unless the user
explicitly asks for a one-piece slip-on sleeve. Do not export separate parts
unless their interfaces name modeled connector feature IDs.

For a removable shell, cover, or base that uses direct fastening into printed
plastic, declare `connection: "self-tapping-screw"` and read
`multipart-connections.md`. That conditional reference owns the complete
fastening object, shared-axis geometry, M3 starting dimensions, positive
serviceable-enclosure default, and acceptance evidence. Do not load those rules
for multipart designs that use another connection.

Non-manufactured installed components do not belong in `manufacturing.parts`.
When the request contains an installed LED/LCD or another display component,
read `installed-displays.md` for the physical aperture/keepout/retention and
display-only scene rules.

Before writing the immutable document, enumerate the exact names that will
enter the manufacturing exporters. Keep installed references, transient visual
content, and purchased hardware out of that list unless the user explicitly
requested a printable surrogate. This inventory is part of intent reasoning;
it is not a separate contract or a keyword-based component taxonomy.

The scene is not allowed to broaden the immutable request. Its `intentRef` is
hash checked and the referenced document must pass the complete root v5 intent
validator. Scene `parts[].id` is an exact set match against the single top-level
intent part or multipart `manufacturing.parts[].name` records. Every physical
node `featureId` must exist in `intent.features[]`, and its `partId` must equal
the resolved intent feature owner. A display component's
`physicalFeatureRef` must name an intent-backed physical feature owned by the
same part as the display node. Build input binding repeats these checks and
also requires the exported part set to match both documents exactly.

For a BRep multipart assembly whose colors follow physical part boundaries,
keep color in the same `evidence-cad-intent/v5` document. This exporter is the
deliberately narrower whole-part case: add exactly one `color_regions` record
per `manufacturing.parts[].name`, with both `name` and `part` equal to the
physical part name, plus `hex` and optional `material.filament` /
`material.transmission`. Set `printability.print_package_mode` explicitly to
`separate_parts`, then bind the same values at export:

```python
export_assembly(
    {"lower-shell": lower_shell, "top-lid": top_lid},
    NAME,
    intent_path=INTENT,
    scene_path=SCENE,
    source_path=__file__,
    part_colors={"lower-shell": "#E8E0D4", "top-lid": "#20242A"},
)
```

This emits one STL and one STEP per physical part, the required assembly STEP,
the plate STL, semantic display GLB, colored separate-parts 3MF, material plan,
and one `evidence-a3d-build/v1` manifest. The 3MF is derived from the same
plate-aligned BRep shapes; STEP and GLB stay in semantic coordinates. All
reported transforms are raw rigid 4x4 matrices with `scale: 1.0`. Do not create
a second color-only intent or import color helpers through a second `sys.path`
entry.

The material plan uses `sourceBindings`, with exactly one binding for every
assignment target. Each binding repeats the assignment and resolved material
fields, then identifies its authority with `sourceKind` and `sourceId`:

- `intent-color-region`: `sourceId` is the exact immutable
  `color_regions[].name`; owner, region, color, and declared material fields
  must agree.
- `scene-part-material`: only for a proposed whole-part color without an intent
  region; `sourceId` is the explicit `scene.parts[].materialId` and must resolve
  in `scene.materials[]`.
- `scene-part-appearance`: only when the scene part has no `materialId`;
  `sourceId` is the exact scene part ID and the proposed color is derived from
  its appearance or the deterministic fallback palette.

The hash-bound intent and scene are re-read when validating these sources.
Missing, duplicate, unknown, or semantically mismatched bindings invalidate the
build; a material-plan record cannot make itself authoritative.

## Evidence rules

- User values outrank standards, standards outrank reference measurement, and
  reference measurement outranks inference.
- Replica fidelity outranks print convenience. Bed contact and support
  reduction may choose orientation, but they may not alter the semantic source
  shape.
- Every inferred dimension must be exposed with low or medium confidence.
- A photograph proves visible relationships, not hidden-side dimensions.
- Landmarks describe identity-bearing relationships. “Looks similar” is not
  an acceptance criterion.
- Critical functional features must be backed by named `observe()` or
  checked-operation evidence. Natural-language acceptance alone is not proof.
- For pixel art, use the structured `reference_analyze` tool's hash-bound cells
  and colors directly. Do not invoke its Python backend through a shell or
  redraw coordinates from memory.
- If a required target remains unknowable and changes function or identity,
  ask. Otherwise choose a reversible assumption and record it.

## Printability acceptance

- Empty or zero-volume geometry is a hard failure; dependent checks remain
  `not_evaluated` rather than crashing or passing.
- Overflow of the selected tool's printable polygon or height is a hard
  failure. Bed exclusions and a 90-degree XY placement are considered.
- For multipart assemblies, every `NAME-PART.stl` is audited as an individual
  printable body and `NAME.stl` is audited as the print-bed layout.
- Every `NAME-PART.step` and the required `NAME-assemble.step` are audited with
  OCCT for CAD readability, solid count,
  and dimensions. STEP checks do not replace mesh printability checks.
- `NAME-display.glb` is the user-visible assembly model. Its named physical
  nodes must match manufacturing geometry; explicitly tagged display-only
  installed components may also appear for assembly context. GLB checks can
  prove loadability and appearance, but not B-rep topology or printability.
- A sub-line-width named feature is a warning tied to its feature ID.
- Local wall thickness below the process wall target is a warning with sampled
  risk bounds. It does not prove mechanical strength.
- A downward surface below the Bambu process support threshold is a warning.
  The Z0 bed face is excluded, and bridges are never assumed safe automatically.
- A missing profile, build report, or thickness result is `not_evaluated`, not
  a printability pass.

## Visual decision

The five-view render detects unintended depth, hidden-side placement, bottom
features, and topology; the matched view tests silhouette and landmark
placement. Visual review uses semantic orientation; print orientation evidence
is for manufacturing fit, contact, support burden, and Z0 placement. For
`full-3d`, a plain planar underside is acceptable only when the object itself
has one or the user requested a relief/flat-backed prop.
`compare_silhouette.py` is valid only
for a flat or genuinely corresponding orthographic reference. Its IoU cannot
prove depth, semantic identity, or printability.

After each visual read, list target-specific deltas. Use a compact landmark
rubric proportional to the request, usually 3-7 identity-critical landmarks for
recognizable objects rather than every small decorative detail. A failed
must-have landmark remains failed even if mesh integrity and dimensions pass;
optional micro-detail gaps may be reported as compromises.
