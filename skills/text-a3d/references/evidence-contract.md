# Evidence contract

The intent contract is the independent target used to judge the model. Write
it before geometry and validate it with `intent_contract.py`. Never rewrite
targets merely to match a generated artifact.

## Required structure

```json
{
  "schema": "evidence-cad-intent/v2",
  "part": "part-name",
  "task_mode": "reference-reproduction",
  "representation": "full-3d",
  "reference_files": [
    {"path": "/absolute/reference.png", "sha256": "...", "role": "front appearance"}
  ],
  "dimensions_mm": {
    "x": {"value": 120, "source": "user", "confidence": "high"},
    "y": {"value": 55, "source": "inferred", "confidence": "low"},
    "z": {"value": 28, "source": "reference", "confidence": "medium"}
  },
  "features": [
    {
      "id": "screen-recess",
      "evidence": "dark centered rectangle in front reference",
      "acceptance": "centered; width 72 ± 1 mm; depth 1.2 ± 0.2 mm"
    }
  ],
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

## Evidence rules

- User values outrank standards, standards outrank reference measurement, and
  reference measurement outranks inference.
- Every inferred dimension must be exposed with low or medium confidence.
- A photograph proves visible relationships, not hidden-side dimensions.
- Landmarks describe identity-bearing relationships. “Looks similar” is not
  an acceptance criterion.
- For pixel art, use `reference_analyze.py` cells and colors directly. Do not
  redraw coordinates from memory.
- If a required target remains unknowable and changes function or identity,
  ask. Otherwise choose a reversible assumption and record it.

## Visual decision

The four-view render detects unintended depth and topology; the matched view
tests silhouette and landmark placement. `compare_silhouette.py` is valid only
for a flat or genuinely corresponding orthographic reference. Its IoU cannot
prove depth, semantic identity, or printability.

After each visual read, list target-specific deltas. A failed landmark remains
failed even if mesh integrity and dimensions pass.
