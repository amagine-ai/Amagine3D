---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Work only in the current session workspace, using public `a3d` commands and the
managed runtime. Keep editable source beside its artifacts. Manufactured parts
use build123d BRep solids and genuine STEP masters; derive meshes from them.
Keep user requirements separate from construction controls. A fixed dimension,
including one in feature acceptance, remains an equality during repair; only an
explicitly declared range permits variation. Never resize a target to pass QA.

## Start with one source and a visible construction

For an existing model, read its source and intent, preserve its identities and
targets, and draft with `a3d draft SOURCE.py --intent INTENT.json`. For loft or
finishing dimensions that drift, first read `references/surface-shell.md`: adapt
its complete-geometry measurement callback and jointly calibrate the controls.

For a new surface-led construction, use `references/surface-design.md` and the
intent-bound example in `references/surface-shell.md`; follow its setup.
For other new models, select one build example: `simple_brep` for one solid,
`installed_module` for an enclosure with internal components. Copy its build source:

```bash
example_name=installed_module
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_build.py" .
```

Read that source and adapt its named controls and geometry to the brief, then run
`a3d draft "${example_name}_build.py"`.
Inspect the returned preview with `view_image` before preparing final evidence.
These two sources run without intent or profile.

Develop the body, cavity and openings from shared component envelopes and datums,
then add support, retention and assembly access before exterior finishing.
Missing component dimensions need reversible, explicit assumptions; preserve the
requested function. Use the supplied images for form; see the Visual review
section of `references/design-review.md` when appearance or uncertain fit needs research.

Keep exploration and final geometry in this same source. `BuildSession` can start
with `part_names`; give each `add`/`cut`/`observe` its owning `part_name`. Draft's
`constructionFeatures` lists registered IDs/owners/roles to reuse in intent, not
requirements or every operation. Drafts remain isolated previews, never delivery.
Query only missing signatures with `a3d capabilities --symbol NAME`.

## Finish the functional construction, then compile

Once the geometry is visible, use `references/authoring-example.md` for the chosen
example's separate profile/intent setup and final ownership. Intent records
requirements; source owns construction controls. Reuse a valid selected printer
profile. Confirm print volume and part placement with `a3d layout` before fixing
details; `references/bambu-printability.md` covers profiles, packing and process.

For a functional assembly, resolve fit, insertion, support, retention and access
using `references/design-review.md`. Bind applicable installed-component checks
with `references/installation-checks.md`. Purchased references stay outside
manufactured parts; their display is optional and does not prove installation.

Submit finishing through `BuildSession.finish`, with `checked_fillet` or
`checked_chamfer`, then `export`. Editing the inspection copy from `build.part()`
does not change exports. Never silently keep unfinished geometry after a failed finish.
Validate the separate intent with `a3d intent`, then use the public compile boundary:
`BuildSession.export()` generates the scene and report from the authored solids;
the scene path passed to compile need not exist yet.

```bash
a3d compile "<name>_scene.json" --intent "<name>_intent.json" \
  --source "<name>_build.py" --output-dir .
```

## Repair against the current result

Keep inputs stable while compile runs; it owns freshness and hash binding.
Use compact findings and `a3d diagnose RESULT.json --id ID --field FIELD` for
omitted detail. Group causes, repair the source, and compile again. Boolean
witnesses locate gaps, owners and nearest points: fix the datum or connection
instead of assuming a larger cutter repairs an operation in empty space.
Preserve defining form and function when replacing a failed construction.

Inspect the current `preview` or failure's `diagnosticPreview` with `view_image`;
an older successful render may be stale. Compare the final STEP against all
intent targets. For a size at a height use `a3d measure MODEL.step --section-z HEIGHT`;
typed section dimensions are checked on final STEP at 0.0001 mm numerical precision.
For a shape edit, `a3d compare BEFORE.glb AFTER.glb --view front` uses a shared
camera and scale when coordinates and units match. Section insets and finite
thickness samples do not prove global minimum walls: inspect witness location,
material direction and sampling scope. Compile success alone is not model quality.

Deliver the newest coherent source/report/output set with concrete observations
and remaining limitations. If image evidence cannot be interpreted, visual review
remains incomplete. Preserve intent during repairs; requested target changes need
a separate revision with verified parent SHA and reason under
`references/evidence-contract.md`. Deleting an unmet requirement changes scope.

## Pull details for the current question

- Construction choice or failure: `a3d guide strategy`, `references/construction-strategies.md`.
- Motion: `a3d guide pressable-control`.
- Multipart fit or closure: `a3d guide multipart`, `references/multipart-connections.md`.
- Non-manufactured display: `references/installed-displays.md`.
- Printed color: `a3d guide color`; uncommon region topology: `color/BACKEND.md`.
- Intent fields: `references/evidence-contract.md`; compile lifecycle: `references/cad-compile.md`.

Read internals only when the relevant guide, capability result and actual error
do not answer the question.
