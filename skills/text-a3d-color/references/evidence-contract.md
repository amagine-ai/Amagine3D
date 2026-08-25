# Color evidence contract

Write `<name>_intent.json` before geometry and validate it with this
skill's `intent_contract.py`.

```json
{
  "schema": "evidence-color-intent/v3",
  "part": "product-name",
  "task_mode": "reference-reproduction",
  "representation": "full-3d",
  "reference_files": [
    {"path": "/absolute/reference.png", "sha256": "...", "role": "appearance"}
  ],
  "dimensions_mm": {
    "x": {"value": 140, "source": "inferred", "confidence": "medium"},
    "y": {"value": 60, "source": "inferred", "confidence": "low"},
    "z": {"value": 30, "source": "reference", "confidence": "medium"}
  },
  "features": [
    {
      "id": "fastener-clearance",
      "evidence": "The assembly specification requires a through fastener.",
      "acceptance": "Clear diameter 3.4 mm; continuous through the housing wall."
    },
    {
      "id": "accent-inset",
      "evidence": "The contrasting front insert is identity-bearing.",
      "acceptance": "Centered inset; printable boundary and no overlap with housing."
    }
  ],
  "color_regions": [
    {
      "name": "housing",
      "hex": "#E8E4DC",
      "purpose": "main enclosure",
      "boundary": "complete parent shell",
      "evidence": "warm light housing in reference",
      "material": {"transmission": "opaque", "filament": "matte warm-white PLA"}
    },
    {
      "name": "accent",
      "hex": "#171A1D",
      "purpose": "identity-bearing front insert",
      "boundary": "front inset rectangle",
      "evidence": "dark contrasting insert region",
      "material": {"transmission": "opaque", "filament": "matte charcoal PLA"}
    }
  ],
  "palette_reduction": {
    "source_colors": 7,
    "filament_limit": 4,
    "plan": "product_palette.json",
    "deliberate_merges": ["two photographic highlights merge into housing"]
  },
  "printability": {
    "profile": {
      "path": "product-name_printer-profile.json",
      "sha256": "..."
    },
    "build_axis": "+Z",
    "bed_contact": "z-min",
    "support_policy": "support-free",
    "minimum_wall_target_mm": 0.9,
    "critical_features": ["fastener-clearance", "accent-inset"]
  },
  "visual": {
    "required": true,
    "reference_view": "front",
    "landmarks": ["accent inset centered", "top control distinct", "right knob distinct"]
  },
  "assumptions": []
}
```

Color sampled from a photograph is evidence, not automatically a filament.
Separate semantic regions first; reduce shades inside each semantic region
second. Preserve rare colors when they encode a logo, control, status, or
material boundary. Record every deliberate merge.

The contract must distinguish permanent printed color from transient display
content. A real LED/LCD screen is usually one physical screen region; reproduce
individual pixels only when the user wants a static decorative face or mosaic.

Every region declares `material.transmission` as `opaque`, `translucent`, or
`transparent`. A non-opaque region must name its filament; an opaque region may
also do so. The filament remains a manufacturing instruction rather than
something RGB-only 3MF readback can prove. `export_regions()` cross-checks
contract names and colors and writes a material plan that preserves these
optical assignments.

Declare functional and identity-bearing requirements independently in
`features`. Every item needs evidence and a concrete acceptance condition.
Every `printability.critical_features` ID must reference that list and must
later appear as a named `observe()` record or checked operation in the v3
build report. For routed cavities, record a representative cross-section as a
separate named feature; the overall bounding box of a bent or compound cutting
tool does not prove local clearance.

The printability profile must come from this skill's `bambu_profile.py`. Its
hash locks the machine, selected tool, nozzle, process, bed, feature floor,
wall target, and support threshold. Use the combined manufacturing STL for
printability evidence; isolated region meshes are topology evidence only.

Allowed task modes are `specification`, `reference-reproduction`,
`reference-inspired`, `recognizable-form`, and `inspect`. Matched visual views
may be `front`, `side`, `top`, `bottom`, or `isometric`; use `bottom` when the
appearance-bearing face is intentionally printed at Z0.
