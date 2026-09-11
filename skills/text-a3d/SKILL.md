---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Use public `a3d` commands in the session workspace. Keep editable build123d
BRep source and STEP masters beside derived meshes; intent owns requirements
and source owns controls.

In final intent, give ordinary exterior dimensions their nominal value and ±0.1
mm unless user tolerances or fits override it. Preserve fixed targets;
use 0.01 mm check precision unless explicitly tighter.

<!-- a3d-workflow:v2 classify > functional-draft > feedback > contract > compile > evidence-repair -->

## 1. Classify and draft visible construction

Read exactly `$AMAGINE3D_SKILL_DIR/SKILL.md` once; never substitute a cwd or
global namesake. Before the first source edit, use only directly relevant
`a3d guide TOPIC` or `a3d capabilities --symbol NAME` evidence. Do not preload
deep references, final intent/profile, final plates or the full compiler before
a visible draft.

For an existing model, preserve source/intent feature IDs and targets, then run:

```bash
a3d draft existing_build.py --intent existing_intent.json
```

For a new ordinary solid, copy the smallest seed:

```bash
cp "$AMAGINE3D_SKILL_DIR/examples/simple_brep_build.py" ./model_build.py
```

First classify the task. It is **assembly-critical** when manufactured parts,
installed components, service access, closure, locator, retention, fasteners,
motion, mating fit or through openings can change envelope or topology. Mere
seams, color regions and grooves are not.

An ordinary appearance-led draft needs only its main envelope, identity-bearing
volumes, silhouette, proportions and major layout. For assembly-critical work,
start from the installed-module seed when useful:

```bash
cp "$AMAGINE3D_SKILL_DIR/examples/installed_module_draft.py" ./model_build.py
```

Before its first draft, add a reversible **functional skeleton**: provisional
part owners, component envelope/cavity, openings, insertion/service/driver
paths, and reserved support, locator, retention or fastener volumes. A direct-
fastened enclosure needs one locator, symmetric shared fastener datums,
removable-part clearance, receiver blind pilots/bosses and reachable access.

Budget interfaces before fixing the exterior. Boss diameter must cover pilot
diameter, twice the minimum radial wall and a named design margin; reserve root
material and a blind end. Keep axes clear of keepouts, ports, corners and thin
walls. Floating-point excess is not design margin; load exact hardware values
only when needed.

Register provisional IDs, owners and `solid`/`cutter`/`separate` roles in
`constructionFeatures`; these are a construction index, not intent requirements.
Then run:

```bash
a3d draft model_build.py
```

Open it with `view_image`; a draft claims no final dimensions, printability,
installation, manufacturing acceptance or readiness.

## 2. Use visible and functional feedback

Compare the draft with the brief and opened images; name silhouette, proportion,
placement and access differences, then change owning parameters. Confirm openings
connect, interfaces fit material and ownership avoids a topology rewrite. Repeat
only after source changes; never rerun an unchanged `source.sha256`.

Load one specialist reference only for a concrete unresolved issue. Do not
reread this skill or the same guidance for an unchanged problem. If image
evidence cannot be interpreted, report review incomplete; metadata is not sight.

Keep exploration and final geometry in one source. `BuildSession` may start with
`part_names`; every `add`, `cut` and `observe` has an owning `part_name`. Proceed
only when key `constructionFeatures` IDs/owners/roles are stable and feedback no
longer requires replacing the overall topology.

## 3. Finalize the contract and construction

Now resolve printer/nozzle profile, manufacturing mode, topology, interfaces,
service paths and immutable intent. Use `references/authoring-example.md`; load
`references/bambu-printability.md` and run `a3d layout` only when volume,
orientation or layout matters. Never switch profile or scale requirements to
clear QA; a larger machine is only an alternative to footprint failure.

For confirmed needs, load `references/multipart-connections.md` for fastening,
`references/design-review.md` for assembly, or
`references/installation-checks.md` for purchased components; only then read
`examples/installed_module_build.py`.

Build body, cavity and openings from shared datums; complete support, retention
and access before finishing. Keep missing dimensions reversible. Use
`references/surface-shell.md` only for loft/finishing drift.

Submit finishing through `BuildSession.finish` with `checked_fillet` or
`checked_chamfer`, then `export`; never keep unchanged geometry after a failed
finish. Validate the separate intent with `a3d intent` before compiling.

## 4. Run the initial full compile

Run one full compile after contract and construction are coherent:

```bash
a3d compile "model_scene.json" --intent "model_intent.json" --source "model_build.py" --output-dir .
```

Run CAD commands directly; never pipe draft, compile or diagnose to `head`,
`tail` or another filter because persisted JSON and the direct exit status are
authoritative. Keep compile inputs stable. Inspect the current preview and
compare final STEP with intent; pass is not readiness.

## 5. Diagnose and make evidence-gated repairs

Use compact code, stage, stable issue ID, affected owners, bounds and repair
hint first. Query only an unread field for the same `(runId, issue ID, field)`:

```bash
a3d diagnose result.json --id FINDING_ID --field FIELD
```

Treat occurrences with the same stable ID or cause across part and plate stages
as one root cause. Repair its owning datum, parameter, feature or connection.
Preserve intent; a target change needs a verified parent SHA and reason under
`references/evidence-contract.md`.

Every expensive repeat must consume new evidence or produce new state:

- draft requires changed source from the prior `source.sha256`;
- compile requires changed source or legitimate changed intent/profile/input
  bindings; do not replay inputs that already produced a persisted result/runId;
- a transport interruption without a persisted result is not a completed compile;
- guidance requires a newly identified concrete problem;
- progress means `repairDelta.resolved`/`newlyUnblocked`, a later passed stage, or
  a `remaining` issue whose observed measurement, bounds or witness moved toward
  expected without a related `regressed` issue.

If the same issue has no evidence change, do not compile or reread the same
material. Return to its owning feature/interface/source line and shared datum, or
replace the construction strategy. A larger cutter does not repair an operation
that misses material; repeated compiles do not replace geometric reasoning.

When `pass=true`, status is `awaiting-visual-review`, and `deliveryReady=false`,
stop compiling and inspect the preview. Before replying, read that result and
report `visualReviewRequired`; never claim done, ready or review complete while
`deliveryReady=false`. Match support/bridge claims to selected print orientation
and current mesh-audit warnings; otherwise disclose risk.
If inspection finds a defect, change source first.

Use `a3d measure MODEL.step --section-z HEIGHT` for sections and
`a3d compare BEFORE.glb AFTER.glb --view front` for shape edits. Finite samples
do not prove global walls; inspect witness scope and stop inside accepted ranges.

## Pull details only for the current problem

- Construction: `a3d guide strategy`, then `references/construction-strategies.md`.
- Motion: `a3d guide pressable-control`.
- Assembly: `a3d guide multipart`, then `references/multipart-connections.md`.
- Non-manufactured display: `references/installed-displays.md`.
- Printed color: `a3d guide color`; uncommon topology: `color/BACKEND.md`.
- Intent: `references/evidence-contract.md`; compile: `references/cad-compile.md`.

Read internals only when public guidance and the error are insufficient. Deliver
the newest coherent source/report/output set with visual/measured observations
and remaining limitations.
