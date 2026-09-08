# Public BRep authoring examples

Choose the example that demonstrates the API structure you need:

- `simple_brep_intent.py` and `simple_brep_build.py`: one rounded body with a
  blind pocket, showing physical feature binding and `export_part()`.
- `assembly_intent.py` and `assembly_build.py`: a removable pin and blind socket
  coupon, showing two manufactured parts, a locating fit and `export_assembly()`.
- `surface_shell_intent.py` and `surface_shell_build.py`: an outer BRep loft and
  an inner loft cutter, showing a section-controlled shell and `export_part()`.
- `installed_module_intent.py` and `installed_module_build.py`: a configurable
  module installation with a real window, support, access, optional preview
  reference and installation evidence against final manufactured parts.

These are API examples. Develop the actual shape, dimensions, features and part
boundaries from the user's request. Keep manufactured parts as valid BRep solids,
bind their features with `write_scene()` and export genuine STEP masters with
the matching exporter. `surface-shell.md` explains the loft example's controls
and wall-thickness checks.

Run in the current session workspace (the runtime supplies `AMAGINE3D_SKILL_DIR`).
Set `example_name=assembly` for the two-part example or `surface_shell` for the
lofted enclosure, or `installed_module` for component installation. The profile below is an
illustrative fallback; use the selected machine/nozzle or point the intent source
at an existing valid profile.

```bash
example_name=simple_brep
a3d mark --mark ".${example_name}.generation-start"
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out "${example_name}_printer-profile.json"
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_intent.py" .
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_build.py" .
python3 "${example_name}_intent.py"
a3d intent "${example_name}_intent.json"
a3d compile "${example_name}_scene.json" --marker ".${example_name}.generation-start" --intent "${example_name}_intent.json" --source "${example_name}_build.py" --output-dir .
```

The intent source writes the target separately. The compiler executes the build
with the bound intent, scene and output paths in environment variables. The build
constructs physical objects, records features, binds those same objects into scene
nodes, then exports. `checked_cut` records the actual cutter used in the body;
`observe` records additive features. Inspect the returned preview with `view_image`.

## Reuse one feature identity

`BrepFeature` carries one feature ID, physical owner, role, and actual BRep
object. Use its checked operation and binding methods together so that renaming
a feature or changing its placement cannot leave a second hand-written label
behind in operation evidence:

```python
from pathlib import Path
from build123d import Box
from geometry_binding import BrepFeature

body = Box(30, 20, 5)
slot = BrepFeature("service-slot", "housing", "cutter", Box(8, 4, 8))
body = slot.cut_from(body)
slot_node = slot.bind(Path("service-slot.stl"))
body_node = BrepFeature("housing-body", "housing", "solid", body).bind(
    Path("housing-body.stl")
)
# Pass [body_node, slot_node] as the housing's nodes to write_scene().
```

`cut_from()` returns the changed solid; retain that return value. `bind()`
records the feature observation and returns a scene node containing the same
ID and owner. The handle does not contain acceptance dimensions or prove that
an installation works: final-part geometry checks remain independent. A cutter
that misses the body still fails its checked operation.

## Stable targets and explicit revisions

Repair source geometry and scene bindings against the existing intent. A new
filename alone does not establish a changed target. For an allowed parameter
adjustment, record the range before modeling in `dimensions_mm`, for example:

```python
dimensions_mm["x"] = {
    "value": 40, "source": "inferred", "confidence": "medium",
    "constraint": {"kind": "range", "min_mm": 38, "max_mm": 42},
}
```

When the target actually requires a revision, `write_intent(..., revision=...)`
accepts a parent file binding and reason:

```python
from hashlib import sha256
from pathlib import Path

parent = Path("enclosure_intent.json").resolve()
revision = {
    "parent": {"path": str(parent), "sha256": sha256(parent.read_bytes()).hexdigest()},
    "kind": "parameter-adjustment",
    "reason": "Adjust width within the previously declared range.",
}
```

`parameter-adjustment` is checked against that prior range. `target-change`
requires `evidence: {"kind": "user-request", "text": "..."}`;
`evidence-correction` requires `kind: "external-evidence"` with the observed
source. These records preserve lineage; they do not permit deleting requirements
to make a failed audit disappear. Keep measured dimensions in build evidence.

## Assembly-specific relationships

The pin rests in a blind socket and remains removable; the example provides no
anti-pullout retention. Feature `direction` follows the outward face normal
(the top socket is `+Z`), while insertion `assembly_axis` is independently `-Z`.
The cutter opens the socket on that top face. Endpoint `diameter` measurements
describe the socket and pin; the female diameter is derived from the pin and a
per-side gap. Purchased hardware would remain outside the export part dictionary,
with its required seat or keepout modeled on the receiving part.

For a signature with required/optional arguments, query just that function:

```bash
a3d capabilities --symbol write_intent
a3d capabilities --symbol write_scene
a3d capabilities --symbol export_part
a3d capabilities --symbol export_assembly
a3d capabilities --symbol BrepFeature
a3d capabilities --symbol BrepFeature.bind
a3d capabilities --symbol BrepFeature.cut_from
a3d capabilities --symbol plan_plates
```

`plan_plates(bboxes, profile, *, spacing_mm=5, edge_margin_mm=0, max_plates=1)`
checks layout plans on the selected printer. Increasing `max_plates` permits a
multi-plate plan; it does not produce a multi-plate 3MF. The current strict
`export_assembly()` manufacturing path still exports a single plate. Report
the plan and outstanding export work accurately rather than changing the
printer to suppress a packing failure.
