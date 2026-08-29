---
name: text-a3d-color
description: >
  Evidence-driven multi-color CAD and manufacturing-region synthesis. Creates
  a colored print 3MF, clean manufacturing mesh, STEP assembly masters, and
  display GLB models from explicit color semantics, deterministic palette
  reduction, strict overlap/coverage checks, archive color readback, pinned
  Bambu printability evidence, provenance hashes, and mandatory colored-view
  review. Region meshes are internal intermediates, not printable part
  deliverables.
  Takes priority when reference colors identify screens, controls, text/logos,
  materials, inlays, functional regions, or the object's recognizable palette,
  even if the user does not mention multi-color, 3MF, or AMS.
---

# Evidence-driven color CAD

This skill treats color as manufactured geometry with semantic purpose. A
valid 3MF object count is insufficient: region topology, stored palette,
appearance, source evidence, and current-run provenance must agree.

`<SKILL_DIR>` means this directory. Resolve it to an absolute path before
running commands from a nested output directory. Outputs belong directly in the
current session working directory.

## Resources

- `reference_analyze.py` extracts objective image/pixel/palette evidence
- `palette_plan.py` reduces source colors into a deterministic printable palette
- `bambu_profile.py` resolves this skill's pinned Bambu machine/process limits
- `intent_contract.py` validates color semantics and boundaries before geometry
- `examples/intent.example.json` is a copyable valid color contract
- `cad_helpers.py` validates regions, optional parent coverage, and provenance
- `export_3mf.py` writes a shared palette and reads object colors from 3MF XML
- `qa_check.py` audits region topology and the clean manufacturing mesh
- `assembly_check.py` cross-checks build regions against the stored 3MF colors
- `step_check.py` audits STEP assembly masters with OCCT
- `render_preview.py` creates orthographic, hash-bound color evidence
- `freshness_check.py` and `compare_silhouette.py` close the run
- Read `references/evidence-contract.md` for every generated color task
- Read `references/color-architecture.md` before choosing region interfaces
- Read `references/bambu-printability.md` before every printable color task

Requires build123d, lib3mf, trimesh, Rtree, Pillow, and NumPy. Preview rendering uses
the headless, single-process CPU Z-buffer in `cpu_z_buffer.py`; it does not
need a GPU, display server, OpenGL, or Matplotlib.
The renderer defaults to a 640-pixel output, 1x supersampling, a 1280-pixel
internal view limit, and 500,000 input triangles. `--supersample 2`,
`--max-resolution`, and
`--max-triangles` may adjust those values within the built-in hard caps.
When running outside Amagine3D's managed session, initialize the repository
runtime and use its Python executable instead of an unrelated system Python.

## 0. Route and interpret color

Use this skill when object-owned color affects identity or separates a screen,
control, logo/text, material, inlay, or functional region. Do not route here
for lighting, shadow, reflection, background, or photo noise alone. An explicit
single-color request routes to `text-a3d`.

When the user names a specific real, catalog, branded, or fictional object, the
named object sets the identity target. When adequate reference images,
drawings, scans, or reliable dimensions are supplied, use
`reference-reproduction` and preserve the identity-bearing form and color
regions. When no reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, generate a faithful-inspired object from broad known
landmarks, and clearly report that it is not an exact replica.

Distinguish permanent printed color from transient display content. A physical
LED/LCD is normally one screen region; model individual lit pixels only for a
requested static decorative face or mosaic.

RGB stored in a 3MF does not prove optical behavior. Record every region as
`opaque`, `translucent`, or `transparent` in the intent when optical behavior
changes the model. Do not invent real filament assignments; the user chooses
actual slicer materials.
Color regions are co-printed partitions, not printable assembly parts. If the
object needs real separately printed parts, design printable interfaces first;
do not turn `NAME-region-REGION.stl` into a user deliverable.
The default `printability.print_package_mode` is `co_print_body`: all color
regions belong to one printable body and must be packaged as one top-level 3MF
mesh build item with per-triangle color properties. Use `separate_parts` only
for intentional multipart prints with real assembly interfaces.

## 1. Open the evidence run

Create a marker before new files:

```bash
python "<SKILL_DIR>/freshness_check.py" --mark ".<name>.generation-start"
python "<SKILL_DIR>/bambu_profile.py" --machine <machine-id> --nozzle <0.2|0.4|0.6|0.8> --tool <N> --out "<name>_printer-profile.json"
python "<SKILL_DIR>/reference_analyze.py" "/absolute/reference.png" --out "<name>_reference.json"
```

Honor a named user or project printer. Otherwise omit `--machine` to resolve
the conservative A1 mini default and record the assumption. Read the generated
profile before modeling; never change it later merely to clear QA.

If the user supplied no image, skip `reference_analyze.py`, set
`reference_files` to `[]`, and record which identity, dimension, and palette
targets are inferred rather than evidenced. A no-reference run should be framed
as reference-inspired or recognizable-form, not exact reference reproduction.

When source colors exceed available color channels, create a proposed plan:

```bash
python "<SKILL_DIR>/palette_plan.py" "<name>_reference.json" --max-colors <N> [--keep "#RRGGBB"] --out "<name>_palette.json"
```

Write `<name>_intent.json` from
`references/evidence-contract.md`. Semantic regions may override automated
frequency: rare logo/control colors are not disposable. Validate:

```bash
python "<SKILL_DIR>/intent_contract.py" "<name>_intent.json"
```

The contract must bind the profile hash, fixed object coordinate system, build
orientation, support policy, wall target, feature kind/face/direction for
functional openings, functional acceptance criteria, critical feature IDs,
replica-fidelity limits, and each region's optical transmission. Critical IDs
must later resolve to named build evidence; for routed cavities, observe a
representative local cross-section.

Printability must not rewrite the object. A full-3D replica must model the
bottom, side, back, and underside forms that belong to the object. Do not make a
flat-backed prop, relief, or plain planar underside merely to avoid supports or
make Z0 contact. Solve print concerns through rigid orientation, permitted
multipart interfaces, or an honest `supports-required`/warning result.

## 2. Design region architecture

Read `references/color-architecture.md` and
`references/bambu-printability.md`. Choose parent split, inset, raised overlay,
or separately assembled insert for every boundary. Build the complete parent
form first when regions collectively represent one co-printed body; this
enables coverage checking, a clean whole-body STL, and support analysis without
false positives at material interfaces.

For replica work, build the semantic object first and choose print orientation
second. Color boundaries, palette reductions, and support strategy must not
remove object-owned underside/back-side form or turn a full-3D request into a
flat relief.

Use the fixed object frame from the intent: `+X` user right, `+Y` object back,
and `+Z` object top. Front is `Y-min`; bottom is `Z-min`. Put ports, holes, and
cutouts on named semantic faces. A bottom opening is valid when the contract
says it belongs on the bottom; an accidental front/bottom edge cut is a design
failure, not a Z0 rule failure.

The color contract defines region name, hex, purpose, geometric boundary,
evidence, and optional optical material. Use the profile's line-width and wall
targets for every boundary. Do not collapse distinct semantic regions merely to
fit an arbitrary palette limit; record every compromise.
For handles, shells, posts, housings, and other continuity-bearing cores, do not
let a color region slice the load path into multiple independent solids. Model
contrasting bands, logos, stripes, runes, and trim as outer shells, shallow
insets, raised overlays, or shallow filled grooves so the structural core stays
continuous. Mark such regions with `continuity: "continuous-core"` when QA
should enforce single-solid continuity.

## 3. Build and export strict regions

Write the complete parametric source in this run. Use stable region and feature
IDs:

```python
import sys
sys.path.insert(0, r"<SKILL_DIR>")
from build123d import *
from cad_helpers import parameter, observe, checked_cut, export_regions

NAME = "<name>"
INTENT = "<name>_intent.json"
WIDTH = parameter(
    "overall-width", 80.0,
    min_value=48.0, max_value=140.0, step=0.5,
    unit="mm", label="Overall width", label_zh="总体宽度",
    group="Envelope", group_zh="外形尺寸",
    affects=("complete-parent",),
)
parent = ...
observe(parent, "complete-parent", "parent")

# Derive regions through declared splits/insets; no coincident duplicate skins.
regions = {
    "housing": (housing, "#E8E4DC"),
    "screen": (screen, "#171A1D"),
}

if __name__ == "__main__":
    export_regions(regions, NAME, parent=parent, intent_path=INTENT)
```

`export_regions()` chooses one print orientation from the semantic parent shape
before final export. It evaluates the six bed-facing orientations: identity,
front/back side lays, left/right side lays, and a top-down 180-degree flip. Each
candidate records the uniform scale needed to fit the selected profile and is
scored after that scale is applied, so a lower-support pose is not rejected just
because it is too large before scaling. Support burden is estimated with a
support-volume proxy, not only downward face area. Support burden and bed
contact quality outrank low print height and scale penalty, so a taller
top-down pose may beat a lower side-lay when it materially reduces supports.
It then emits `NAME.3mf` as the preferred multi-color print package and
`NAME.stl` as the clean whole-body manufacturing mesh in selected print
coordinates. `NAME-assemble.step` and `NAME-display.glb` preserve the semantic
object orientation for CAD review and visual fidelity. The report keeps the
original semantic bounds plus rotation/scale/translation evidence under
`print_orientation` and `manufacturing.transform`.
In `co_print_body` mode, the 3MF stores one top-level mesh build item with
per-triangle colors and region metadata. In `separate_parts` mode, each region
may remain a top-level build item, but only when the intent says those are real
separately printed parts.
It requires `parent=` and writes hidden internal print-pose region meshes for
3MF packing plus hidden semantic-pose region meshes for colored visual review;
neither set is a user deliverable. The STL is the coverage-checked parent
without internal material-interface faces. It also emits `NAME_material-plan.json`
as region metadata because 3MF RGB values do not prove optical behavior.
For the STL fallback, preserve any required single-material-visible engraving,
recess, relief, or raised texture in the parent geometry; do not rely on a
filled color insert to carry a feature that must remain visible after region
colors are discarded.

Expose every meaningful user-adjustable driving dimension with `parameter()`:
overall dimensions plus local feature, interface, inset, clearance, and region
boundary dimensions. Give each one a stable ID, conservative topology-safe
bounds, a positive step, unit, label, group, and the feature or region IDs it
affects. Add concise `label_zh` and `group_zh` translations while keeping IDs and
Python variable names stable in English. Localized fields are presentation
metadata only. Derived coordinates and palette values are not independent
slider parameters. Every override must rebuild the entire region set and print
3MF; never publish a parameter that is unused by the full model
construction.

## 4. Audit meshes, archive, and appearance

Audit hidden internal region meshes only when debugging color-region topology.
Do not present those meshes as printable parts, and do not run overhang checks
on an isolated co-printed region because adjacent materials may provide
support:

```bash
python "<SKILL_DIR>/qa_check.py" ".amagine3d-internal/<name>/<name>-region-<region>.stl" --topology-only --region <region> --components <N> --out "<name>-region-<region>_mesh-audit.json"
```

Run a lightweight static print-package QA on the 3MF. This checks package
provenance, package mode, region names/colors, units, top-level build item,
dimensions, Z0, and bed fit:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>.3mf" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --tol <T> --require-z0 --out "<name>_package-audit.json"
```

Then run full Bambu manufacturing mesh checks exactly once on the clean whole
body STL:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components <N> --tol <T> --require-z0 --out "<name>_mesh-audit.json"
```

Read every `fail`, `warning`, and `not_evaluated` result. A region topology
pass cannot replace the manufacturing bed-fit, wall, feature, or overhang
evidence.
Treat printability advisory checks as coarse process-risk guardrails, not as a
goal to make every warning disappear. Package validity, parent coverage,
region/color integrity, contract dimensions, and visual/semantic fidelity are
higher-priority success criteria than warning-free support or overhang reports.
If a region is marked `continuity: "continuous-core"`, QA must fail when the
build report shows that region split across multiple solids; repair by keeping
the core region continuous and moving color detail into a shell, shallow inset,
raised overlay, or shallow filled groove.
Only repair a printability advisory by changing source geometry when it points
to a broad process blocker, such as impossible bed fit, impossible height,
globally undersized walls, critical features below the line-width floor, or
support burden so large that the print process is likely to fail. Local
overhangs, localized support needs, and cosmetic-print risks should normally be
reported as `supports-required` or `static print-package QA passed with
warnings` instead of flattening, thickening, moving, converting to full-depth
color columns, or simplifying identity-bearing geometry.

Then verify that 3MF names and colors match the build report:

```bash
python "<SKILL_DIR>/assembly_check.py" "<name>_report.json" "<name>.3mf" --out "<name>_assembly-audit.json"
```

Then verify the STEP assembly master with OCCT:

```bash
python "<SKILL_DIR>/step_check.py" "<name>-assemble.step" --intent "<name>_intent.json" --report "<name>_report.json" --out "<name>_assemble-audit.json"
```

`qa_check.py` and `step_check.py` read contract dimensions from `--intent`
when explicit `--expect-x/y/z` values are omitted. `step_check.py` also reads
region solid counts from `--report` when available; pass explicit expected
values only to override the evidence.

Render all regions with contract colors in semantic object orientation,
producing five views and the matched view. Use the hidden semantic region meshes
as renderer inputs. Use print-pose meshes and `NAME.3mf` only for package and
manufacturing checks, not for judging whether the model was built well:

```bash
python "<SKILL_DIR>/render_preview.py" --part ".amagine3d-internal/<name>/semantic/<name>-region-<region-a>.stl=#RRGGBB" --part ".amagine3d-internal/<name>/semantic/<name>-region-<region-b>.stl=#RRGGBB" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

Use `read` on both. Judge geometry landmarks, silhouette/depth, region
placement, boundary thickness, palette, and unexpectedly plain underside—not
merely whether colors exist. For `full-3d` replicas, the bottom view must be
reviewed when the object's underside contributes to identity or volume; a flat
bottom created for print convenience is a visual-fidelity failure.
For open-ended recognizable objects, keep `visual.landmarks` as a compact
quality rubric, usually 3-7 identity-critical landmarks. Prefer major
silhouette, proportion, material/color-region, and one or two signature details
over an exhaustive checklist of every small decoration. Optional micro-details
may be reported as compromises instead of blocking delivery.
Use silhouette scoring only for a corresponding orthographic/flat source.

## 5. Repair and close

Repair the failed evidence class: parent geometry, region boundary, palette
mapping, mesh topology, bed fit, feature size, broad wall-thickness failure,
excessive support burden, archive assignment, or visual placement. Bed-fit and
excessive-height failures should first be repaired by a different whole-package
orientation when a candidate exists. If a lower-support candidate only misses
the profile before scaling, prefer the recorded uniform print scale over a
worse-support pose unless the user's task requires fixed final dimensions.
Overlap, coverage, or visual failures repair the semantic source model. Feature
and wall repairs are for
critical or broad process failures, not isolated cosmetic advisory risk.
Overhang repairs are required only when support-free output was explicitly
promised or the support burden is likely to make the print process fail;
otherwise preserve the semantic shape and declare supports required. Never
lower the profile limits or scale fixed user dimensions to clear QA. Never
chase warning-free QA by changing object identity, expected part relationships,
meaningful proportions, appearance landmarks, or semantic color boundaries.
Every change requires rebuild, all affected internal region topology checks
when used, package audit, mesh audit, 3MF assembly audit, STEP assembly audit,
render, and read. Maximum three evidence-repair passes; disclose remaining
failures at the cap.

Freshness must cover the printer profile, intent, palette plan when used,
source, STL, assembly STEP, display GLB, 3MF, material plan, build report, 3MF
package audit, mesh audit, 3MF assembly audit, STEP assembly audit, visual
previews, and the render evidence report.

Deliver the complete evidence bundle. Report geometry, region integrity, 3MF
package QA, 3MF color readback, STEP master validity, optical-region metadata,
freshness, visual fidelity, palette fidelity, bed fit, feature resolution,
walls, and overhangs as separate statuses. Summarize the result as
`static print-package QA passed`, `static print-package QA passed with
warnings`, or `static print-package QA failed`. Report `actual slicer
validation: intentionally out of scope / not planned`; never list it as a
pending issue, and never call static package or mesh QA definitive proof that a
real slicer accepted the file.
