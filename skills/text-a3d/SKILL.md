---
name: text-a3d
description: >
  Unified evidence-driven printable 3D modeling for single-material parts,
  multipart assemblies, and color-aware 3MF output. Selects an internal
  single-material or color manufacturing mode from the object's manufactured color
  requirements, then creates fresh STEP/STL/3MF/display artifacts from
  specifications, drawings, or reference images with independent intent,
  fail-closed operations, provenance, pinned printer profiles, manufacturing
  audits, and matched-view review when appearance matters.
---

# Evidence-driven printable 3D modeling

The deliverable is not merely a watertight mesh. It is a model whose source,
assumptions, measurable targets, visual evidence, manufacturing mode, and
current-run artifacts agree. This single discoverable skill owns both
single-material and color-aware generation.

`<SKILL_DIR>` means this directory. Resolve it to an absolute path before
running commands from a nested output directory. Outputs belong directly in the
current session working directory.

## Resources

- `intent_contract.py` validates independent targets before geometry
- `bambu_profile.py` resolves pinned Bambu machine, nozzle, process, and tool limits
- `examples/intent.example.json` is a copyable valid contract
- `reference_analyze.py` extracts image hash, bounds, palette, and pixel cells
- `cad_helpers.py` provides fail-closed operations and provenance-rich export
- `interface_recipes.py` derives paired printable connectors from one parameter set
- `plate_layout.py` packs multipart BRep bounds on the bound printer using translations only
- `scene_contract.py` validates the mutable canonical geometry graph separately from intent
- `hybrid_compile.py` compiles mesh-master nodes and BRep-derived mesh nodes into physical parts
- `shape_consistency.py` proves final physical GLB nodes and manufacturing STLs have not drifted
- `qa_check.py` audits geometry, Bambu bed fit, walls, features, and overhangs
- `assembly_check.py` audits same-material multipart report integrity
- `step_check.py` audits STEP assembly masters with OCCT
- `freshness_check.py` proves every deliverable belongs to this run
- `render_preview.py` emits orthographic views plus a hash-bound render report
- `compare_silhouette.py` scores comparable orthographic silhouettes
- `color/MODE.md` is the internal color-aware 3MF and region-manufacturing mode
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

## 0. Select the internal manufacturing mode

Use `single-material` mode for an explicit single-color request or when visible
color differences come only from lighting, reflections, background, or photo
variation. Continue with the single-material workflow in this document.

Use `color` mode when permanent color on manufactured parts or regions
distinguishes a control, logo/text, material boundary, inlay, printable bezel,
functional region, or identity palette, even without the words 3MF or AMS.
Read `color/MODE.md` completely and use its color-region workflow. A real
LED/LCD surface, glass appearance, or transient screen content that is excluded
from manufacturing does not select color mode by itself. Its runtime, examples,
and references are colocated under `color/`; it is an internal mode, not a
separately discoverable skill.

Both modes share the same task interpretation, semantic coordinate frame,
evidence priorities, and visual-fidelity obligations. Mode selection chooses
the manufacturing/export implementation; it does not introduce a user approval
gate or prevent the agent from iterating autonomously.

Classify the job as specification, reference reproduction, reference inspired,
recognizable form, or inspect-only. Inspect-only never claims generation.

The remaining sections describe the `single-material` mode. In `color` mode,
continue in `color/MODE.md` after applying the shared interpretation above.

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

Before choosing inferred overall dimensions, read
`derived.rotation_safe_envelope` and copy its explicit spatial-diagonal
constraint into the intent's primary-envelope acceptance. This makes the first
parameter set rotation-safe instead of relying on a later fit repair.

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
IDs, support policy, replica-fidelity limits, and manufacturing mode. Default to
one printable manufacturing body when it can preserve the requested object,
printable feature sizes, strength, and appearance. When the user did not fix a
physical size, choose the semantic dimensions before construction so the
envelope's spatial bounding-box diagonal is no greater than the smallest usable
build extent. This is a simple positive guarantee that every rigid rotation
remains bed-safe; write the chosen dimensions into the intent and build at unit
scale. If a fixed-size object cannot meet that guarantee, preserve its fixed
dimensions and record the allowed orientations instead. Do not split solely
because the first inferred envelope was too large; revise its driving dimensions
before construction. Use `multipart` only when separate printed
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

### Appearance-first and hybrid construction

For a freeform enclosure, character-like product, or other appearance-sensitive
object, choose the representation master from the geometry that carries its
identity. This is an autonomous iteration method, not a staged approval
workflow. When the identity depends on a contour that would collapse into a
generic BRep primitive, author a watertight, unit-scale mesh-master exterior from
explicit cross-sections/freeform profiles. Generate the inner-cavity cutter,
ports, pockets, and paired interface solids precisely in build123d, tessellate
those tools at unit scale, and combine them through the semantic scene. For a
primarily mechanical enclosure, keep the whole part BRep-master and use Three.js
only to inspect and shade the tessellated physical result.

Before selecting those profiles, turn the reference into explicit front, side,
and top silhouette landmarks: widths/depths at named heights, shoulder and base
transitions, local bulges, asymmetries, and insert-to-shell ratios. A sphere,
capsule, rounded box, or short ellipse-loft sequence is a blockout, not a final
reference-sensitive shell, unless its section coordinates come from those
landmarks and its matched silhouettes pass. Prefer a mesh master when cheeks,
feet, ears, shoulders, or other local form cannot be expressed by shared BRep
sections without losing identity.

Keep two documents with different responsibilities:

- `<name>_intent.json` is the immutable target: references, dimensions,
  landmarks, assumptions, and manufacturing acceptance.
- `<name>_scene.json` is the mutable implementation: parts, construction
  recipes, boolean nodes, interface arithmetic, artifact bindings, and one
  geometry revision shared by every compiler.

Validate the scene after graph changes:

```bash
python "<SKILL_DIR>/scene_contract.py" "<name>_scene.json"
```

Parts, color regions, installed-component references, and display decoration
are orthogonal. A housing, base, printable bezel/lens, and button are physical
parts; a real LED/LCD module and its active image are normally non-manufactured
assembly references; ivory, black, and coral are color regions only when they
belong to printed geometry. An emitted facial expression is display decoration
unless the user requests it as printable relief/inlay. Never use a color-region
object or visual screen proxy as a substitute for a physical part tree.

Give each physical part one `representationMaster`:

- `brep` for STEP-first shells, bores, pockets, wall thickness, and fitted
  interfaces. Three.js edits the shared profiles and parameters, build123d
  rebuilds the physical BRep, and the final display mesh is tessellated from
  that physical result.
- `mesh` for genuinely freeform printable surfaces. The canonical mesh enters
  manufacturing booleans directly and produces STL/3MF; do not claim a clean
  parametric STEP for that part.

Every scene node has one role: `solid`, `cutter`, `separate`, or
`display-only`. Build an enclosure as outer volume minus an inner-cavity cutter.
Every separate printed insert needs a paired recipient feature: printable
panel/pocket, button/retained guide, lid/socket, or pin/bore. A visually adjacent
or floating solid is not an assembly connection.

Model a real screen as one shared datum and four related consequences, not as a
black printable slab:

- subtract a named front aperture through the shell so the display can be seen;
- subtract a rear module keepout/seat from the actual module envelope plus
  assembly clearance, while preserving a printable lip around the aperture;
- add physical retainers, bosses, clips, or a separately printable bezel/lens
  only when the design calls for them; and
- place the Three.js glass/content surface behind the opening as a
  `display-only` `displayComponent` node whose `physicalFeatureRef` names the
  aperture cutter.

Derive the aperture, keepout, retainer positions, and visual plane from the same
screen center, normal, visible area, and module envelope. Do not subtract the
visual plane itself, and do not use the whole module envelope as the visible
opening. A `displayComponent` appears in the final display GLB but is excluded
from STEP, STL, 3MF, physical-part/interface counts, and all manufacturing
booleans. If the user explicitly requests a printable dummy screen, make that a
normal physical part instead of silently changing this default.

Apply PBR materials, fabric response, lighting, and expressions after physical
compilation. `concept.glb` is diagnostic; publish one `NAME-display.glb` whose
physical nodes come from manufactured geometry and whose explicitly tagged
display-only nodes show non-manufactured installed components. Never substitute
an independently polished shell for the compiled physical surface.

For a scene with mesh inputs, compile the manufacturing graph directly:

```bash
python "<SKILL_DIR>/hybrid_compile.py" "<name>_scene.json" --output-dir "."
```

`recipe.parameters.sourceMesh` may point to a Three.js-authored watertight
physical solid, a build123d-tessellated solid/cutter, or a non-volume visual
mesh on a `display-only` node. All sources use the same millimetre coordinate
frame, revision, and `scale: 1`. The compiler performs declared physical
unions/subtractions and emits per-part STL, part-colored 3MF, and one PBR display
GLB. The display GLB contains the compiled physical nodes plus explicitly
excluded display-only component visuals; shape consistency selects only the
named physical nodes. The compiler deliberately does not invent a smooth STEP
for a mesh-master part; BRep-master parts keep their build123d STEP source.

Iterate appearance by changing the canonical profiles/parameters and advancing
the geometry revision. Recompile all affected representations from that
revision. Never hand-edit the final GLB or copy a visually improved Three.js
surface without updating its canonical recipe or mesh master.

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

# primary envelope -> observed identity volumes -> real openings -> controls -> finishes
body = ...
observe(body, "primary-envelope", "envelope")
screen_aperture_tool = ...
body = checked_cut(body, screen_aperture_tool, "screen-aperture")
screen_module_keepout_tool = ...
body = checked_cut(body, screen_module_keepout_tool, "screen-module-keepout")
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
uniform scale that would have been needed as diagnostic evidence. Do not scale
during export. If inferred dimensions somehow miss the profile despite the
rotation-safe initial envelope, update the intent and driving parameters, then
rebuild before rejecting a lower-support pose. It emits `NAME.stl` as the
printable single-part mesh in the selected print coordinates,
`NAME-display.glb` as the user-visible semantic display model, and
`NAME-assemble.step` as the OCCT-readable semantic physical master. The report
stores the semantic bounds plus `print_orientation` and `print.transform`
rotation/translation evidence.

For BRep-master multipart assemblies, build each manufacturing part as its own
valid solid and export the assembly. Omit `part_colors` for a same-material
build; when color follows whole physical-part boundaries, pass the complete
part-to-color map shown below. The same v4 intent must declare matching
`color_regions`; do not create a second color intent or packaging script.

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
    }, NAME, intent_path=INTENT, part_colors={
        "lower-shell": "#E8E4DC",
        "top-lid": "#171A1D",
    })
```

For multipart and hybrid display/manufacturing builds, use the paired recipes
in `interface_recipes.py` when the connection matches: `collar_socket()` for a
locating shell/base joint, `inset_pocket()` for a printable fitted panel, lens,
or bezel insert (not an active display module),
`retained_slider()` for a printable button/guide, `pin_socket()` for a basic
locating pin, and `hinge_pin()` for a removable pin plus a shared coaxial knuckle
bore. Each recipe returns one retained `male`, its clearance-derived
`female_cutter`, and evidence from the same parameter set. Apply one rigid
placement transform to the pair, observe the retained feature, and use the
matching checked cut on the receiving part. Do not maintain independent
male/female dimensions when a paired recipe applies.

Before building, map every manufactured part to at least one declared
`manufacturing.interfaces` connection. The intent validator checks this before
geometry exists; use `parts[].installation: "adhesive"` or `"loose"` only for
an explicit exception. A front push button should normally be a
`retained-slider` pair, not a decorative cylinder placed near the base.

Each exported assembly part must be one valid solid. `export_assembly()` emits
`NAME-PART.stl` for each part's print placement, `NAME.stl` for the full
print-bed layout, `NAME-assemble.step` for the physical assembly master, and
`NAME-display.glb` for display. With `part_colors`, it additionally emits a
plate-aligned separate-parts 3MF and material plan from those same BRep shapes;
the application treats that 3MF as the primary print artifact. The bound
printer profile drives a deterministic two-dimensional bbox layout before any
file is written; the top-level STL and 3MF reuse exactly those translated
parts. If the full set cannot occupy one plate at unit scale, revise the part
tree or deliberately plan multiple plates instead of serializing off-bed or
overlapping items. Do not join
separate requested lids/covers into the body merely to satisfy single-color
output. Pass `part_name=` to every observed feature and checked operation in a
multipart build; export fails when evidence is unowned or a part is unobserved.

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

For an appearance-first or hybrid scene, bind every manufactured part's named
physical node in the final assembly-display GLB and its semantic-pose STL to the
same revision with `scale: 1`, then run the independent drift check:

```bash
python "<SKILL_DIR>/shape_consistency.py" --manifest "<name>_scene.json" --output "<name>_shape-consistency.json"
```

Run this against semantic/assembly coordinates, not a packed print plate. A
rigid Y-up/Z-up transform is allowed and recorded; scale, reflection, stale
revision, dimension drift, or surface-distance drift fails. Concept-only lights
and cameras are absent from the delivered GLB. Explicit `display-only`
installed-component nodes may be present, but are omitted from the physical
node-name selections used for surface comparison. If the check fails, repair
the canonical graph and recompile rather than altering only the display or only
the STL.
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
- bed overflow: preserve fixed user dimensions; for inferred dimensions, revise
  the intent and all responsible driving parameters together, then rebuild at
  unit scale. Do not transform an already-generated body with uniform scaling
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

For an appearance-first or hybrid build, also include `<name>_scene.json`, all
canonical graph/Three.js sources, `<name>_shape-consistency.json`, and the final
assembly-display GLB. Do not list `concept.glb` as the delivered display model.

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
