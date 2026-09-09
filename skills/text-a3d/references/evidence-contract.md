# Evidence contract

The intent contract is the independent target used to judge the model. Write
it before geometry and validate it with `a3d intent INTENT.json`. Never rewrite
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
only that physical part. For BRep backends, the hash-bound STEP readback bounds
must satisfy the intent's allowed dimension intervals with a default 0.01 mm
measurement tolerance, using the raw measured value. A dimension item can set
`measurement_precision_mm` from 0.0001 through 0.01 to tighten this tolerance
when explicitly required. This is measurement acceptance, not printer accuracy.
Recorded bounds retain their separate 0.0002 mm rounding allowance; final raw
STEP readback receives only the selected measurement tolerance. The
approximate Hybrid mesh envelope retains its 0.5 mm audit tolerance, and
representation readback of an exported STEP/STL retains a separate 0.05 mm
tolerance. `source: "inferred"` describes confidence; it does
not grant permission to resize. An axis can explicitly declare
`"constraint": {"kind":"range","min_mm":100,"max_mm":125}` beside its `value`,
`source`, and `confidence`. Both the selected value and final measured envelope
must satisfy that declared range with the applicable measurement tolerance.
Without a range, the value remains fixed.

At initial authoring, ordinary exterior dimensions default to nominal ±0.1 mm,
expressed using this existing range field. For example, 54 mm uses `value: 54`
and `constraint: {kind: "range", min_mm: 53.9, max_mm: 54.1}`. Explicit user
tolerances, limits and functional fits take precedence; do not apply this allowance
to minimum walls, floor thickness, clearances or collision checks. Stop calibrating
inside the accepted interval. `a3d measure` saves and returns measured lengths
to two decimals; Python measurement helpers and raw STEP acceptance stay unrounded.
Canonical geometry record precision is unchanged.
Changing an already bound fixed target to a range requires an intent revision.

A feature can optionally declare `section_dimensions` when its requirement is
the outside size at a particular height or other semantic plane:

```json
"section_dimensions": [{
  "plane": {"axis": "z", "coordinate_mm": 95},
  "outer_envelope": {"width_u_mm": {"value": 54}}
}]
```

The nonempty list belongs to the feature's physical part. Each plane uses an
absolute semantic coordinate; its in-plane `u/v` axes are `+Y/+Z` for `x`,
`+X/+Z` for `y`, and `+X/+Y` for `z`. Declare `width_u_mm`, `depth_v_mm`, or
both. Each metric accepts `value`, optional `constraint`, and optional
`measurement_precision_mm` as above. Final compile measures the owner's hash-bound
semantic STEP after all finishing, using the selected precision independently of `--tol`.
An empty section fails. The audit records the plane, actual value, target,
delta and STEP hash; an assembly union or another part cannot substitute for
the owning part. This requires a BRep owner and its semantic STEP artifact.
In multipart BRep exports, `assembly` names the aggregate STEP; a physical owner
with that same name is ambiguous and cannot carry this check. Single-part owners
named `assembly` are supported.

This is the outer envelope of all material islands, including gaps between
them. It does not measure a hole, cavity clearance, passage or wall thickness,
even when attached to a feature with that kind. Use the corresponding geometric
checks for those requirements. Drafts remain previews; whole-envelope errors
can stop compile before its section audit runs. Existing intents without this
field keep their previous behavior: acceptance prose is not parsed into a
section check. Keep their existing dimension assertions unless a legitimate
intent revision supplies the typed requirement; adding one is a target change
under the revision rules below.

The parameter panel does not amend or regenerate this immutable intent. Direct
parameter rebuilds are valid only while the complete semantic X/Y/Z envelope
continues to satisfy `dimensions_mm`. For a user-requested change to the target
envelope or required features, write a revised intent under a new filename in
the same workspace. `write_intent` accepts identical existing content and
requires a new filename for changed content. Include a `revision` with
`parent: {path, sha256}`, `kind`, and a concrete `reason`. Use
`parameter-adjustment` for values within existing allowed ranges;
`target-change` requires evidence `{kind:"user-request", text:"..."}`, while
`evidence-correction` requires `{kind:"external-evidence", text:"..."}` and can
include a reference. Evidence is an author-declared record, not independent
proof of authorization. Never fabricate it to pass a checker. Keep the same model name
and editable build source, pass the revised file through `a3d compile --intent`,
and let `write_scene` refresh its intent binding. This is a requirement revision
of the current model; it does not require a separate conversation or new
manufacturing output set. Within each revision, repair geometry against that
target rather than changing the target to match a failing artifact. The compiler
stores a workspace/model revision lineage and requires each changed intent to
descend from the last verified parent. Changing the intent filename or output
directory does not start a fresh requirement baseline. Removed requirements are
reported as scope changes, not repaired geometry.

When the user asks to replicate, reproduce, or exactly match a named real,
catalog, branded, or fictional object, preserve that identity as the target.
Use `reference-reproduction` when supplied or discoverable evidence supports it.
If no reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, record inferred landmarks and dimensions, and report that
the result is inspired by the named object rather than an exact replica.

Reuse a tool-generated profile that matches the user's process, or resolve one
with `a3d profile`; its hash binds the process assumptions used by the contract.
When the printer is unspecified, record the selected catalog profile as a
reversible process assumption. A profile can be reused across builds with the
same process; generating new geometry does not require regenerating it.
Apply profile-driven design and repair rules from `bambu-printability.md`.

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
feature is intentionally on an edge or corner. `edge_crossing` describes the
boundary between exterior faces, not whether a cutter passes through the skin.
A top cavity contained inside its surrounding rim uses `forbidden`, even though
it is open. A notch that reaches across the top/front edge uses `required`.

For applicable installed-component relationships, a receiving feature can
declare `installation_checks`: a nonempty list of `clearance`, `insertion`,
`support`, `retention`, or `passage`. These targets require corresponding
hash-bound scene evidence and final-part checks; `installation-checks.md`
describes the API and geometric scope. They are independent of preview nodes
and do not prescribe a mounting method.

## Manufacturing structure

Always declare `manufacturing`. `single-part` owns one printed body; `multipart`
must enumerate printed parts and interfaces. Use `a3d guide multipart` for the
design decision and connection semantics.

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

The same v5 intent owns manufactured-color evidence. Do not create a separate
color-intent document. Every `color_regions[]` record requires `name`, owning
`part`, `hex`, `purpose`, `boundary`, and `evidence`. Also record the
`palette_reduction` decision.

- For `single-part`, two or more regions may share the top-level physical part;
  use `print_package_mode: "co_print_body"`.
- For `multipart`, every region name is globally unique and `part` identifies
  its owning physical part. `export_assembly()` binds one whole-part material
  assignment per BRep part. Use
  `print_package_mode: "separate_parts"` for the physical-part package.

Raw intent JSON with `color_regions` must include `print_package_mode`.
The public `write_intent` helper records this field from the chosen
`manufacturing_mode`: `co_print_body` for `single-part`, `separate_parts` for
`multipart`. Its callers supply the color regions and palette decision, without
adding a `print_package_mode` argument. CLI validation and QA require the field
in the resulting document.

```json
"color_regions": [
  {
    "name": "body",
    "part": "product",
    "hex": "#E8E4DC",
    "purpose": "continuous structural body",
    "boundary": "parent volume excluding the shallow accent inset",
    "evidence": "the requested body is warm ivory",
    "continuity": "continuous-core"
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

Color intent fields remain in this contract. Export selection, material-plan
bindings, region topology, and 3MF readback belong to `a3d guide color` and,
when routed there, `color/BACKEND.md`.

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
- Bind critical functional features to their actual geometry: use named
  `observe()`/checked-operation evidence and bind those same BRep objects as
  scene feature nodes.
- For pixel art, run `a3d reference IMAGE --out REPORT.json`. When the report
  supplies `pixel_grid`, use its cells and colors directly with the recorded
  source-image hash. A `general-image` result calls for image-based interpretation
  rather than an assumed pixel grid.
- If a required target remains unknowable and changes function or identity,
  ask. Otherwise choose a reversible assumption and record it.

## Visual acceptance

`compare_silhouette.py` is valid only
for a flat or genuinely corresponding orthographic reference. Its IoU cannot
prove depth, semantic identity, or printability.

After each visual read, list target-specific deltas. Use a compact landmark
rubric proportional to the request, usually 3-7 identity-critical landmarks for
recognizable objects rather than every small decorative detail. A failed
must-have landmark remains failed even if mesh integrity and dimensions pass;
optional micro-detail gaps may be reported as compromises.
