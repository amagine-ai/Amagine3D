---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Work in the current session workspace with the public `a3d` tools. Keep editable
source beside the outputs. Manufactured parts use build123d BRep solids and
genuine STEP masters; STL, GLB and 3MF are derived outputs.

## Design the requested object

Identify the user's required dimensions, functions and defining visual relationships.
Keep them distinct from proposed construction and dimensions in intent. Give
adjustable inferred dimensions explicit ranges before using them; otherwise the
contract treats them as fixed. Expose controlling dimensions near the source top.

For functional products, develop the component space, support, access and required
exterior-to-interior passages with the main form. Unknown components can use
replaceable, parameterized envelopes. Purchased references stay outside the
manufactured part list; their visibility does not determine installation needs.

Use the relevant guidance below when making that design decision. Begin with the
matching construction example and load further detail for an applicable feature
or a concrete error, rather than reading the reference library in advance.

| Current design need | Guidance |
|---|---|
| One dimension-driven BRep part | `examples/simple_brep_intent.py` and `simple_brep_build.py` |
| Appearance-led form or enclosure | `references/surface-design.md`; `references/surface-shell.md` for its loft example |
| Separately manufactured parts | `a3d guide multipart`; `examples/assembly_intent.py` and `assembly_build.py` |
| Installed components or assembly-path reasoning | `references/design-review.md`; `references/installation-checks.md` for applicable geometry evidence |
| Direct fasteners into printed plastic or serviceable closure | `references/multipart-connections.md` |
| Pressable or sliding control | `a3d guide pressable-control` |
| Non-manufactured display component | `references/installed-displays.md` |
| Permanent manufactured color | `a3d guide color`; `color/BACKEND.md` for multiple regions within one part |

Inspect supplied reference images directly. For appearance-led work without a
reference, use available native search and actually view a few relevant images
when network access is enabled. Record sources and useful contour/proportion
relationships locally. Use primary component specifications for uncertain fit
dimensions. If evidence is unavailable, state the gap and expose assumptions;
perspective images do not establish exact dimensions. `a3d reference IMAGE`
provides optional palette/silhouette facts, not a substitute for seeing the image.

## Build and inspect

For a new model, create a marker before writing source. Reuse the selected printer
profile or resolve one with `a3d profile`; record a fallback printer as an assumption.
Resolve usable print volume early. For multipart work, plan part bounds in their
print orientations with `a3d layout`; a failed layout is not permission to resize.

Write `<name>_intent.py` separately and run it once to create the intent; validate
with `a3d intent`. The build source constructs geometry, binds the actual feature
objects with `write_scene(...)`, and calls the matching exporter. Use the examples
above; `references/authoring-example.md` provides their invocation and binding API.
Query related signatures together with `a3d capabilities --symbol NAME`.

```bash
a3d mark --mark ".<name>.generation-start"
# Reuse a valid profile, or choose machine/nozzle with a3d profile.
python3 "<name>_intent.py"
a3d intent "<name>_intent.json"
a3d compile "<name>_scene.json" --marker ".<name>.generation-start" \
  --intent "<name>_intent.json" --source "<name>_build.py" --output-dir .
```

The compiler executes the build source; do not run it separately. Inspect its
current `preview`, or `diagnosticPreview` on failure, with native `view_image`.
Inspect the primary form before adding dependent detail. Compare the visible
contours, proportions and functional spaces with the brief, then edit the geometry
responsible for the largest discrepancy. A successful compile does not settle
visual or functional omissions; state when visual inspection is incomplete.

## Repair and revise

Keep the latest valid report's source/output paths stable for the same model.
Repair geometry against the existing user targets. Change construction when
necessary while preserving defining form and function. Use `checked_fillet()` and
`checked_chamfer()`; do not silently return unfinished geometry after a failure.

Read only the diagnostic detail needed for the repair with `a3d diagnose`.
Group findings with a shared cause into one source edit. Keep full evidence on
disk; use `references/cad-compile.md` for pagination or compile lifecycle questions.
`references/construction-strategies.md` helps with construction failures;
`references/bambu-printability.md` distinguishes process advisories and blockers.
Inspect internals only when public guidance and the reported error are insufficient.

When the user changes a target, create a separate intent revision bound to its
verified parent; see `references/evidence-contract.md` for revision and field rules.
Do not remove unmet requirements to make a check pass. Deliver the useful editable
and manufacturing files with observations and remaining limitations.
