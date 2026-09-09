# BRep loft shell example

Adapt `examples/surface_shell_intent.py` and `examples/surface_shell_build.py`
for the requested proportions and mechanisms; `surface-design.md` explains the
construction choices. Keep interfaces on their owning BRep parts.

Each `STATIONS` row controls height, width, depth, corner radius and center.
Outer and inner lofts form the cavity, rim and floor. `WALL_INSET` and `FLOOR`
control section inset and base thickness. An inset does not guarantee normal
wall thickness on slopes; check actual walls and shoulder overhang after edits.

The example initially declares ±0.1 mm ranges around its nominal 100×80×90 mm
envelope and 82 mm outer width at z=90. These are ordinary non-fit modeling
allowances, not printer accuracy; explicit user ranges and strict requirements
take precedence. Keep existing contracts fixed during repairs. The section check
measures the owner's outer envelope at the declared plane, not a hole, passage
or wall. Final compile checks the bound STEP after finishing; draft is provisional.

When finished dimensions drift together, first read `build_geometry`,
`measure_finished` and `main` in `examples/surface_shell_build.py`.
For an existing source, move its construction and finishing into
`build_geometry(controls=None)`, returning a fresh `BuildSession` without exporting.
Preserve its intent filename and part/feature IDs. Use a local station copy in
both outer and cavity construction, including `station_at`; adapt the control
indices, measured owner and section plane. Keep structural checks in the builder
and final acceptance/export in `main`, under `if __name__ == "__main__"`.
Calibration returns measurements without exporting or applying final acceptance.

For a legacy target without a typed section check, retain or add a final guard
after finishing, before export. Set `TOP_WIDTH_MIN/MAX` from the agreed requirement,
equal for a strict fixed target; never derive them from station controls or widen
them during repair. Use the actual owner/plane and module-level imports of `Plane`,
`measure_section`, and `DEFAULT_DIMENSION_PRECISION_MM` from `intent_contract`.
For an explicitly stricter requirement, use its declared precision in the guard:

```python
if not build.is_draft:
    outer = measure_section(build.part(PART_NAME), Plane.XY.offset(TOP_PLANE_Z))["outer_envelope"]
    eps = DEFAULT_DIMENSION_PRECISION_MM  # ordinary 0.01 mm; tighten when explicitly required
    if outer is None or not TOP_WIDTH_MIN - eps <= outer["width_u_mm"] <= TOP_WIDTH_MAX + eps:
        raise ValueError(f"Final section requires {TOP_WIDTH_MIN}..{TOP_WIDTH_MAX} mm; measured {outer}")
```

Copying the example does not change an old contract. This separate calibration
script reads its original ranges, stops when satisfied, and otherwise proposes
one bounded update. Adapt the file, feature, plane and control mapping together:

```python
import json, os
from pathlib import Path
import numpy as np
from intent_contract import dimension_limits, dimension_measurement_precision_mm
from surface_shell_build import measure_finished, STATIONS, TOP_PLANE_Z

intent = json.loads(Path(os.environ.get("AMAGINE3D_INTENT_PATH") or "surface_shell_intent.json").read_text())
section = next(f for f in intent["features"] if f["id"] == "shell-surface")["section_dimensions"][0]
if section["plane"] != {"axis": "z", "coordinate_mm": TOP_PLANE_Z}:
    raise ValueError("Match the callback's measured plane to the original contract")
metrics = [intent["dimensions_mm"][a] for a in "xy"] + [section["outer_envelope"]["width_u_mm"]]
lower, upper = np.array([dimension_limits({"dimensions_mm": {"x": m}}, "x",
                                         dimension_measurement_precision_mm(m)) for m in metrics]).T
controls = np.array([STATIONS[2][1], STATIONS[2][2], STATIONS[-1][1]])
actual = measure_finished(controls)  # rebuild; return measured dimensions as an array
if not np.isfinite(actual).all():
    raise ValueError("Missing finite measurements")
error = actual - np.clip(actual, lower, upper)
if not np.any(error):
    print("Calibrated dimensions are within the agreed bounds; run final compile.")
    raise SystemExit(0)
h = 0.02  # mm; choose a small finite perturbation for these length controls
jacobian = np.column_stack([
    (measure_finished(controls + np.eye(3)[i] * h) - actual) / h
    for i in range(3)
])
# Aim only violated dimensions toward nominal values, away from range edges.
proposal_error = np.where(error != 0, actual - np.array([m["value"] for m in metrics]), 0.0)
change = np.linalg.solve(jacobian, -proposal_error)
change *= min(1.0, 2.0 / max(np.max(np.abs(change)), 1e-12))
```

Run with `PYTHONPATH="$AMAGINE3D_SKILL_DIR" python3 calibrate.py` in a separate
ordinary process. Never catch a failed managed draft/compile operation and publish
a later trial as that run's success.

Keep probes and updates physically valid. Try the change, then half or a quarter;
accept only a reduction in the norm of `actual - np.clip(actual, lower, upper)`.
Remeasure every calibrated dimension; stop inside all intervals, including strict ones;
do not chase nominal values inside a range. Preserve raw measurements, controls
and failures; cap at 30 evaluations and stop on invalid geometry, singularity or
no improvement. Save accepted controls into the two middle widths, middle depth
and top width, then compile. Range satisfaction proves no wall, foot, fit or
visual quality; those checks remain required.

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

Write intent once. `BuildSession.add/cut` retain the actual outer solid and cavity
cutter; `export` derives their bindings. Compile binds the STEP master, derived
meshes and audits. Refine silhouette, opening and shoulders from the current
preview, then inspect the new compile findings.
