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
        "features": [
            {"id": "shell-surface", "kind": "envelope",
             "evidence": "Proposed demonstration of a section-driven BRep shell, not a product requirement",
             "acceptance": "100 x 80 x 90 mm envelope with rounded rectangular sections and narrower upper/lower shoulders; ruled transitions are acceptable"},
            {"id": "shell-cavity", "kind": "cavity", "face": "top", "direction": "+Z",
             "edge_crossing": "forbidden",
             "evidence": "Proposed open-top hollow-shell construction using a separate inner loft",
             "acceptance": "Real top opening with an annular rim, 3 mm horizontal section inset and a closed 3 mm floor"},
        ],
    }},
    support_policy="support-free", minimum_wall_target_mm=1.6,
    critical_features=["shell-surface", "shell-cavity"], reference_view="isometric",
    landmarks=["rounded rectangular plan", "six sections with independent width and depth",
               "narrower upper and lower shoulders", "slightly offset upper sections", "visible top cavity"],
    assumptions=["Example dimensions are proposed; no installed hardware, closure, drainage or sealing is specified.",
                 "A1 mini with a 0.4 mm nozzle is the proposed fallback printing process.",
                 "Ruled BRep loft transitions prioritize stable solid geometry; tangent continuity is not required.",
                 "Wall control is a 3 mm horizontal section inset, not an exact normal offset of the 3D surface; measured minimum wall thickness must be checked after shape edits."],
)
