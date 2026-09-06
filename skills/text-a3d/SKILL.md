---
name: text-a3d
description: Create or modify printable 3D models and their editable sources.
---

# text-a3d

Work directly in the current session directory. Use the public `a3d` commands
and the helpers in `$AMAGINE3D_SKILL_DIR`; do not install packages or write into
the project source tree. Start from existing editable source when present.
Keep inspection calls small and retry a simplified call after a wrapper syntax
error; one malformed wrapper does not mean that `a3d` is unavailable.

## Build contract

For a new model, choose a short lowercase name, then create the marker and
printer profile before authoring the immutable intent:

```bash
a3d mark --mark ".<name>.generation-start"
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 \
  --out "<name>_printer-profile.json"
```

Create `<name>_intent.py`, run it once to write `<name>_intent.json`, and check
it with `a3d intent`. Create or update `<name>_build.py` from that intent. The
build source owns the semantic scene and geometry but must never rewrite the
intent. Import the documented helpers from `cad_helpers.py` for BRep, but do
not read that source file to discover signatures; query `a3d capabilities
--symbol NAME` instead. Use mesh binding only when freeform geometry actually
needs it. Keep important dimensions near the top.

Compile, audit, package, and render through the single public boundary:

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
Before replying, open the newest preview and deliver the editable source plus
useful STL/STEP/3MF/GLB artifacts.

## Reference images

Inspect uploaded images directly. When deterministic palette or silhouette
facts are useful, run:

```bash
a3d reference "/absolute/image/path" --out "<name>_reference.json"
```

Treat appearance inferred from a perspective image as approximate. Do not claim
an exact reproduction without measurements.

## Pull details only when needed

Use `a3d guide workflow`, `a3d guide pressable-control`, `a3d guide multipart`,
or `a3d guide color` for a concrete need. Use
`a3d capabilities --symbol NAME` for an uncertain API. Inspect compiler or
validator internals only when these public interfaces and the reported error do
not explain the problem.

Rare fallbacks remain pull-only: `references/multipart-basics.md`,
`references/multipart-connections.md`, and `references/installed-displays.md`.
