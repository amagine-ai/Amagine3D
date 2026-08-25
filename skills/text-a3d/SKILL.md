---
name: text-a3d
description: >
  Evidence-driven single-color CAD synthesis and reconstruction. Creates fresh
  STEP/STL artifacts from specifications, drawings, or reference images using
  an independent intent contract, fail-closed build operations, provenance
  hashes, mesh audit, and mandatory matched-view review when appearance
  matters. Use only for single-material output or incidental photographic
  colors. If object-owned colors distinguish screens, controls, text/logos,
  materials, inlays, or identity, text-a3d-color takes priority.
---

# Evidence-driven single-color CAD

The deliverable is not merely a watertight mesh. It is a model whose source,
assumptions, measurable targets, visual evidence, and current-run artifacts
agree.

`<SKILL_DIR>` means this directory. Outputs belong directly in the current
session working directory.

## Resources

- `intent_contract.py` validates independent targets before geometry
- `examples/intent.example.json` is a copyable valid contract
- `reference_analyze.py` extracts image hash, bounds, palette, and pixel cells
- `cad_helpers.py` provides fail-closed operations and provenance-rich export
- `qa_check.py` audits topology, finite geometry, degeneracy, dimensions, and Z0
- `freshness_check.py` proves every deliverable belongs to this run
- `render_preview.py` emits orthographic views plus a hash-bound render report
- `compare_silhouette.py` scores comparable orthographic silhouettes
- Read `references/evidence-contract.md` whenever evidence or appearance matters
- Read `references/construction-strategies.md` before writing geometry

Requires build123d, trimesh, matplotlib, Pillow, and NumPy.

## 0. Route before modeling

Do not use this skill when meaningful colors belong to the object. A screen,
control, logo/text, material boundary, inlay, or identity palette routes to
`text-a3d-color`, even without the words 3MF or AMS. Lighting, reflections,
background, and photo variation are incidental. An explicit single-color
request overrides this preference.

Classify the job as specification, reference reproduction, reference inspired,
recognizable form, or inspect-only. Inspect-only never claims generation.

## 1. Open a traceable run

Choose a filename-safe name and create the marker before writing any contract or
source:

```bash
python "<SKILL_DIR>/freshness_check.py" --mark ".<name>.generation-start"
```

For image evidence, run:

```bash
python "<SKILL_DIR>/reference_analyze.py" "/absolute/reference.png" --out "<name>_reference.json"
```

Write `<name>_intent.json` using
`references/evidence-contract.md`, then validate it:

```bash
python "<SKILL_DIR>/intent_contract.py" "<name>_intent.json"
```

The contract must expose inferred dimensions and hidden-side assumptions. Do
not weaken it later to match the output.

## 2. Choose construction from evidence

Read `references/construction-strategies.md`. Pick full 3D, orthographic solid,
relief, or surface-led construction deliberately. Establish axes and a feature
dependency graph before code. Pixel/icon inputs use analyzer cells; never
hand-copy their coordinates.

## 3. Build with observable operations

Write the complete `<name>.py` in this run. Use parameters tied to
contract feature IDs. New sources use this runtime shape:

```python
import sys
sys.path.insert(0, r"<SKILL_DIR>")
from build123d import *
from cad_helpers import parameter, observe, checked_cut, checked_fillet, export_part

NAME = "<name>"
INTENT = "<name>_intent.json"
WIDTH = parameter(
    "overall-width", 40.0,
    min_value=24.0, max_value=80.0, step=0.5,
    unit="mm", label="Overall width", label_zh="总体宽度",
    group="Envelope", group_zh="外形尺寸",
    affects=("primary-envelope",),
)

# primary envelope -> identity volumes -> cuts -> controls -> finishes
body = ...
observe(body, "primary-envelope", "envelope")
body = checked_cut(body, ..., "screen-recess")
body = checked_fillet(
    body, lambda current: ..., 2.0, "outer-softening",
    allow_reduce=False,
)

if __name__ == "__main__":
    export_part(body, NAME, intent_path=INTENT)
```

Checked operations raise instead of silently returning unchanged geometry.
Finishing degradation is forbidden unless the contract permits it; if allowed,
the actual size appears in the build report.

Expose every meaningful user-adjustable driving dimension with `parameter()`:
overall dimensions, local feature sizes and positions, clearances, wall
thicknesses, hole diameters, and finish sizes when applicable. Give each one a
stable ID, conservative topology-safe bounds, a positive step, unit, label,
group, concise `label_zh` and `group_zh` translations, and the contract feature
IDs it affects. Localized fields are presentation metadata only: keep IDs and
Python variable names stable in English. Derived coordinates remain ordinary
expressions and must not be exposed as independent controls. A parameter change
rebuilds and republishes the complete model, so never declare an output-only or
unused value.

## 4. Prove geometry, then appearance

Execute the source and save the mesh audit:

```bash
python "<name>.py"
python "<SKILL_DIR>/qa_check.py" "<name>.stl" --expect-x <X> --expect-y <Y> --expect-z <Z> --tol <T> --require-z0 --out "<name>_mesh-audit.json"
```

Cross-check contract features against the build report's observed features and
operation ledger. Mesh success does not prove semantic correctness.

Visual review is mandatory for reference reproduction, recognizable form, or
any appearance requirement. Render after mesh success:

```bash
python "<SKILL_DIR>/render_preview.py" "<name>.stl" --out "<name>_views.png" --reference-view <front|side|top|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

Use `read` on the new four-view PNG and matched-view PNG. Compare every
contract landmark, silhouette, ratio, negative space, and unintended depth.
For a truly corresponding orthographic/flat reference, also run
`compare_silhouette.py` and read its overlay.

## 5. Repair by failed evidence class

- dimensional failure: change the responsible parameter
- missing/extra landmark: change the feature graph
- silhouette failure: change envelope/profile, not tiny details
- depth/view failure: change representation or secondary volumes
- mesh failure: repair topology without relaxing the contract

After any source/build change, rerun execution, mesh audit, render, and reads.
Maximum three evidence-repair passes. At the limit, report a failed category
and the latest preview; never call it a match.

## 6. Freshness and delivery

The freshness gate includes contract, source, model, reports, and required
previews:

```bash
python "<SKILL_DIR>/freshness_check.py" --after ".<name>.generation-start" "<name>_intent.json" "<name>.py" "<name>.step" "<name>.stl" "<name>_report.json" "<name>_mesh-audit.json" "<name>_views.png" "<name>_reference-view.png" "<name>_render.json"
```

For jobs whose contract sets `visual.required` to false, omit the last three
visual artifacts.

Deliver STEP, STL, parametric source, intent contract, build report, mesh audit,
and previews. Report specification, topology, freshness, visual fidelity, and
printability as separate statuses.
