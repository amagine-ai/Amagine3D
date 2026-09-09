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

The intent keeps the 100×80×90 mm envelope and the 82 mm final outer width on
z=90 separate from editable loft controls. Its `section_dimensions` declaration
on `shell-surface` measures the owning part's outer section in semantic coordinates;
it does not measure a hole, passage or wall thickness. Public compile checks the
bound final STEP after finishing. Draft remains an unvalidated preview and may
still miss these targets. Use the callback below to check all target errors
together; final compile can stop at an earlier blocking issue.

When finished dimensions drift together, first read `build_geometry`,
`measure_finished` and `main` in `examples/surface_shell_build.py`.
For an existing source, move its construction and finishing into
`build_geometry(controls=None)`, returning a fresh `BuildSession` without exporting.
Preserve its intent filename and part/feature IDs; use the example's environment
fallback to resolve that same intent in ordinary Python. Use a local station copy
in both outer and cavity construction, including `station_at`; adapt the control
indices, measured owner and section plane to your model.
Keep structural validity checks in the builder, and final acceptance/export in
`main`, called only under `if __name__ == "__main__"`. Off-target calibration trials
must return actual errors without exporting or triggering final acceptance.

An old contract may lack a typed check for a fixed section target. Retain or add
its missing final-geometry guard before export, after all finishing; skip it in
draft. For example, with module-level imports of `Plane` and `measure_section`,
use your actual owner and fixed brief constants (not station controls):

```python
if not build.is_draft:
    top = measure_section(build.part(PART_NAME), Plane.XY.offset(TOP_PLANE_Z))
    outer = top["outer_envelope"]
    if outer is None or not abs(outer["width_u_mm"] - TOP_OUTER_WIDTH) <= 1e-4:
        raise ValueError(f"Final section must be {TOP_OUTER_WIDTH} mm wide; measured {outer}")
```

Copying an example does not upgrade an old contract. After that original intent
is available, a separate calibration script can start with the following calls
(change the import and control mapping for your source):

```python
import numpy as np
from surface_shell_build import measure_finished, STATIONS, TARGET_ENVELOPE, TOP_OUTER_WIDTH

controls = np.array([STATIONS[2][1], STATIONS[2][2], STATIONS[-1][1]])
targets = np.array([*TARGET_ENVELOPE[:2], TOP_OUTER_WIDTH])
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

Run calibration in its own ordinary Python process, for example
`PYTHONPATH="$AMAGINE3D_SKILL_DIR" python3 calibrate.py`. A failed checked operation
inside a managed draft/compile leaves diagnostics for that run; do not catch it
and publish a later trial as a successful run.

Keep finite-difference probes and proposed changes within physically valid control
bounds. Try this change, then half or a quarter if needed; accept only a reduction
in the Euclidean norm of all target errors. Retest every target
after each accepted update. Keep every trial's controls, measurements and failures,
and cap the experiment at 30 geometry evaluations. Stop on invalid geometry,
singular sensitivity or failure to improve; simplify the construction instead of
enlarging tolerances. Save the three controls into the source's two middle width
entries, middle depth and top width, then run the full public compile. Adapt this
control mapping to the actual model. Numerical convergence says nothing about wall,
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
a3d draft surface_shell_build.py --intent surface_shell_intent.json
```

Inspect the returned preview with `view_image`, develop the source controls,
then run `a3d compile surface_shell_scene.json --intent surface_shell_intent.json --source surface_shell_build.py --output-dir .`.

The intent is written once; the compiler executes the build source. Its
`BuildSession.add` and `cut` retain the outer solid and actual cavity cutter;
`export` derives their scene bindings and exports the final valid solid. STEP is the
manufacturing master; STL, GLB and 3MF are derived outputs. Run the build through
the compiler so its geometry, wall checks and artifacts remain bound together.

Use the current geometry's preview to refine the silhouette, opening and shoulders
through their source controls. The main skill describes current-run previews and
how to continue after compile findings.
