# Color manufacturing mode

This is an internal mode of the discoverable `text-a3d` skill, not a separate
skill. Enter this mode when permanent colors on manufactured geometry identify
controls, text/logos, materials, inlays, printable bezels, functional regions,
or the object's recognizable palette, even if the user does not mention
multi-color, 3MF, or AMS. A non-manufactured display module or transient screen
content does not select this mode by itself.

`<COLOR_MODE_DIR>` means this document's directory. It contains this mode's
Python runtime, examples, and references. Resolve it to an absolute path before
running commands from a nested output directory.
`<SKILL_DIR>` means its parent `skills/text-a3d/` directory.

For generated Python that combines root BRep/hybrid helpers with color export,
add only the parent `<SKILL_DIR>` to `sys.path` and import color helpers through
their namespace, for example `from color.export_3mf import write_color_archive`
or `from color.cad_helpers import export_regions`. Never add both
`<SKILL_DIR>` and `<COLOR_MODE_DIR>` as competing top-level import roots; both
modes intentionally contain some same-named CLI modules.

This mode treats color as manufactured geometry with semantic purpose. A
valid 3MF object count is insufficient: region topology, stored palette,
appearance, source evidence, and current-run provenance must agree.

Outputs belong directly in the current session working directory.

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

Use this mode when permanent manufactured color affects identity or separates a
control, printable bezel, logo/text, material, inlay, or functional region. Do
not route here for lighting, shadow, reflection, background, photo noise, or an
excluded LED/LCD visual alone. An explicit single-color request selects the
unified skill's `single-material` mode.

When the user names a specific real, catalog, branded, or fictional object, the
named object sets the identity target. When adequate reference images,
drawings, scans, or reliable dimensions are supplied, use
`reference-reproduction` and preserve the identity-bearing form and color
regions. When no reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, generate a faithful-inspired object from broad known
landmarks, and clearly report that it is not an exact replica.

Distinguish permanent printed color from transient display content. A real
LED/LCD module is normally a non-manufactured `displayComponent`: its shell
aperture and module keepout are physical cutters, while its glass/content visual
appears only in the final display GLB. It is not a color region and never enters
STL/3MF. A printable bezel, opaque dummy screen, or requested static decorative
face/mosaic may be a manufactured color region.

RGB stored in a 3MF does not prove optical behavior. Record every region as
`opaque`, `translucent`, or `transparent` in the intent when optical behavior
changes the model. Do not invent real filament assignments; the user chooses
actual slicer materials.
Color regions are co-printed partitions, not printable assembly parts. If the
object needs real separately printed parts, design printable interfaces first;
do not turn `NAME-region-REGION.stl` into a user deliverable.
The default `printability.print_package_mode` is `co_print_body`: all color
regions belong to one printable body and must be packaged as one top-level 3MF
mesh build item with per-triangle color properties. Choose the colored export
path from the physical representation, not from a second color workflow:

- For a BRep-master multipart assembly whose colors exactly follow whole
  physical-part boundaries, call the root
  `export_assembly(..., part_colors={...})`. Keep one v4 intent, one assembly
  report, and one set of physical shapes for STEP, STL, 3MF, and display GLB.
- For multiple co-printed colors within one physical body, use
  `color.cad_helpers.export_regions()` and `co_print_body`.
- For a mesh-master part, mixed mesh/BRep graph, or multipart graph containing
  within-part regions, use the semantic scene and `hybrid_compile.py`.

Do not obtain a multipart assembly merely by switching color-region proxies to
`separate_parts`; define the physical parts and their paired interfaces first.

Map every printed part to a declared mating interface unless it is explicitly a
loose or adhesive-installed item. In particular, generate a button and its
retained guide from the same `retained_slider` parameters rather than placing an
unretained colored cap beside the housing.

## 1. Open the evidence run

Create a marker before new files:

```bash
python "<COLOR_MODE_DIR>/freshness_check.py" --mark ".<name>.generation-start"
python "<COLOR_MODE_DIR>/bambu_profile.py" --machine <machine-id> --nozzle <0.2|0.4|0.6|0.8> --tool <N> --out "<name>_printer-profile.json"
python "<COLOR_MODE_DIR>/reference_analyze.py" "/absolute/reference.png" --out "<name>_reference.json"
```

Honor a named user or project printer. Otherwise omit `--machine` to resolve
the conservative A1 mini default and record the assumption. Read the generated
profile before modeling; never change it later merely to clear QA.

Read `derived.rotation_safe_envelope` before choosing inferred dimensions and
copy its explicit spatial-diagonal constraint into the primary-envelope
acceptance.

When overall dimensions are inferred, choose them before geometry construction
so the semantic envelope's spatial bounding-box diagonal is no greater than the
smallest usable build extent. This keeps every arbitrary rigid rotation inside
the machine from the first build. Record those dimensions in the intent and
generate at `scale: 1`; if the user fixed a larger size, preserve it and record
only the orientations that fit.

If the user supplied no image, skip `reference_analyze.py`, set
`reference_files` to `[]`, and record which identity, dimension, and palette
targets are inferred rather than evidenced. A no-reference run should be framed
as reference-inspired or recognizable-form, not exact reference reproduction.

When source colors exceed available color channels, create a proposed plan:

```bash
python "<COLOR_MODE_DIR>/palette_plan.py" "<name>_reference.json" --max-colors <N> [--keep "#RRGGBB"] --out "<name>_palette.json"
```

Write `<name>_intent.json` from
`references/evidence-contract.md`. Semantic regions may override automated
frequency: rare logo/control colors are not disposable. Validate:

```bash
python "<COLOR_MODE_DIR>/intent_contract.py" "<name>_intent.json"
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

Before partitioning color, decide the physical part tree independently. For
example, `housing`, `speaker-base`, optional `printable-bezel`, and `button-1`
are printed parts; the screen module is a non-manufactured assembly reference;
`ivory`, `fabric-gray`, `black`, and `coral` are appearance assignments or
regions only on printed geometry. A single part may contain several co-printed
regions, and several parts may share one color. For appearance-first
Three.js/build123d work, follow
the parent skill's mutable semantic-scene workflow: compile booleans and paired
interfaces first, then put PBR materials on the resulting physical meshes.

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
from color.cad_helpers import parameter, observe, checked_cut, export_regions

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
    "printable-bezel": (printable_bezel, "#171A1D"),
}

if __name__ == "__main__":
    export_regions(regions, NAME, parent=parent, intent_path=INTENT)
```

`export_regions()` chooses one rigid print orientation from the semantic parent
shape before final export. It evaluates the six bed-facing orientations:
identity, front/back side lays, left/right side lays, and a top-down 180-degree
flip. Each candidate records the uniform scale that *would* be needed as repair
evidence, but export never applies it: a hidden scale would change walls,
clearances, interfaces, STEP/display agreement, and the requested object. Size
the canonical model for the printer while setting its driving dimensions; if no
rigid orientation fits, rebuild those dimensions or report the fit failure.
Support burden is estimated with a support-volume proxy, not only downward face
area. Support burden and bed contact quality outrank low print height among the
rigid candidates that actually fit.
It then emits `NAME.3mf` as the preferred multi-color print package and
`NAME.stl` as the clean whole-body manufacturing mesh in selected print
coordinates. `NAME-assemble.step` and `NAME-display.glb` preserve the semantic
object orientation for CAD review and visual fidelity. The report keeps the
original semantic bounds plus rigid rotation/translation evidence and explicit
unit scale under
`print_orientation` and `manufacturing.transform`.
In `co_print_body` mode, the 3MF stores one top-level mesh build item with
per-triangle colors and region metadata. `export_regions()` is for that one-body
case. For BRep-master colored multipart output whose color boundaries equal the
physical part boundaries, use root `export_assembly(part_colors=...)`; it emits
one top-level 3MF item per already-declared physical part. Use the hybrid
compiler when any part is mesh-master or carries within-part regions. In every
case, a top-level 3MF item is a physical part with an interface, not a color
proxy.
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
python "<COLOR_MODE_DIR>/qa_check.py" ".amagine3d-internal/<name>/<name>-region-<region>.stl" --topology-only --region <region> --components <N> --out "<name>-region-<region>_mesh-audit.json"
```

Run a lightweight static print-package QA on the 3MF. This checks package
provenance, package mode, region names/colors, units, top-level build item,
dimensions, Z0, and bed fit:

```bash
python "<COLOR_MODE_DIR>/qa_check.py" "<name>.3mf" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --tol <T> --require-z0 --out "<name>_package-audit.json"
```

Then run full Bambu manufacturing mesh checks exactly once on the clean whole
body STL:

```bash
python "<COLOR_MODE_DIR>/qa_check.py" "<name>.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components <N> --tol <T> --require-z0 --out "<name>_mesh-audit.json"
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
python "<COLOR_MODE_DIR>/assembly_check.py" "<name>_report.json" "<name>.3mf" --out "<name>_assembly-audit.json"
```

Then verify the STEP assembly master with OCCT:

```bash
python "<COLOR_MODE_DIR>/step_check.py" "<name>-assemble.step" --intent "<name>_intent.json" --report "<name>_report.json" --out "<name>_assemble-audit.json"
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
python "<COLOR_MODE_DIR>/render_preview.py" --part ".amagine3d-internal/<name>/semantic/<name>-region-<region-a>.stl=#RRGGBB" --part ".amagine3d-internal/<name>/semantic/<name>-region-<region-b>.stl=#RRGGBB" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
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
rigid orientation when a candidate exists. The recorded fit scale is diagnostic
only: change inferred driving dimensions in the canonical source and rebuild,
or report bed-fit failure. Never apply that scale during export.
Overlap, coverage, or visual failures repair the semantic source model. Feature
and wall repairs are for
critical or broad process failures, not isolated cosmetic advisory risk.
Overhang repairs are required only when support-free output was explicitly
promised or the support burden is likely to make the print process fail;
otherwise preserve the semantic shape and declare supports required. Never
lower the profile limits or silently scale any artifact to clear QA. Never
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
