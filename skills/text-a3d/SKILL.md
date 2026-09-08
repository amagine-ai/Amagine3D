---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Use editable source only from the current session workspace; never inspect
sibling sessions. Use public `a3d` commands and keep source beside artifacts.

Author manufactured geometry as build123d BRep solids and retain genuine STEP
masters. STL, GLB and 3MF meshes are derived export and review artifacts.

Develop the primary form from the requested product character and use. When the
request prioritizes exterior form, styling, or reference reproduction, read
`references/surface-design.md` for shaping volumes, choosing surface controls,
and integrating details. Describe the defining contours, surface transitions and
functional datums in the existing landmarks and acceptance fields. Choose the
construction that gives these relationships useful freedom to evolve, and carry
them through revisions to the BRep construction.
For appearance-led work, establish a reference direction using the image guidance
below and inspect a primary-form preview before detail makes proportion changes
expensive. Compare contours and relationships, not just the presence of parts.

## Author and build

Separate user requirements, functional necessities, and your proposed construction
in the existing evidence, acceptance, and assumptions fields. For a requested
functional device, derive the geometry needed for each function before writing
intent: relevant component envelopes and the internal space, exterior passages,
and assembly or installation paths those functions need. Resolve how installed
items are supported, located and retained in use, and provide closure or protection
where the intended use needs it. Choose those relationships from the function;
an installed item does not by itself require a lid, screws or a particular split.
Model these relationships and record them in feature acceptance. A functional opening serving an internal
component must form a continuous passage from the exterior into its target cavity
or keepout.

When component specifications are unknown, expose proposed envelopes and
controlling dimensions as reversible parameters while retaining the geometry
needed for the requested functions. Use a form-only scope with simple physical
seats when the user's request is limited to appearance; missing component
specifications or dimensions alone do not establish that scope.

Keep manufactured parts, purchased-component references and transient visual
content distinct. `NAME-display.glb` means assembly preview: it contains the
manufactured geometry plus any optional `display-only` references. Producing it
does not require a dummy component. Only intended manufactured pieces belong in
the export part list. Develop installation geometry independently of whether a
reference component is shown. For required installation relationships, declare
the applicable evidence using `references/installation-checks.md`.

When the main volume or component arrangement is still unresolved, use
`a3d draft <name>_draft.py` for a provisional preview before full feature
registration. Query `export_draft`; `examples/installed_module_draft.py` shows
parts and component envelopes. Reuse the chosen construction in the final build;
draft previews carry no installation, printability or delivery acceptance.

For the final model, create its marker before authoring intent and build source.
Use the user's or project's printer selection and reuse its valid profile when
available. The profile command below illustrates an A1 mini with a 0.4 mm nozzle;
when using it as a fallback, record that process assumption in intent.

Resolve the usable print volume before choosing part dimensions and detail.
For multipart work, use `a3d layout` on proposed part bounds in their print
orientations, with spacing, edge margin and allowed plate count; see
`references/bambu-printability.md`. Keep assembled size, individual part size
and plate occupancy distinct. A failed packing heuristic does not authorize
changing the printer or the target dimensions.

```bash
a3d mark --mark ".<name>.generation-start"
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 \
  --out "<name>_printer-profile.json"
```

Run `<name>_intent.py` once to write `<name>_intent.json`; validate it with
`a3d intent`. Keep contract creation separate from `<name>_build.py`, which owns
the scene and geometry. Expose controlling dimensions near the top of the source.

For a starting example, choose one that fits the construction and adapt its
geometry to the brief:

- One BRep part: `examples/simple_brep_intent.py` and `simple_brep_build.py`.
- Separately manufactured BRep parts: `examples/assembly_intent.py` and `assembly_build.py`.
- A BRep shell lofted through key sections: `references/surface-shell.md`.

`references/authoring-example.md` gives the invocation. Its `BuildSession` examples
derive scene bindings and operation evidence from `add`/`cut`, then submit final
edits with `finish` and write artifacts with `export`. Keep construction choices
(section stations, cutter overshoot, loft mode) in build source; intent records
the required final dimensions, function and form. The existing `write_scene` and
`export_part`/`export_assembly`/`export_regions` path remains available for explicit
bindings and color partitioning. Query signatures with `a3d capabilities --symbol NAME`.

Compile, audit, package, and render through one public boundary:

```bash
a3d compile "<name>_scene.json" \
  --marker ".<name>.generation-start" \
  --intent "<name>_intent.json" \
  --source "<name>_build.py" \
  --output-dir .
```

The command prints a compact decision with actionable errors and paths. Full
evidence remains in the reported compile-result and audit files; saving evidence
does not require loading it into context. Default compile and diagnostic output
have a total character budget. Follow diagnostic pagination for omitted blocking
findings; use `a3d diagnose RESULT.json --id ID --field FIELD` for bounded chunks
of a specific field. See `references/cad-compile.md` for query options. Group related findings by their source cause
and make a coordinated edit. Thickness findings include a location witness and,
after repeated checks, a warning comparison; use its measurement and sampling
scope to judge whether the change addressed that region.
`references/design-review.md` helps with fit and
assembly reasoning; `references/bambu-printability.md` explains which process
advisories call for a change and which can remain disclosed limitations.

Repair or rebuild the BRep construction around the user's goals, required
functional relationships, and defining form features. A kernel failure identifies
a problem with the current construction; it does not establish that the intended
design must be simplified. Provisional implementation choices may be replaced;
do not treat an arbitrary first idea as a requirement. Do not discard a defining
feature merely to obtain a valid solid. Judge the repair by the final form,
function, and manufacturability against intent, not only a successful operation
or compile.
Use `checked_fillet()` and `checked_chamfer()` for finishing operations; never
catch a failure and silently return the unfinished input solid.
For construction-specific failures, read `references/construction-strategies.md`.

## Iterative edits

Use the paths in the newest valid build report as the current working set. Keep
source and output names stable for refinements and repairs. Create a separate
model version when the user requests a snapshot, variant or additional model.

Keep the existing intent for refinements that preserve its targets. When the user
changes a target recorded there, write a new intent revision file with the updated
request and compile with that path. For the same model, retain its part ID, source
and output paths; the previous intent remains intact. Contract revision is a
separate authoring step before compile.
Bind every changed intent to its verified parent path and SHA-256, and state the
revision kind and reason; see `references/evidence-contract.md`. Declare genuine
design freedom as dimension ranges before using it. An inferred value alone is
not a range, and deleting an unmet requirement is a scope change, not a repair.

Read the current result's `preview`, or its `diagnosticPreview` after a failed
compile, with native `view_image`. Use the visible form to guide the next source
edit. A failed run can leave the previous successful render pointer in place, so
use the paths returned for this attempt. If the returned image cannot be
interpreted, state that visual review is incomplete. Deliver useful editable and
manufacturing files with specific observations and remaining limitations.

For requested dimensions at a particular location, measure the final STEP with
`a3d measure MODEL.step --section-z HEIGHT`; repeat the section option as needed.
For a shape edit, preserve the earlier display GLB before compiling, then use
`a3d compare BEFORE.glb AFTER.glb --view front` for the same camera and scale.
Both inputs must share units and coordinates. `references/design-review.md`
explains the measurement and projected-change limits.

## Reference images

Inspect uploaded images directly and use them as the primary visual reference.
For appearance-led work without a supplied reference, use available native search
to find and actually view a small, relevant set of images when network access is
enabled. Record their source URLs and a few useful silhouette, proportion or
surface relationships in the session workspace; distinguish observed features
from your interpretation. A page title or textual image description is not visual
inspection. Dimension-driven parts do not need an unrelated visual-reference search.

Follow the runtime's network instruction. The server defaults
`CODEX_WEB_SEARCH_ENABLED` to true; only a server environment setting of false
disables it. Enabled configuration does not establish provider support for search,
image retrieval or perception. Use the available tools directly; if a step fails,
state which evidence is unavailable and continue from supplied or local evidence.
Do not run a capability probe before every task or claim to have seen unavailable
images.

When deterministic palette or silhouette facts are useful, run:

```bash
a3d reference "/absolute/image/path" --out "<name>_reference.json"
```

Treat appearance inferred from a perspective image as approximate. Do not claim
an exact reproduction without measurements.
When network access is enabled, use primary component drawings or supplier
specifications for uncertain mounting, fit, or process dimensions. Carry the source
and exact component identity into intent evidence; a similar product is not an
exact specification. Otherwise expose reversible assumptions as parameters.

## Pull details only when needed

Keep one BRep master for each manufactured part, then add applicable assembly,
control, and manufactured-color concerns. Route by meaning, not keywords.

- Unclear construction choice from the available evidence: `a3d guide strategy`.
- Pressable or sliding mechanism: `a3d guide pressable-control`.
- Separately manufactured or assembled parts: `a3d guide multipart`; additionally
  read `references/multipart-connections.md` for direct fastening into printed
  plastic or a serviceable-enclosure closure.
- Installed components or uncertain assembly/functional relationships:
  `references/design-review.md`; use `references/installation-checks.md` when
  checking component clearance, an insertion envelope, support, retention or a passage.
- Non-manufactured display affecting the enclosure or preview:
  `references/installed-displays.md`.
- Permanent printed color: `a3d guide color`; additionally read
  `color/BACKEND.md` for multiple regions inside one part or uncommon topology.
- Uncertain helper signature: `a3d capabilities --symbol NAME`.
- Intent field meaning after consulting its helper: `references/evidence-contract.md`.
- Feature construction and binding: `references/construction-strategies.md`.
- Compile lifecycle or evidence-path questions: `references/cad-compile.md`.

Inspect internals only when the relevant guide, capability result, and reported
error are insufficient.
