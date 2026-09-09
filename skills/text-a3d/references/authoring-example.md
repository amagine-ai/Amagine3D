# Public BRep authoring examples

Choose the example that demonstrates the API structure you need:

- `simple_brep_intent.py` and `simple_brep_build.py`: one rounded body with a
  blind pocket, showing `BuildSession.add`, `finish`, `cut` and `export`.
- `assembly_intent.py` and `assembly_build.py`: a removable pin and blind socket
  coupon, showing two manufactured parts and a locating fit.
- `surface_shell_intent.py` and `surface_shell_build.py`: an outer BRep loft and
  an inner loft cutter, showing a section-controlled shell.
- `installed_module_intent.py` and `installed_module_build.py`: a configurable
  module installation with a real window, support, access, optional preview
  reference and installation evidence against final manufactured parts.

These are API examples. Develop the actual shape, dimensions, features and part
boundaries from the user's request. `BuildSession` binds the authored BRep solids
through the existing `write_scene` and STEP exporters. `surface-shell.md` explains
the loft example's controls and wall-thickness checks.

Run in the current session workspace (the runtime supplies `AMAGINE3D_SKILL_DIR`).
Set `example_name=assembly` for the two-part example or `surface_shell` for the
lofted enclosure, or `installed_module` for component installation. The profile below is an
illustrative fallback; use the selected machine/nozzle or point the intent source
at an existing valid profile.

```bash
example_name=simple_brep
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_build.py" .
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out "${example_name}_printer-profile.json"
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_intent.py" .
python3 "${example_name}_intent.py"
a3d intent "${example_name}_intent.json"
a3d compile "${example_name}_scene.json" --intent "${example_name}_intent.json" --source "${example_name}_build.py" --output-dir .
```

The intent source writes the target separately. The compiler executes the build
with the bound intent, scene and output paths in environment variables. The build
constructs physical objects; its session derives feature evidence and scene nodes
from those objects, then exports. Inspect the returned preview with `view_image`.

`simple_brep_build.py` and `installed_module_build.py` declare `part_names`:
after copying the build source, run `a3d draft SOURCE.py` before creating an
intent or profile. The installed-module source owns its construction controls;
its intent generator imports them without running geometry and keeps the brief's
overall and component dimensions independent. No parameter JSON is needed.
The draft's `constructionFeatures` lists registered IDs, owners and roles to reuse
when writing intent; it is not the requirements or a complete list of operations.
For the other examples, or a source that reads intent parameters, create the
intent first and use `a3d draft SOURCE.py --intent INTENT.json`. Draft export is
isolated under `.amagine3d-drafts`; it carries no final acceptance. Keep the same
geometry source for final compile. A source without intent can declare
`BuildSession(__file__, part_names=("housing", "cover"))` and use
`build.add("cover-body", solid, part_name="cover")` (also on `cut`/`observe`).
Final intent must declare these same parts, features and owners. Pass optional
preview component envelopes as `build.export(draft_references={"module": solid})`;
they do not become manufactured parts or final installation evidence.

## Reuse one feature identity

Declare feature IDs once in intent. `BuildSession` obtains their physical owners
from that contract; `add` and `cut` use each ID once for geometry, scene and evidence:

```python
from build123d import Box
from build_session import BuildSession

build = BuildSession(__file__)
build.add("housing-body", Box(30, 20, 5))
build.cut("service-slot", Box(8, 4, 8))
build.export()
```

For a final fillet, use `build.finish("housing", lambda body:
checked_fillet(body, body.edges(), RADIUS, "edge-rounding"))`. `finish` commits
the returned solid and checked evidence; if the operation ID names an intent
feature, it also binds that feature. Implementation-only operation IDs need no
additional intent feature. In either case,
selectors must come from the callback's body. `build.part("housing")` returns a
copy for inspection, so changing that copy alone does not change exported geometry.

`observe(id, shape)` binds a physical observation without a material operation;
omitting shape observes the current owning part. Naming a whole-part observation
"floor" does not measure floor thickness. Use `role="solid"` to identify a solid
feature already contained in the part, such as a screw boss within a thick corner.
A missed cut still fails, and final
geometry, interface and installation checks remain independent. Paired interfaces,
raw screw interfaces and installation checks pass through `export`; screw recipe
nodes are derived from the interface's feature IDs. See the installed-module example.

The installed-module example uses a local `interval_box` with explicit lower/upper
bounds for each axis; a lower-bound datum cannot also be used as a centered Box's
position. Reuse the module and window intervals for cavities and installation
paths so geometry and its witnesses share the same datums.

For explicit bindings or regional color exports, the existing `BrepFeature`,
`write_scene` and exporters remain available. Use `with build.capture():` if
additional existing helper parameters or observations need the session's evidence.

## Stable targets and explicit revisions

Repair source geometry and scene bindings against the existing intent. A new
filename alone does not establish a changed target. Intent records final targets
and acceptance, while build source owns implementation parameters such as section
count, cutter overshoot and loft mode. An inset may change to meet an unchanged
minimum wall requirement; a user-requested dimension or form must remain satisfied.
For an allowed target parameter
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

Query the signatures needed for the next operation together:

```bash
a3d capabilities --symbol BuildSession --symbol BuildSession.add --symbol BuildSession.cut --symbol BuildSession.finish --symbol BuildSession.export
```

`plan_plates(bboxes, profile, *, spacing_mm=5, edge_margin_mm=0, max_plates=1)`
checks layout plans on the selected printer. Increasing `max_plates` permits a
multi-plate plan; it does not produce a multi-plate 3MF. The current strict
`export_assembly()` manufacturing path still exports a single plate. Report
the plan and outstanding export work accurately rather than changing the
printer to suppress a packing failure.
