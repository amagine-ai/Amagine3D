---
name: text-a3d-color
description: >
  Evidence-driven multi-color CAD and manufacturing-region synthesis. Creates
  per-region STLs, a colored 3MF, and STEP assembly from explicit color
  semantics, deterministic palette reduction, strict overlap/coverage checks,
  archive color readback, pinned Bambu printability evidence, provenance
  hashes, and mandatory colored-view review.
  Takes priority when reference colors identify screens, controls, text/logos,
  materials, inlays, functional regions, or the object's recognizable palette,
  even if the user does not mention multi-color, 3MF, or AMS.
---

# Evidence-driven color CAD

This skill treats color as manufactured geometry with semantic purpose. A
valid 3MF object count is insufficient: region topology, stored palette,
appearance, source evidence, and current-run provenance must agree.

`<SKILL_DIR>` means this directory. Outputs belong directly in the current
session working directory.

## Resources

- `reference_analyze.py` extracts objective image/pixel/palette evidence
- `palette_plan.py` reduces source colors into a deterministic filament plan
- `bambu_profile.py` resolves this skill's pinned Bambu machine/process limits
- `intent_contract.py` validates color semantics and boundaries before geometry
- `examples/intent.example.json` is a copyable valid color contract
- `cad_helpers.py` validates regions, optional parent coverage, and provenance
- `export_3mf.py` writes a shared palette and reads object colors from 3MF XML
- `qa_check.py` audits region topology and the combined manufacturing mesh
- `assembly_check.py` cross-checks build regions against the stored 3MF colors
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

Distinguish permanent printed color from transient display content. A physical
LED/LCD is normally one screen region; model individual lit pixels only for a
requested static decorative face or mosaic.

RGB stored in a 3MF does not prove optical behavior. Record every region as
`opaque`, `translucent`, or `transparent` in the intent. Non-opaque regions
also require the generated material plan and explicit slicer filament mapping.

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

When source colors exceed available filaments, create a proposed plan:

```bash
python "<SKILL_DIR>/palette_plan.py" "<name>_reference.json" --max-colors <N> [--keep "#RRGGBB"] --out "<name>_palette.json"
```

Write `<name>_intent.json` from
`references/evidence-contract.md`. Semantic regions may override automated
frequency: rare logo/control colors are not disposable. Validate:

```bash
python "<SKILL_DIR>/intent_contract.py" "<name>_intent.json"
```

The contract must bind the profile hash, build orientation, support policy,
wall target, functional acceptance criteria, critical feature IDs, and each
region's optical transmission. Critical IDs must later resolve to named build
evidence; for routed cavities, observe a representative local cross-section.

## 2. Design region architecture

Read `references/color-architecture.md` and
`references/bambu-printability.md`. Choose parent split, inset, raised overlay,
or separately assembled insert for every boundary. Build the complete parent
form first when regions collectively represent one co-printed body; this
enables coverage checking, a combined manufacturing STL, and support analysis
without false positives at material interfaces.

The color contract defines region name, hex, optical material, purpose,
geometric boundary, and evidence. Use the profile's line-width and wall targets
for every boundary. Do not collapse distinct semantic regions merely to fit an
arbitrary palette limit; record every compromise.

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

For separately assembled inserts whose union intentionally differs from one
parent, omit `parent=` only after recording that architecture in the contract.
`export_regions()` always emits `<name>-combined.stl`; when `parent=` is
provided the combined file is the coverage-checked parent without internal
material-interface faces. It also emits `<name>_material-plan.json` because 3MF
RGB values cannot encode translucency or filament chemistry.

Expose every meaningful user-adjustable driving dimension with `parameter()`:
overall dimensions plus local feature, interface, inset, clearance, and region
boundary dimensions. Give each one a stable ID, conservative topology-safe
bounds, a positive step, unit, label, group, and the feature or region IDs it
affects. Add concise `label_zh` and `group_zh` translations while keeping IDs and
Python variable names stable in English. Localized fields are presentation
metadata only. Derived coordinates and palette values are not independent
slider parameters. Every override must rebuild the entire region set and
combined 3MF; never publish a parameter that is unused by the full model
construction.

## 4. Audit meshes, archive, and appearance

Audit every region STL for topology only. Do not run overhang checks on an
isolated co-printed region because adjacent materials may provide support:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>-<region>.stl" --topology-only --region <region> --components <N> --out "<name>-<region>_mesh-audit.json"
```

Run Bambu manufacturing checks exactly once on the combined STL:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>-combined.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components <N> --expect-x <X> --expect-y <Y> --expect-z <Z> --tol <T> --require-z0 --out "<name>-combined_mesh-audit.json"
```

Read every `fail`, `warning`, and `not_evaluated` result. A region topology
pass cannot replace the combined bed-fit, wall, feature, or overhang evidence.

Then verify that 3MF names and colors match the build report:

```bash
python "<SKILL_DIR>/assembly_check.py" "<name>_report.json" "<name>.3mf" --out "<name>_assembly-audit.json"
```

Render all regions with contract colors, producing four views and the matched
view:

```bash
python "<SKILL_DIR>/render_preview.py" --part "<name>-<region-a>.stl=#RRGGBB" --part "<name>-<region-b>.stl=#RRGGBB" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

Use `read` on both. Judge geometry landmarks, silhouette/depth, region
placement, boundary thickness, and palette—not merely whether colors exist.
Use silhouette scoring only for a corresponding orthographic/flat source.

## 5. Repair and close

Repair the failed evidence class: parent geometry, region boundary, palette
mapping, mesh topology, bed fit, feature size, wall thickness, overhang,
archive assignment, or visual placement. Never lower the profile limits or
scale fixed user dimensions to clear QA. Every change requires rebuild, all
affected region audits, combined audit, assembly audit, render, and read.
Maximum three evidence-repair passes; disclose remaining failures at the cap.

Freshness must cover the printer profile, intent, palette plan when used,
source, every region STL, combined STL, STEP, 3MF, material plan, build report,
region mesh audits, combined manufacturing audit, assembly audit, visual
previews, and the render evidence report.

Deliver the complete evidence bundle. Report geometry, region integrity, 3MF
readback, material/transmission assignment, freshness, visual fidelity, palette
fidelity, bed fit, feature resolution, walls, and overhangs as separate
statuses. Summarize the combined result as `print preflight passed`, `print
preflight passed with warnings`, or `print preflight failed`. Report `actual
slicer validation: not evaluated` unless a real Bambu Studio or equivalent
slicer run was completed. For Bambu Studio, prefer the colored 3MF; region STLs
remain the fallback for manual filament assignment.
