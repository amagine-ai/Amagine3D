---
name: text-a3d
description: >
  Evidence-driven single-color CAD synthesis and reconstruction. Creates fresh
  STEP/STL artifacts from specifications, drawings, or reference images using
  an independent intent contract, fail-closed build operations, provenance
  hashes, pinned Bambu printer/nozzle/process profiles, printability repair,
  mesh audit, and mandatory matched-view review when appearance matters. Use
  only for single-material output or incidental photographic
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
- `bambu_profile.py` resolves pinned Bambu machine, nozzle, process, and tool limits
- `examples/intent.example.json` is a copyable valid contract
- `reference_analyze.py` extracts image hash, bounds, palette, and pixel cells
- `cad_helpers.py` provides fail-closed operations and provenance-rich export
- `qa_check.py` audits geometry, Bambu bed fit, walls, features, and overhangs
- `freshness_check.py` proves every deliverable belongs to this run
- `render_preview.py` emits orthographic views plus a hash-bound render report
- `compare_silhouette.py` scores comparable orthographic silhouettes
- Read `references/evidence-contract.md` whenever evidence or appearance matters
- Read `references/construction-strategies.md` before writing geometry
- Read `references/bambu-printability.md` before every generated printable part

Requires build123d, trimesh, Rtree, Pillow, and NumPy. Preview rendering uses the
headless, single-process CPU Z-buffer in `../cpu_z_buffer.py`; it does not need
a GPU, display server, OpenGL, or Matplotlib.
The renderer defaults to a 640-pixel output, 1x supersampling, a 1280-pixel
internal view limit, and 500,000 input triangles. `--supersample 2`,
`--max-resolution`, and
`--max-triangles` may adjust those values within the built-in hard caps.

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

Resolve one Bambu profile before the intent contract. Honor a named user or
project printer. Otherwise use the conservative A1 mini 0.4 mm default and
record that assumption. For dual-tool machines, select the actual tool:

```bash
python "<SKILL_DIR>/bambu_profile.py" --list
python "<SKILL_DIR>/bambu_profile.py" --machine <machine-id> --nozzle <0.2|0.4|0.6|0.8> --tool <N> --out "<name>_printer-profile.json"
```

Read the resolver output and the generated profile. Do not model until the
machine, tool, wall targets, and support threshold are known. Never switch the
profile later merely to clear QA.

For image evidence, run:

```bash
python "<SKILL_DIR>/reference_analyze.py" "/absolute/reference.png" --out "<name>_reference.json"
```

Write `<name>_intent.json` using
`references/evidence-contract.md`, then validate it:

```bash
python "<SKILL_DIR>/intent_contract.py" "<name>_intent.json"
```

The contract must expose inferred dimensions, hidden-side assumptions, the
profile path and hash, build orientation, minimum wall target, critical feature
IDs, and support policy. Do not weaken it later to match the output.

## 2. Choose construction from evidence

Read `references/construction-strategies.md` and
`references/bambu-printability.md`. Pick full 3D, orthographic solid, relief,
or surface-led construction deliberately. Establish axes, the print
orientation, wall parameters, and a feature dependency graph before code.
Pixel/icon inputs use analyzer cells; never hand-copy their coordinates.

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

# primary envelope -> observed identity volumes -> cuts -> controls -> finishes
body = ...
observe(body, "primary-envelope", "envelope")
screen = ...
observe(screen, "screen-frame", "additive")
body = body + screen
screen_tool = ...
body = checked_cut(body, screen_tool, "screen-recess")
body = checked_fillet(
    body, lambda current: ..., 2.0, "outer-softening",
    allow_reduce=False,
)

if __name__ == "__main__":
    export_part(body, NAME, intent_path=INTENT)
```

Observe every manufacturing-critical additive feature before union. Checked
cuts record tool bounds; checked finishes record actual size. These feature IDs
let QA tell the model which source parameter to repair. Finishing degradation
is forbidden unless the contract permits it.

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
python "<SKILL_DIR>/qa_check.py" "<name>.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --expect-x <X> --expect-y <Y> --expect-z <Z> --tol <T> --require-z0 --out "<name>_mesh-audit.json"
```

Cross-check contract features against the build report's observed features and
operation ledger. Read every `fail`, `warning`, and `not_evaluated` check plus
its structured `repair` object. Mesh success does not prove printability or
semantic correctness.

Visual review is mandatory for reference reproduction, recognizable form, or
any appearance requirement. Render after mesh success:

```bash
python "<SKILL_DIR>/render_preview.py" "<name>.stl" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
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
- bed overflow: try reported XY rotation or orientation; preserve fixed dimensions and profile
- feature resolution: widen the named feature parameter to the profile floor
- thin wall: thicken the responsible shell or reduce a decorative recess
- overhang: reorient, slope, chamfer, arch, or declare supports required
- not evaluated: restore the missing evidence; never call it a pass

Never lower a profile limit, enable slicer compensation as the only repair, or
scale user dimensions to make QA pass. After any source/build change, rerun
execution, mesh audit, render, and reads. Maximum three evidence-repair passes.
At the limit, report `pass_with_warnings` or the failed category honestly.

## 6. Freshness and delivery

The freshness gate includes contract, source, model, reports, and required
previews:

```bash
python "<SKILL_DIR>/freshness_check.py" --after ".<name>.generation-start" "<name>_printer-profile.json" "<name>_intent.json" "<name>.py" "<name>.step" "<name>.stl" "<name>_report.json" "<name>_mesh-audit.json" "<name>_views.png" "<name>_reference-view.png" "<name>_render.json"
```

For jobs whose contract sets `visual.required` to false, omit the last three
visual artifacts.

Deliver the resolved profile, STEP, STL, parametric source, intent contract,
build report, mesh audit, and previews. Report specification, topology,
freshness, visual fidelity, bed fit, feature resolution, wall thickness,
and overhang/support need as separate statuses. Summarize the combined result
as `print preflight passed`, `print preflight passed with warnings`, or `print
preflight failed`. Report `actual slicer validation: not evaluated` unless a
real Bambu Studio or equivalent slicer run was completed; never call a
profile-backed mesh audit definitive proof that the part is printable.
