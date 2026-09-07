"""Run once before compiling surface_shell_build.py; proposed example dimensions."""
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ["AMAGINE3D_SKILL_DIR"])
from authoring import write_intent

ROOT = Path(__file__).resolve().parent
write_intent(
    ROOT / "surface_shell_intent.json",
    profile_path=ROOT / "surface_shell_printer-profile.json",
    part="surface-shell", task_mode="specification", representation="full-3d",
    dimensions_mm={axis: {"value": value, "source": "inferred", "confidence": "medium"}
                   for axis, value in zip("xyz", (100.0, 80.0, 90.0))},
    manufacturing_mode="single-part",
    parts={"surface-shell": {
        "features": [{"id": "shell-surface", "kind": "envelope",
                      "evidence": "Proposed demonstration of a freeform shell, not a product requirement",
                      "acceptance": "100 x 80 x 90 mm envelope, real top cavity, closed 3 mm floor, continuous upper/lower shoulders"}],
    }},
    support_policy="support-free", minimum_wall_target_mm=1.6,
    critical_features=["shell-surface"], reference_view="isometric",
    landmarks=["rounded rectangular plan", "narrower upper and lower shoulders", "visible top cavity"],
    assumptions=["Example dimensions are proposed; no installed hardware, closure, drainage or sealing is specified.",
                 "Wall control is a 3 mm horizontal section inset, not an exact normal offset of the 3D surface."],
)
