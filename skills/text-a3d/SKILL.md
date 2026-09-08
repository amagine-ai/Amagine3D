---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Use editable source only from the current session workspace; never inspect
sibling sessions. Use public `a3d` commands and keep source beside artifacts.

Develop the primary form from the requested product character and use. For
consumer enclosures and other appearance-led objects, read
`references/surface-design.md` for shaping volumes, choosing surface controls,
and integrating details. Choose the construction that gives this design useful
freedom to evolve.

## Author and build

Separate user requirements, functional necessities, and your proposed construction
in the existing evidence, acceptance, and assumptions fields. Match the detail
to the request: a form concept can use proposed dimensions and simple physical
seats; a fit- or assembly-dependent design needs the relevant component envelopes,
controlling dimensions and installation relationships.

For a new model, create its marker before authoring intent and build source.
Use the user's or project's printer selection and reuse its valid profile when
available. The profile command below illustrates an A1 mini with a 0.4 mm nozzle;
when using it as a fallback, record that process assumption in intent.

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
- A mesh shell built from varying sections: `references/surface-shell.md`.

`references/authoring-example.md` gives the BRep invocation. For an all-BRep
scene, finish the build source with `write_scene(...)` and the appropriate
`export_part(...)`, `export_assembly(...)` or `export_regions(...)` call. If any
part has a mesh master, the source finishes at `write_scene(...)`; public compile
handles hybrid export for the whole scene. Query individual signatures with
`a3d capabilities --symbol NAME`.

Compile, audit, package, and render through one public boundary:

```bash
a3d compile "<name>_scene.json" \
  --marker ".<name>.generation-start" \
  --intent "<name>_intent.json" \
  --source "<name>_build.py" \
  --output-dir .
```

The command prints a compact decision with actionable errors and paths. Full
evidence remains in the reported compile-result and audit files. If the summary
is insufficient, query only the relevant detail with `a3d diagnose RESULT.json
--id ID` (or `--code`/`--severity`). Group related findings by their source cause
and make a coordinated edit. Use diagnostics to improve the construction while
preserving the requested form and function. `references/design-review.md` helps
with fit and assembly reasoning; `references/bambu-printability.md` explains which
process advisories call for a change and which can remain disclosed limitations.

## Iterative edits

Use the paths in the newest valid build report as the current working set. Keep
source and output names stable for refinements and repairs. Create a separate
model version when the user requests a snapshot, variant or additional model.

Keep the existing intent for refinements that preserve its targets. When the user
changes a target recorded there, write a new intent revision file with the updated
request and compile with that path. For the same model, retain its part ID, source
and output paths; the previous intent remains intact. Contract revision is a
separate authoring step before compile.

Read the current result's `preview`, or its `diagnosticPreview` after a failed
compile, with native `view_image`. Use the visible form to guide the next source
edit. A failed run can leave the previous successful render pointer in place, so
use the paths returned for this attempt. If the returned image cannot be
interpreted, state that visual review is incomplete. Deliver useful editable and
manufacturing files with specific observations and remaining limitations.

## Reference images

Inspect uploaded images directly. When deterministic palette or silhouette
facts are useful, run:

```bash
a3d reference "/absolute/image/path" --out "<name>_reference.json"
```

Treat appearance inferred from a perspective image as approximate. Do not claim
an exact reproduction without measurements.
When this turn permits web search, use primary component drawings or supplier
specifications for uncertain mounting, fit, or process dimensions. Carry the source
and exact component identity into intent evidence; a similar product is not an
exact specification. Otherwise expose reversible assumptions as parameters.

## Pull details only when needed

Choose one geometry master for each manufactured part, then add applicable
assembly, control, and manufactured-color concerns. Route by meaning, not keywords.

- Unclear representation choice from the available evidence: `a3d guide strategy`.
- Pressable or sliding mechanism: `a3d guide pressable-control`.
- Separately manufactured or assembled parts: `a3d guide multipart`; additionally
  read `references/multipart-connections.md` for direct fastening into printed
  plastic or a serviceable-enclosure closure.
- Installed components or uncertain assembly/functional relationships:
  `references/design-review.md`; use `references/installation-checks.md` when
  checking component clearance, an insertion envelope, support or retention.
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
