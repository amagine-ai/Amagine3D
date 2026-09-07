# Continuous-section shell example

Use `examples/surface_shell_intent.py` and `examples/surface_shell_build.py` when
the intended silhouette needs smoothly changing sections and a mesh master is
appropriate. This is one surface construction example, not the default shape or
architecture for consumer electronics. Keep dimension-controlled mechanisms and
interfaces in the appropriate BRep workflow.

The example exposes width, depth, height, horizontal wall inset, floor thickness,
section exponent and separate upper/lower shoulder controls. Quintic transitions
join the shoulders to the full-width middle with continuous first and second
derivatives. Its 96-point sections and 41 height levels form a real cavity, an
annular rim and a closed floor. Increasing samples improves tessellation, not the
underlying proportions. The inset is exact within each analytic horizontal
section; sloping walls do not have an exact 3D normal-offset thickness. Check the
measured wall thickness and shoulder overhang after changes.

Copy the two files into the current session workspace, then use the public path:

```bash
a3d mark --mark .surface_shell.generation-start
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out surface_shell_printer-profile.json
cp "$AMAGINE3D_SKILL_DIR/examples/surface_shell_intent.py" .
cp "$AMAGINE3D_SKILL_DIR/examples/surface_shell_build.py" .
python3 surface_shell_intent.py
a3d intent surface_shell_intent.json
a3d compile surface_shell_scene.json --marker .surface_shell.generation-start --intent surface_shell_intent.json --source surface_shell_build.py --output-dir .
```

The intent is written once; the compiler executes the build source. This mesh
example ends at `write_scene`; the public compiler handles hybrid export. Do not
call `export_assembly` or run the build source separately.

When geometry is available, compile provides a diagnostic preview before full QA
finishes. Inspect that preview against the intended silhouette, opening and
shoulders; it does not establish manufacturing readiness. On failure, distinguish
the surface construction from tessellation, export and validation defects. Keep
identity-defining curves and openings while fixing the cause; deleting them to
silence a check is not an acceptable repair.
