---
name: text-a3d-color
description: >
  Evidence-driven multi-color CAD and manufacturing-region synthesis. Creates
  per-region STLs, a colored 3MF, and STEP assembly from explicit color
  semantics, deterministic palette reduction, strict overlap/coverage checks,
  archive color readback, provenance hashes, and mandatory colored-view review.
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
- `intent_contract.py` validates color semantics and boundaries before geometry
- `examples/intent.example.json` is a copyable valid color contract
- `cad_helpers.py` validates regions, optional parent coverage, and provenance
- `export_3mf.py` writes a shared palette and reads object colors from 3MF XML
- `qa_check.py` audits each exported region mesh
- `assembly_check.py` cross-checks build regions against the stored 3MF colors
- `render_preview.py` creates orthographic, hash-bound color evidence
- `freshness_check.py` and `compare_silhouette.py` close the run
- Read `references/evidence-contract.md` for every generated color task
- Read `references/color-architecture.md` before choosing region interfaces

Requires build123d, lib3mf, trimesh, matplotlib, Pillow, and NumPy.

## 0. Route and interpret color

Use this skill when object-owned color affects identity or separates a screen,
control, logo/text, material, inlay, or functional region. Do not route here
for lighting, shadow, reflection, background, or photo noise alone. An explicit
single-color request routes to `text-a3d`.

Distinguish permanent printed color from transient display content. A physical
LED/LCD is normally one screen region; model individual lit pixels only for a
requested static decorative face or mosaic.

## 1. Open the evidence run

Create a marker before new files:

```bash
python "<SKILL_DIR>/freshness_check.py" --mark ".<name>.generation-start"
python "<SKILL_DIR>/reference_analyze.py" "/absolute/reference.png" --out "<name>_reference.json"
```

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

## 2. Design region architecture

Read `references/color-architecture.md`. Choose parent split, inset, raised
overlay, or separately assembled insert for every boundary. Build the complete
parent form first when regions collectively represent one body; this enables
coverage checking and prevents invented gaps.

The color contract defines region name, hex, purpose, geometric boundary, and
evidence. Do not collapse distinct semantic regions merely to fit an arbitrary
palette limit; record every compromise.

## 3. Build and export strict regions

Write the complete parametric source in this run. Use stable region and feature
IDs:

```python
import sys
sys.path.insert(0, r"<SKILL_DIR>")
from build123d import *
from cad_helpers import observe, checked_cut, export_regions

NAME = "<name>"
INTENT = "<name>_intent.json"
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

## 4. Audit meshes, archive, and appearance

Audit every region STL using its expected component count and dimensions:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>-<region>.stl" --region <region> --components <N> --out "<name>-<region>_mesh-audit.json"
```

Then verify that 3MF names and colors match the build report:

```bash
python "<SKILL_DIR>/assembly_check.py" "<name>_report.json" "<name>.3mf" --out "<name>_assembly-audit.json"
```

Render all regions with contract colors, producing four views and the matched
view:

```bash
python "<SKILL_DIR>/render_preview.py" --part "<name>-<region-a>.stl=#RRGGBB" --part "<name>-<region-b>.stl=#RRGGBB" --out "<name>_views.png" --reference-view <front|side|top|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

Use `read` on both. Judge geometry landmarks, silhouette/depth, region
placement, boundary thickness, and palette—not merely whether colors exist.
Use silhouette scoring only for a corresponding orthographic/flat source.

## 5. Repair and close

Repair the failed evidence class: parent geometry, region boundary, palette
mapping, mesh topology, archive assignment, or visual placement. Every change
requires rebuild, all affected region audits, assembly audit, render, and read.
Maximum three evidence-repair passes; disclose remaining failures at the cap.

Freshness must cover intent, palette plan when used, source, every region STL,
STEP, 3MF, build report, mesh audits, assembly audit, visual previews, and the
render evidence report.

Deliver the complete evidence bundle. Report geometry, region integrity, 3MF
readback, freshness, visual fidelity, palette fidelity, and printability as
separate statuses. For Bambu Studio, prefer the colored 3MF; region STLs remain
the fallback for manual filament assignment.
