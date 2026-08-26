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

The printability profile must come from this skill's `bambu_profile.py`. Its
hash locks the machine, selected tool, nozzle, standard process, printable
polygon, wall targets, and support threshold used for the run. The minimum
wall target must meet the resolved process wall target. Use `support-free`
unless supports are explicitly accepted or unavoidable.

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

Always declare `manufacturing`. Use `single-part` for one printed body. Use
`multipart` when the requested object needs separate same-material parts such
as a lower shell and top lid, snap-on cap, removable cover, insert, hinge leaf,
latch, or slide.

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
      "id": "lid-body-seam",
      "between": ["lower-shell", "top-lid"],
      "clearance_mm": 0.3,
      "acceptance": "non-overlapping mating faces with a visible seam"
    }
  ]
}
```

Each multipart `parts[].name` becomes an exported STL suffix. Do not convert a
separate requested lid or cover into an open-top single body unless the user
explicitly asks for a one-piece slip-on sleeve.

## Evidence rules

- User values outrank standards, standards outrank reference measurement, and
  reference measurement outranks inference.
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
placement. `compare_silhouette.py` is valid only
for a flat or genuinely corresponding orthographic reference. Its IoU cannot
prove depth, semantic identity, or printability.

After each visual read, list target-specific deltas. A failed landmark remains
failed even if mesh integrity and dimensions pass.
