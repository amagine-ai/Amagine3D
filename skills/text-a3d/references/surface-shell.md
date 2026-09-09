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

The example checks its 100×80×90 mm envelope and 82 mm final outer width on the
z=90 plane after all geometry edits, using `bounding_box` and `measure_section`.
It reports all dimensional deviations together. Keep these brief targets separate
from loft controls. A new station or fillet can change a previously correct
section; the assertion reports target and measured width before export in both
draft and compile. Use the requested datum and size for the actual model, and
retain the complete shape's preview and independent final STEP checks.

When several finished dimensions keep drifting together, calibrate the controls
together. In a small local script, put the complete construction, cavity and
finishing in a function; measure its final BRep on every call. Keep the targets
fixed. The following is one update for three independent controls and three
measured dimensions, using the existing NumPy runtime:

```python
import numpy as np

actual = measure_finished(controls)  # rebuild; return measured dimensions as an array
error = actual - targets
h = 0.02  # mm; choose a small finite perturbation for these length controls
jacobian = np.column_stack([
    (measure_finished(controls + np.eye(3)[i] * h) - actual) / h
    for i in range(3)
])
change = np.linalg.solve(jacobian, -error)
change *= min(1.0, 2.0 / max(np.max(np.abs(change)), 1e-12))
```

Keep finite-difference probes and proposed changes within physically valid control
bounds. Try this change, then half or a quarter if needed; accept only a reduction
in the Euclidean norm of all target errors. Retest every target
after each accepted update. Keep every trial's controls, measurements and failures,
and cap the experiment at 30 geometry evaluations. Stop on invalid geometry,
singular sensitivity or failure to improve; simplify the construction instead of
enlarging tolerances. Save the resulting controls into the ordinary source, then
run the full public compile. Numerical convergence says nothing about wall,
foot, fit or visual quality; those checks still apply to the changed geometry.

`RULED=True` allows visible shoulder transitions. Smooth lofts are also useful
when the profiles remain valid; changing this setting requires fresh geometry
and wall checks. G2 continuity is not required. Simplify or split the BRep
construction when profiles become unstable. Near a flat foot, a smooth loft can
retreat before widening and leave a thin projecting edge. Union a short outer-foot
extrusion with the loft before subtracting the common cavity, preserving the
required floor and foot profile. This gives direct control of the lower wall;
inspect the actual section near the join. Adding closely spaced stations alone
does not establish a sound foot.

Copy the two files into the current session workspace, then use the public path:

```bash
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out surface_shell_printer-profile.json
cp "$AMAGINE3D_SKILL_DIR/examples/surface_shell_intent.py" .
cp "$AMAGINE3D_SKILL_DIR/examples/surface_shell_build.py" .
python3 surface_shell_intent.py
a3d intent surface_shell_intent.json
a3d compile surface_shell_scene.json --intent surface_shell_intent.json --source surface_shell_build.py --output-dir .
```

The intent is written once; the compiler executes the build source. Its
`BuildSession.add` and `cut` retain the outer solid and actual cavity cutter;
`export` derives their scene bindings and exports the final valid solid. STEP is the
manufacturing master; STL, GLB and 3MF are derived outputs. Run the build through
the compiler so its geometry, wall checks and artifacts remain bound together.

Use the current geometry's preview to refine the silhouette, opening and shoulders
through their source controls. The main skill describes current-run previews and
how to continue after compile findings.
