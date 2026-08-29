# Evidence contract

The intent contract is the independent target used to judge the model. Write
it before geometry and validate it with `intent_contract.py`. Never rewrite
targets merely to match a generated artifact.

## Required structure

```json
{
  "schema": "evidence-cad-intent/v4",
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
printer profile is small; if the user did not fix the final size, scale the
whole model first. Use `multipart` only when separate printed parts create a
real manufacturing benefit such as cleaner support strategy, better strength
orientation, post-installed components, functional movement, or separable
covers, inserts, hinges, latches, or slides inferred from the object.

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
      "clearance_mm": 0.3,
      "engagement_mm": 2.0,
      "features": ["lid-tab", "lid-slot"],
      "acceptance": "2 mm printable tab enters the lid slot with 0.3 mm clearance"
    }
  ]
}
```

Each multipart `parts[].name` becomes an exported STL suffix. Do not convert a
separate requested lid or cover into an open-top single body unless the user
explicitly asks for a one-piece slip-on sleeve. Do not export separate parts
unless their interfaces name modeled connector feature IDs.

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
- For pixel art, use `reference_analyze.py` cells and colors directly. Do not
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
- `NAME-assemble.step` is audited with OCCT for CAD readability, solid count,
  and dimensions. STEP checks do not replace mesh printability checks.
- `NAME-display.glb` is the user-visible display model. GLB display checks can
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
