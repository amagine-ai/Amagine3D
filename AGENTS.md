# Amagine3D Codex runtime

- Each task runs in one isolated session workspace. Create and modify task artifacts only in the current working directory.
- For CAD tasks, run `a3d help`, then read `$AMAGINE3D_SKILL_DIR/SKILL.md` once for the compact public workflow.
- Start from existing workspace files and public `a3d` interfaces. Open one targeted `a3d guide TOPIC` only for a concrete modeling need. Do not read `cad_helpers.py` or other helper/compiler/validator source merely to discover an API; use `a3d capabilities --symbol NAME`. Inspect the smallest relevant internal section only when the public guide, capability lookup, and a concrete reported error are still insufficient.
- Keep tool calls small and single-purpose. If a code-mode wrapper fails with a JavaScript syntax or quoting error, simplify and retry the call; do not report the CAD tool as unavailable based on one wrapper failure.
- Use the managed tools already provided by the project. Do not install packages.
- Keep editable source beside generated manufacturing and preview artifacts.
- Validate the final CAD result and inspect its latest preview before replying.
- Be concise in the final response: summarize the result and name the useful output files.
