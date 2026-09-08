# BRep loft shell example

This example implements one of the constructions described in `surface-design.md`.

Use `examples/surface_shell_intent.py` and `examples/surface_shell_build.py` to
develop an enclosure from a few editable key sections. Adapt the proportions,
section shapes and opening to the requested product; add mechanisms and
interfaces as BRep features of their owning parts.

The example uses six rounded-rectangle sections. Each `STATIONS` entry controls
height, width, depth, corner radius and center position. It lofts an outer solid
and subtracts an inner loft to form a real cavity, rim and floor. `WALL_INSET`
and `FLOOR` control the section inset and base thickness. Section insets do not
guarantee constant 3D normal thickness on sloping walls; check measured wall
thickness and shoulder overhang after changes.

`RULED=True` allows visible shoulder transitions. Smooth lofts are also useful
when the profiles remain valid; changing this setting requires fresh geometry
and wall checks. G2 continuity is not required. Simplify or split the BRep
construction when profiles become unstable.

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

The intent is written once; the compiler executes the build source. Its
`BuildSession.add` and `cut` retain the outer solid and actual cavity cutter;
`export` derives their scene bindings and exports the final valid solid. STEP is the
manufacturing master; STL, GLB and 3MF are derived outputs. Run the build through
the compiler so its geometry, wall checks and artifacts remain bound together.

Use the current geometry's preview to refine the silhouette, opening and shoulders
through their source controls. The main skill describes current-run previews and
how to continue after compile findings.
