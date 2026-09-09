---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Work only in the current session workspace; never inspect sibling sessions. Use
public `a3d` commands and keep editable source beside artifacts. Model manufactured
parts as build123d BRep solids with genuine STEP masters; derive meshes from them.

## Develop the geometry

Start from the requested dimensions, defining contours and functional datums.
Separate user requirements from your construction choices. For a functional device,
use component envelopes and shared datums to lay out the body, cavity and openings
in the first draft. Missing component dimensions call for reversible, explicit
assumptions; they do not reduce a functional device to an appearance model.
For installed components, adapt the shared datums in `examples/installed_module_build.py`.
Draft the body and cavity before intent or profile, then develop mounting and
access on the visible construction before rounding the exterior.

For appearance-led work, inspect the supplied images or establish a reference
using the image guidance below. Read `references/surface-design.md` to choose
surface controls, then inspect the primary form before detail makes proportion
changes expensive. Carry the defining relationships into intent acceptance.

Use one geometry source for exploration and final compilation. If a new model's
form or arrangement is unresolved, start a `BuildSession` with manufactured part
names and preview it before writing the full intent:

```python
build = BuildSession(__file__, part_names=("housing", "cover"))
build.add("housing-body", housing_solid, part_name="housing")
build.cut("viewing-window", window_cutter, part_name="housing")
build.add("cover-body", cover_solid, part_name="cover")
build.export()
```

Run `a3d draft <name>_build.py`, then inspect its returned preview with `view_image`.
For an existing source that needs intent, use `a3d draft SOURCE.py --intent INTENT.json`.
Drafts are isolated previews with no final acceptance. Keep the chosen geometry
in this same source; final compile requires matching parts, features and owners
in intent. `examples/simple_brep_build.py` demonstrates both phases;
`references/authoring-example.md` covers multipart ownership and component previews.
Query needed signatures together with `a3d capabilities --symbol NAME`.

## Compile the chosen construction

For a functional device, verify usable space, openings from outside into the target cavity,
support, retention and assembly access before final compile. Use `references/design-review.md`
and bind applicable installed-component evidence with `references/installation-checks.md`.

Keep purchased references outside manufactured parts. They may appear in the final
`NAME-display.glb` assembly preview; no dummy component is required. Installation
geometry remains necessary whether or not references are displayed.

Use the selected printer and existing valid profile. Resolve usable print volume
before fixing dimensions or details. For multipart work, use `a3d layout` on part
bounds in print orientation with spacing, edge margin and plate count; see
`references/bambu-printability.md`. Keep assembled size, part size and plate
occupancy distinct. Packing failure does not authorize changing target dimensions
or the printer. If no selection exists, this example profile is a fallback whose
process assumption belongs in intent:

```bash
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out "<name>_printer-profile.json"
```

Run `<name>_intent.py` separately to write `<name>_intent.json`; validate with
`a3d intent`. Intent records final requirements and acceptance. Build source owns
construction choices such as section stations, cutter overshoot and loft mode;
expose controlling dimensions near its top. Choose an API example to adapt:

- One BRep part: `examples/simple_brep_intent.py` and `simple_brep_build.py`.
- A simple mating pair: `examples/assembly_intent.py` and `assembly_build.py`.
- Component installation: `examples/installed_module_intent.py` and its build source.
- A section-controlled shell: `references/surface-shell.md`.

`BuildSession.add`/`cut` derive scene bindings and operation evidence. Submit final
edits with `finish`, then `export`. `build.part()` returns an inspection copy;
editing it alone does not alter the exported solid. Use `checked_fillet()` and
`checked_chamfer()`; never silently return unfinished input after a failed finish.
The explicit `write_scene` and `export_part`/`export_assembly`/`export_regions`
APIs remain available for explicit bindings and manufactured color.

Compile, audit, package and render through the public boundary:

```bash
a3d compile "<name>_scene.json" --intent "<name>_intent.json" \
  --source "<name>_build.py" --output-dir .
```

Compile binds stable input hashes and owns output freshness; no manual generation
marker is needed. Keep selected inputs unchanged while it runs. The compact result
contains actionable findings and full evidence paths. Follow diagnostic pagination
for omitted blockers; query a field with
`a3d diagnose RESULT.json --id ID --field FIELD`. Loading every evidence file into
context is unnecessary. See `references/cad-compile.md` for lifecycle and queries.

## Repair and verify

Group findings by their source cause and make a coordinated source edit. Boolean
failure witnesses identify the owning solid and operand components in operation
coordinates, with measured gaps and nearest points. Check the datum, owner and
intended connection before changing dimensions. A missed cut may lie in empty
space; making a larger cutter is not by itself a repair. Keep intended form and
function while replacing construction choices that fail. Do not remove a defining
feature just to obtain a valid solid. For construction-specific failures, consult
`references/construction-strategies.md`.

Use the current attempt's `preview`, or `diagnosticPreview` on failure, with native
`view_image`. Previous successful render pointers may be stale. If the image cannot
be interpreted, visual review remains incomplete. Inspect the actual form and
functional relationships; compile success alone does not establish model quality.
`references/design-review.md` covers fit and assembly; `references/bambu-printability.md`
distinguishes process advisories from defects requiring a change.

Compare final STEP measurements with intent targets, including feature acceptance.
For dimensions at a height, use `a3d measure MODEL.step --section-z HEIGHT`; repeat as needed.
A fixed size stays an equality during repair. Use only declared ranges; inferred values are not ranges.
For drifting loft or finishing dimensions, use `references/surface-shell.md` to
calibrate coupled controls and assert all final dimensions after the complete construction.
For a shape edit, save the previous display GLB and use
`a3d compare BEFORE.glb AFTER.glb --view front` for a shared camera and scale.
Inputs must share coordinates and units. A projected change does not score quality,
and outer/inner section widths do not establish minimum wall thickness. Thickness
witnesses and repeated-warning comparisons describe local measurements; check their
location, material direction and sampling scope before claiming a defect resolved.

Use the newest valid build report's paths as the working set. Keep source/output
names stable for repairs; create another model version for a requested variant or
snapshot. Preserve intent when its targets have not changed. A changed target needs
a separate intent revision with verified parent path/SHA and a reason, retaining
part identity and source/output paths. See `references/evidence-contract.md`.
Deleting an unmet requirement changes scope. Deliver editable and manufacturing
files with specific observations and remaining limitations.

## Reference images and dimensions

Inspect uploaded images directly as the primary visual reference. For appearance-led
work without one, use available native search to find and actually view a small
relevant set when network access is enabled. Record URLs and useful silhouette,
proportion or surface relationships in the workspace, separating observations from
interpretation. Dimension-driven parts need no unrelated image search.

Follow runtime network instructions. `CODEX_WEB_SEARCH_ENABLED` defaults to true;
false disables search. Enabled configuration does not guarantee provider tools or
image perception. Use available tools directly, without per-task capability probes;
if a step fails, identify missing evidence and continue with supplied/local evidence.
A page title is not visual inspection. For deterministic palette/silhouette facts,
use `a3d reference IMAGE --out REPORT.json`. Perspective appearance is approximate;
do not claim exact reproduction without measurements.

For uncertain fit or mounting dimensions, use primary component drawings or supplier
specifications when network access is enabled. Carry exact component identity and
source into intent; a similar product is not an exact specification. Otherwise keep
proposed envelopes and dimensions as reversible parameters.

## Pull details when needed

Route by the actual construction or unresolved question:

- Unclear strategy: `a3d guide strategy`.
- Pressable/sliding mechanism: `a3d guide pressable-control`.
- Multipart assembly: `a3d guide multipart`; for fastening into printed plastic or
  serviceable enclosure closure, `references/multipart-connections.md`.
- Final component fit, insertion, support, retention or passage: `references/installation-checks.md`.
- Non-manufactured display: `references/installed-displays.md`.
- Permanent printed color: `a3d guide color`; for multiple regions inside one part or
  uncommon topology, `color/BACKEND.md`.
- Uncertain API: `a3d capabilities --symbol NAME`.
- Intent field meaning after its helper: `references/evidence-contract.md`.

Inspect internals only when the relevant guide, capability result and reported
error are insufficient.
