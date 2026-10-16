---
name: a3d-text
description: Create or modify printable 3D models and their editable sources.
---

# a3d-text

Read exactly `$AMAGINE3D_SKILL_DIR/SKILL.md` once; never substitute a cwd or global namesake.
Examples live in `$AMAGINE3D_SKILL_DIR/examples/`; references in `$AMAGINE3D_RUNTIME_DIR/references/`.
On continuation/compaction use `a3d agent context` and its nextInspect/nextDesignRead; reuse known contracts until capabilityVersion changes.
Keep editable designs or build123d BRep source and STEP masters in the workspace. Intent owns requirements; source owns controls.
Unless tolerances/fits override them, exterior dimensions use nominal ±0.1 mm and checks 0.01 mm precision. Never relax QA targets.

<!-- a3d-workflow:v2 classify > functional-draft > feedback > contract > compile > evidence-repair -->

## Draft and review

Prefer DSL, including for enclosures; name unsupported geometry/binding before Python fallback.
Batch only needed contracts: `a3d agent capabilities '{"designOps":["open_box","hole"]}'` (Python: symbols).
Results page by output size. Follow page.nextRequest with `a3d agent inspect`; do not preload contracts or references.
Starter: configurable cube; full: `$AMAGINE3D_SKILL_DIR/examples/open_box.a3d.json`; edit: `$AMAGINE3D_RUNTIME_DIR/references/parametric-design.md`:

Use useful independent controls with bilingual labels/ranges, shared IDs for related geometry, and expressions for derived dimensions. Preserve frozen intent/QA; a range is not a manufacturing guarantee.

```json
{"action":"apply","design":"model.a3d.json","baseRevision":null,"changes":[{"kind":"add","target":"params.size","value":{"type":"length","value":80,"range":[50,100],"label":{"en":"Overall size","zh":"整体尺寸"}}},{"kind":"add","target":"params.wall","value":{"type":"length","value":2,"range":[1,4],"label":{"en":"Wall thickness","zh":"壁厚"}}},{"kind":"add","target":"parts.body","value":{"result":"@body.shell"}},{"kind":"add","target":"body.shell","value":{"op":"open_box","size":["=size","=size","=size"],"wall":"=wall","floor":"=wall"}}]}
```

Save requests in the workspace and run `a3d agent design --args-file edits.json`. Keep stable IDs; batch related edits and use the returned revision.
Updates use `{"kind":"update","target":"params.wall","set":{"value":2.4}}`; later apply requests require baseRevision. Do not replace the whole design for local repairs.
Expressions require matching units (`=height - mm(2)`); references use `@body.shell`. Frame local v = normal × u; u and normal are orthogonal.
For Python, read only `$AMAGINE3D_SKILL_DIR/examples/simple_brep_build.py` or, when assembly changes topology, `$AMAGINE3D_SKILL_DIR/examples/installed_module_draft.py`.
Use BuildSession with part_names before intent and owned constructionFeatures. Skip Python authoring/intent-writer examples in DSL mode.
Reserve cavity, openings, support, retention, fastener and insertion/service paths before finishing; keep unknown dimensions reversible.
For construction decisions needing detail, read `$AMAGINE3D_RUNTIME_DIR/references/construction-feedback.md`.

```bash
a3d agent build '{"phase":"draft","design":"model.a3d.json","revision":"REVISION"}'
a3d agent build '{"phase":"draft","source":"model_build.py"}'
```

View the returned preview with view_image and edit from actual form/function feedback. Failed-operation diagnostic images may show only prior valid geometry.
If artifactStages reports display failure after passed cadExport, keep the bound STEP and use `a3d agent display '{"report":"RUN.json","sha256":"SHA"}'`; localize the issue before changing manufacturing geometry.
At `awaiting-preview`, manufacturing has passed but PNG evidence is incomplete. Use context's `nextDisplay`; display retry verifies and reuses the original manufacturing proof without rerunning source or checks. Automatic pass still requires visual review.
Save up to six observations/todo items with `a3d agent checkpoint` bound to the run report. It records agent feedback, never CAD acceptance or proof that an image was viewed.
A draft does not establish installation, final dimensions or printability. Repeat builds after changed inputs, new evidence or incomplete/failed attempts.

## Contract and compile

After the functional skeleton is stable, declare independent intent, validate and run the first compile before more cosmetic edits. Check LCD support/stop/passage and screw clearance/pilot/engagement now.
DSL uses `$AMAGINE3D_SKILL_DIR/examples/intent.example.json` for a solid or `$AMAGINE3D_SKILL_DIR/examples/installed_module_intent.json` for collar/screw/component assemblies.
Assembly intent pairs with `$AMAGINE3D_SKILL_DIR/examples/installed_module.a3d.json`; read only when construction bindings are needed.
Specify independent targets and chosen printer/profile; preserve owners, physical feature bindings and access. Do not copy failed geometry into accepted targets.
Python final authoring uses `$AMAGINE3D_RUNTIME_DIR/references/authoring-example.md`. Fastening/installation details use multipart-connections.md/installation-checks.md under the runtime references directory when needed.
Validate before compile: `a3d agent validate '{"design":"model.a3d.json","revision":"REVISION","intent":"model_intent.json"}'` (Python: intent/source/available scene).
cut/pocket/hole/open_box requirements describe cutters. Each physical part needs solid/separate material in its result chain; observe a pure subtractive final body with zero-offset translate bound to an existing final-body intent feature, then select that result. No additional shape is needed.
Frozen intent files are immutable, including their bytes. For revisions, use a new file linked to parent SHA/reason; read `$AMAGINE3D_RUNTIME_DIR/references/evidence-contract.md` then.
Cosmetic edits retain frozen dimensions and functional targets; compare narrative acceptance with actual controls. Derive installation witnesses from the unchanged component envelope and complete actual path, never reduce them to silence collisions.

```bash
a3d agent build '{"phase":"compile","design":"model.a3d.json","revision":"REVISION","intent":"model_intent.json"}'
a3d agent build '{"phase":"compile","source":"model_build.py","intent":"model_intent.json","scene":"model_scene.json"}'
```

Python requires a scene destination even when source generates it; DSL creates one. All task operations accept --args-file.

## Evidence and conditional details

On failure, read `$AMAGINE3D_RUNTIME_DIR/references/error-diagnostics.md`; load its linked operation guide only as needed. Route request/validation, Boolean or finishing errors by returned evidence, not code prefix alone.
Read issue IDs, owners, actual/expected and repair hints; use report/SHA with agent inspect and page.nextRequest. Do not guess filenames, write report parsers or pipe CAD output through head/tail.
Repair owning datums/features; unchanged fully bound success is reused. At pass=true/status=awaiting-visual-review, view the actual preview instead of recompiling.
Report status, visualReviewRequired, review scope and unresolved warnings accurately; automatic acceptance alone does not prove manufacturing readiness.
Thin-wall sampling minima/p05 do not prove a false alarm: retain warnings unless a targeted BRep/section check at the risk location establishes the cause.
Use printOrientationEvidence for support/bridge claims; rotateDegreesXYZ is the actual export pose.
Read `$AMAGINE3D_RUNTIME_DIR/references/bambu-printability.md` and use a3d layout when orientation/packing matters; never scale targets or switch printer to silence failure.
Surface/finishing issues use `$AMAGINE3D_RUNTIME_DIR/references/surface-shell.md`; installed displays use `$AMAGINE3D_RUNTIME_DIR/references/installed-displays.md`.
Uncommon printed color topology uses `$AMAGINE3D_RUNTIME_DIR/color/BACKEND.md`; read internals only if the public contract and specific error are insufficient.
