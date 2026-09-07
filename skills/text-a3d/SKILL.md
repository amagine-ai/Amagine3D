---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Use editable source only from the current session workspace; never inspect
sibling sessions. Use public `a3d` commands and keep source beside artifacts.

## Build contract

Separate user requirements, functional necessities, and your proposed construction
in the existing evidence, acceptance, and assumptions fields. Resolve the risky
fit, assembly path, and controlling dimension relationships before committing an
intent. A helper's name or successful compile is not proof of the product's function.

For a new model, create its marker and profile before intent:

```bash
a3d mark --mark ".<name>.generation-start"
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 \
  --out "<name>_printer-profile.json"
```

Run `<name>_intent.py` once to write `<name>_intent.json`; validate it with
`a3d intent`. Create/update `<name>_build.py` from it. Build source owns scene
and geometry but never rewrites intent. Use BRep helpers for dimension-controlled
geometry and mesh binding only for freeform geometry. Keep key dimensions near the top.
For the complete public authoring path, adapt `examples/assembly_intent.py` and
`examples/assembly_build.py`; read `references/authoring-example.md` for their scope
and invocation. Query individual signatures with `a3d capabilities --symbol NAME`.

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
--id ID` (or `--code`/`--severity`). Fix shared source causes and compile again.
Distinguish a wrong construction, missing proof, and a tool limitation from a
conflict in the requirements. A packing heuristic failure does not prove that
parts cannot fit. Preserve user-visible function when changing your construction;
rebuilding intent to remove a failing target is not a repair. See
`references/design-review.md` when resolving a failed fit or judging readiness.

## Iterative edits

Treat the paths referenced by the newest valid build report as the current model's
canonical working set. For a follow-up change, update the same editable source and
run the same build and compile path so generated scene, evidence, previews, and
manufacturing outputs are republished at their canonical paths.

Do not rename, move, or copy the current working set merely to preserve a prior
revision. The compiler's staged publication is the rollback boundary, and the
conversation is the change history. Create a separate version only when the user
explicitly asks for a snapshot, fork, variant, or additional model. After a failed
compile, repair the canonical source and compile again instead of creating an
alternate set of artifact paths.

Before replying, read the newest five-view preview with the native `view_image`
tool and compare it to the intended landmarks. Numeric image statistics establish
neither appearance nor functional correctness. If the tool returns image content
but you cannot interpret it, report visual review as incomplete; do not blame file
access or start an unrelated viewer. Deliver the editable source and useful
artifacts with the actual remaining limitations.

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

Choose one geometry master, then add applicable assembly, control, and
manufactured-color concerns. Route by meaning, not keywords.

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

Inspect internals only when the relevant guide, capability result, and reported
error are insufficient.
