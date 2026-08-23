# Color evidence contract

Write `<name>_intent.json` before geometry and validate it with this
skill's `intent_contract.py`.

```json
{
  "schema": "evidence-color-intent/v2",
  "part": "product-name",
  "representation": "full-3d",
  "reference_files": [
    {"path": "/absolute/reference.png", "sha256": "...", "role": "appearance"}
  ],
  "dimensions_mm": {
    "x": {"value": 140, "source": "inferred", "confidence": "medium"},
    "y": {"value": 60, "source": "inferred", "confidence": "low"},
    "z": {"value": 30, "source": "reference", "confidence": "medium"}
  },
  "color_regions": [
    {
      "name": "housing",
      "hex": "#E8E4DC",
      "purpose": "main enclosure",
      "boundary": "complete parent shell",
      "evidence": "warm light housing in reference"
    },
    {
      "name": "screen",
      "hex": "#171A1D",
      "purpose": "identity-bearing front display",
      "boundary": "front inset rectangle",
      "evidence": "dark screen region"
    }
  ],
  "palette_reduction": {
    "source_colors": 7,
    "filament_limit": 4,
    "plan": "product_palette.json",
    "deliberate_merges": ["two photographic highlights merge into housing"]
  },
  "visual": {
    "required": true,
    "reference_view": "front",
    "landmarks": ["screen centered", "top control distinct", "right knob distinct"]
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
