---
name: text-a3d
description: >
  Evidence-driven single-color CAD synthesis and reconstruction for one-piece
  parts or same-material multipart assemblies. Creates fresh STEP/STL artifacts
  from specifications, drawings, or reference images using an independent
  intent contract, fail-closed build operations, provenance hashes, pinned
  Bambu printer/nozzle/process profiles, printability repair, mesh audit, and
  mandatory matched-view review when appearance matters. Use only for
  single-material output or incidental photographic
  colors. If object-owned colors distinguish screens, controls, text/logos,
  materials, inlays, or identity, text-a3d-color takes priority.
---

# Evidence-driven single-color CAD

The deliverable is not merely a watertight mesh. It is a model whose source,
assumptions, measurable targets, visual evidence, and current-run artifacts
agree.

`<SKILL_DIR>` means this directory. Resolve it to an absolute path before
running commands from a nested output directory. Outputs belong directly in the
current session working directory.

## Resources

- `intent_contract.py` validates independent targets before geometry
- `bambu_profile.py` resolves pinned Bambu machine, nozzle, process, and tool limits
- `examples/intent.example.json` is a copyable valid contract
- `reference_analyze.py` extracts image hash, bounds, palette, and pixel cells
- `cad_helpers.py` provides fail-closed operations and provenance-rich export
- `qa_check.py` audits geometry, Bambu bed fit, walls, features, and overhangs
- `assembly_check.py` audits same-material multipart report integrity
- `step_check.py` audits STEP assembly masters with OCCT
- `freshness_check.py` proves every deliverable belongs to this run
- `render_preview.py` emits orthographic views plus a hash-bound render report
- `compare_silhouette.py` scores comparable orthographic silhouettes
- Read `references/evidence-contract.md` whenever evidence or appearance matters
- Read `references/construction-strategies.md` before writing geometry
- Read `references/bambu-printability.md` before every generated printable part

Requires build123d, trimesh, Rtree, Pillow, and NumPy. Preview rendering uses the
headless, single-process CPU Z-buffer in `cpu_z_buffer.py`; it does not need
a GPU, display server, OpenGL, or Matplotlib.
The renderer defaults to a 640-pixel output, 1x supersampling, a 1280-pixel
internal view limit, and 500,000 input triangles. `--supersample 2`,
`--max-resolution`, and
`--max-triangles` may adjust those values within the built-in hard caps.
When running outside Amagine3D's managed session, initialize the repository
runtime and use its Python executable instead of an unrelated system Python.

## 0. Route before modeling

Do not use this skill when meaningful colors belong to the object. A screen,
control, logo/text, material boundary, inlay, or identity palette routes to
`text-a3d-color`, even without the words 3MF or AMS. Lighting, reflections,
background, and photo variation are incidental. An explicit single-color
request overrides this preference.

Classify the job as specification, reference reproduction, reference inspired,
recognizable form, or inspect-only. Inspect-only never claims generation.

When the user names a specific real, catalog, branded, or fictional object, the
named object sets the identity target. When adequate reference images,
drawings, scans, or reliable dimensions are supplied, use
`reference-reproduction` and preserve the identity-bearing form. When no
reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, generate a faithful-inspired object from broad known
landmarks, and clearly report that it is not an exact replica.

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

If the user supplied no image, skip `reference_analyze.py`, set
`reference_files` to `[]`, and record which identity and dimension targets are
inferred rather than evidenced. A no-reference run should be framed as
reference-inspired or recognizable-form, not exact reference reproduction.

Write `<name>_intent.json` using
`references/evidence-contract.md`, then validate it:

```bash
python "<SKILL_DIR>/intent_contract.py" "<name>_intent.json"
```

The contract must expose inferred dimensions, hidden-side assumptions, the
object coordinate system, feature kind/face/direction for functional openings,
profile path and hash, build orientation, minimum wall target, critical feature
IDs, support policy, replica-fidelity limits, and manufacturing mode. Default to one printable
manufacturing body when it can preserve the requested object, printable feature
sizes, strength, and appearance. Do not split solely because the first profile
is too small; when the user did not fix a physical size, scale the whole model
to fit before considering multipart. Use `multipart` only when separate printed
parts create a real manufacturing benefit such as cleaner support strategy,
better strength orientation, post-installed components, functional movement, or
separable covers/inserts inferred from the object. Do not weaken the contract
later to match the output.

Printability must not rewrite the object. A full-3D replica must model the
bottom, side, back, and underside forms that belong to the object. Do not make a
flat-backed prop, relief, or plain planar underside merely to avoid supports or
make Z0 contact. Solve print concerns through rigid orientation, permitted
multipart interfaces, or an honest `supports-required`/warning result.

## 2. Choose construction from evidence

Read `references/construction-strategies.md` and
`references/bambu-printability.md`. Pick full 3D, orthographic solid, relief,
or surface-led construction deliberately; never call a relief-like or
flat-backed build `full-3d`. Use the required object frame:
`+X` is user right, `+Y` is object back, `+Z` is object top; front is `Y-min`
and bottom is `Z-min`. Put ports, holes, and cutouts on named semantic faces.
A bottom opening is valid when the contract says it belongs on the bottom; an
accidental front/bottom edge cut is a design failure, not a Z0 rule failure.
Establish the semantic model, underside/back-side fidelity targets, wall
parameters, and feature dependency graph before code. Print orientation is a
post-modeling manufacturing decision; it may rotate the finished body but may
not change the source shape. Pixel/icon inputs use analyzer cells; never
hand-copy their coordinates.

## 3. Build with observable operations

Write the complete `<name>.py` in this run. Use parameters tied to contract
feature IDs. For one-piece builds, use this runtime shape:

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

`export_part()` chooses one lightweight rigid print orientation from the
semantic body before final export, using the same orientation evidence strategy
as the color skill. It evaluates the six bed-facing orientations: identity,
front/back side lays, left/right side lays, and a top-down 180-degree flip.
Profile fit is a hard gate; among fitting candidates, support burden and bed
contact quality outrank low print height, so a taller top-down pose may beat a
lower side-lay when it materially reduces supports. Every candidate records the
uniform scale needed to fit the selected profile. Do not silently scale during
export; when dimensions are inferred rather than fixed by the user, use that
scale as repair evidence, update the contract and driving parameters together,
then rebuild before rejecting a lower-support pose. It emits `NAME.stl` as the
printable single-part mesh in the selected print coordinates,
`NAME-display.glb` as the user-visible semantic display model, and
`NAME-assemble.step` as the OCCT-readable semantic physical master. The report
stores the semantic bounds plus `print_orientation` and `print.transform`
rotation/translation evidence.

For same-material multipart assemblies, build each manufacturing part as its
own valid solid and export the assembly:

```python
from cad_helpers import parameter, observe, checked_cut, checked_fillet, export_assembly

lower_shell = ...
top_lid = ...
observe(lower_shell, "lower-shell", "part", part_name="lower-shell")
observe(top_lid, "top-lid", "part", part_name="top-lid")

if __name__ == "__main__":
    export_assembly({
        "lower-shell": lower_shell,
        "top-lid": top_lid,
    }, NAME, intent_path=INTENT)
```

Each exported assembly part must be one valid solid. `export_assembly()` emits
`NAME-PART.stl` for each part's print placement, `NAME.stl` for the full
print-bed layout, `NAME-assemble.step` for the physical assembly master, and
`NAME-display.glb` for display. Do not join separate requested lids/covers
into the body merely to satisfy single-color output. Pass `part_name=` to every
observed feature and checked operation in a multipart build; export fails when
evidence is unowned or a part is unobserved.

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
python "<SKILL_DIR>/qa_check.py" "<name>.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --tol <T> --require-z0 --out "<name>_mesh-audit.json"
python "<SKILL_DIR>/step_check.py" "<name>-assemble.step" --intent "<name>_intent.json" --report "<name>_report.json" --tol <T> --out "<name>_assemble-audit.json"
```

For multipart assemblies, audit each part STL as an individual print placement,
audit `NAME.stl` as the print-bed layout, then run the assembly and STEP
checkers:

```bash
python "<SKILL_DIR>/qa_check.py" "<name>-lower-shell.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components 1 --require-z0 --out "<name>-lower-shell_mesh-audit.json"
python "<SKILL_DIR>/qa_check.py" "<name>-top-lid.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components 1 --require-z0 --out "<name>-top-lid_mesh-audit.json"
python "<SKILL_DIR>/qa_check.py" "<name>.stl" --profile "<name>_printer-profile.json" --intent "<name>_intent.json" --report "<name>_report.json" --components 2 --require-z0 --out "<name>_mesh-audit.json"
python "<SKILL_DIR>/assembly_check.py" "<name>_report.json" "<name>.stl" --out "<name>_assembly-audit.json"
python "<SKILL_DIR>/step_check.py" "<name>-assemble.step" --intent "<name>_intent.json" --report "<name>_report.json" --out "<name>_assemble-audit.json"
```

Cross-check contract features against the build report's observed features and
operation ledger. Read every `fail`, `warning`, and `not_evaluated` check plus
its structured `repair` object. Mesh success does not prove STEP assembly
correctness, STEP success does not prove printability, and GLB display success
does not prove CAD topology.
Treat printability advisory checks as coarse process-risk guardrails, not as a
goal to make every warning disappear. Geometry validity, contract dimensions,
critical feature evidence, and visual/semantic fidelity are higher-priority
success criteria than warning-free support or overhang reports. Only repair a
printability advisory by changing source geometry when it points to a broad
process blocker, such as impossible bed fit, impossible height, globally
undersized walls, critical features below the line-width floor, or support
burden so large that the print process is likely to fail. Local overhangs,
localized support needs, and cosmetic-print risks should normally be reported
as `supports-required` or `pass_with_warnings` instead of flattening,
thickening, moving, or simplifying identity-bearing geometry.
`qa_check.py` and `step_check.py` read contract dimensions from `--intent`
when explicit `--expect-x/y/z` values are omitted. `qa_check.py` skips that
automatic dimension check for multipart assembly reports because individual
parts and print-bed layouts can have different extents. `step_check.py` also
reads assembly solid counts from `--report` when available; pass explicit
expected values only to override the evidence.

Visual review is mandatory for reference reproduction, recognizable form, or
any appearance requirement. Render the semantic display model after mesh
success; use the print STL to judge manufacturing placement, not object
identity:

```bash
python "<SKILL_DIR>/render_preview.py" "<name>-display.glb" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

For multipart assemblies, render the display GLB rather than the print-bed
layout:

```bash
python "<SKILL_DIR>/render_preview.py" "<name>-display.glb" --out "<name>_views.png" --reference-view <front|side|top|bottom|isometric> --reference-out "<name>_reference-view.png" --report "<name>_render.json"
```

Use `read` on the new five-view PNG and matched-view PNG. Compare every
contract landmark, silhouette, ratio, negative space, unintended depth, and
unexpectedly plain underside. For `full-3d` replicas, the bottom view must be
reviewed when the object's underside contributes to identity or volume; a flat
bottom created for print convenience is a visual-fidelity failure.
For open-ended recognizable objects, keep `visual.landmarks` as a compact
quality rubric, usually 3-7 identity-critical landmarks. Prefer major
silhouette, proportion, material/region boundaries represented as geometry, and
one or two signature details over an exhaustive checklist of every small
decoration. Optional micro-details may be reported as compromises instead of
blocking delivery.
For a truly corresponding orthographic/flat reference, also run
`compare_silhouette.py` and read its overlay.

## 5. Repair by failed evidence class

- dimensional failure: change the responsible parameter
- missing/extra landmark: change the feature graph
- silhouette failure: change envelope/profile, not tiny details
- depth/view failure: change representation or secondary volumes
- mesh failure: repair topology without relaxing the contract
- bed overflow: try reported XY rotation or orientation; if a lower-support
  candidate only misses the profile because inferred dimensions are too large,
  uniformly scale the semantic design and intent before falling back to a
  worse-support fitting pose; preserve fixed user dimensions and profile
- feature resolution: widen the named feature parameter to the profile floor
- thin wall: repair only when the affected area is broad, structural, or
  critical; report localized cosmetic thin-wall risk without distorting the
  object
- overhang/support burden: repair only when support demand is excessive for the
  print process or the user required support-free output; otherwise preserve
  the semantic shape and declare supports required
- not evaluated: restore the missing evidence; never call it a pass

Never lower a profile limit, enable slicer compensation as the only repair, or
scale user dimensions to make QA pass. Never chase warning-free QA by changing
object identity, expected part relationships, meaningful proportions, or
appearance landmarks. After any source/build change, rerun execution, mesh
audit, render, and reads. Maximum three evidence-repair passes. At the limit,
report `pass_with_warnings` or the failed category honestly.

## 6. Freshness and delivery

The freshness gate includes contract, source, model, reports, and required
previews:

```bash
python "<SKILL_DIR>/freshness_check.py" --after ".<name>.generation-start" "<name>_printer-profile.json" "<name>_intent.json" "<name>.py" "<name>-assemble.step" "<name>-display.glb" "<name>.stl" "<name>_report.json" "<name>_mesh-audit.json" "<name>_assemble-audit.json" "<name>_views.png" "<name>_reference-view.png" "<name>_render.json"
```

For multipart assemblies, include every `NAME-PART.stl`, every part mesh audit,
`NAME.stl`, `NAME_mesh-audit.json`, and `NAME_assembly-audit.json`.

For jobs whose contract sets `visual.required` to false, omit the last three
visual artifacts.

Deliver the resolved profile, STEP, STL(s), parametric source, intent contract,
build report, mesh audit(s), assembly audit when present, and previews. Report
specification, manufacturing structure, topology, freshness, visual fidelity,
bed fit, feature resolution, wall thickness, and overhang/support need as
separate statuses. Summarize the print result as `print preflight passed`,
`print preflight passed with warnings`, or `print preflight failed`. Report
`actual slicer validation: intentionally out of scope / not planned`; never
list it as a pending issue, and never call a profile-backed mesh audit
definitive proof that the part is printable.
