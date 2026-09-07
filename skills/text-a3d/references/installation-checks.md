# Installed-component geometry checks

Use `a3d capabilities --symbol check_installation` for the callable signature.
This optional helper accepts build123d solids/compounds and closed trimesh meshes
in semantic assembly coordinates. It chooses no dimensions, mounting method or
part split. Call it on the finished receiving geometry before export.

```python
from installation_check import check_installation

check_installation(
    pcb_envelope, {"tray": tray},
    insertion_envelope=pcb_insertion_sweep,
    supports={"seat": tray},
    retainers={"lid": lid},
    withdrawal_axis=(0, 0, 1),
    free_travel_mm=0.29, stop_travel_mm=0.35,
    out_path=OUT / "pcb-installation.json",
)
```

The example's travel values are illustrative, not PCB defaults. The support
probe moves the component slightly opposite withdrawal; each named support must
make contact. Retainers must leave the declared free travel clear and stop the
component at the declared stop travel. The helper records measured overlap and
raises on failure after saving the optional report. Omit support or retention
checks when that relationship is not needed; omitted checks are not claimed.

Author the swept envelope from the actual installation path. For a rectangular
component moving along Z, a box spanning its seated bottom to its entry top is
an exact envelope; a bounding-box sweep of a nonrectangular component is only a
conservative approximation. Use a shaped or segmented swept volume when that
approximation collides but the real part can pass. Check USB cable approach with
its own plug/swept envelope. Clearance alone proves neither support nor a path.

Contact probes establish geometry, not clamping force, compliant behavior or
strength. Keep the supplied envelope dimensions and their assumptions in the
existing intent evidence. Purchased components remain excluded from manufacturing
exports. No new intent/scene schema or mandatory checklist is required.
