"""Proposed final dimensions; section count and inset remain build parameters."""
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
    dimensions_mm={axis: {"value": value, "source": "inferred", "confidence": "medium",
                          "constraint": {"kind": "range", "min_mm": value - 0.1, "max_mm": value + 0.1}}
                   for axis, value in zip("xyz", (100.0, 80.0, 90.0))},
    manufacturing_mode="single-part",
    parts={"surface-shell": {
        "features": [
            {"id": "shell-surface", "kind": "envelope",
             "section_dimensions": [{
                 "plane": {"axis": "z", "coordinate_mm": 90.0},
                 "outer_envelope": {"width_u_mm": {"value": 82.0,
                     "constraint": {"kind": "range", "min_mm": 81.9, "max_mm": 82.1}}},
             }],
             "evidence": "Proposed demonstration of a section-driven BRep shell, not a product requirement",
             "acceptance": "100 x 80 x 90 mm envelope with rounded rectangular sections and narrower upper/lower shoulders; ruled transitions are acceptable"},
            {"id": "shell-cavity", "kind": "cavity", "face": "top", "direction": "+Z",
             "edge_crossing": "forbidden",
             "evidence": "Proposed open-top hollow shell with a closed base",
             "acceptance": "Real top opening with an annular rim, 82 mm outer width at z=90 mm, at least 1.6 mm walls and a closed 3 mm floor"},
        ],
    }},
    support_policy="support-free", minimum_wall_target_mm=1.6,
    critical_features=["shell-surface", "shell-cavity"], reference_view="isometric",
    landmarks=["rounded rectangular plan", "wider middle body",
               "narrower upper and lower shoulders", "slightly offset upper sections", "visible top cavity"],
    assumptions=["Example dimensions are proposed; no installed hardware, closure, drainage or sealing is specified.",
                 "The envelope and top width allow +/-0.1 mm modeling deviation from nominal, not a guarantee of printer accuracy; wall and floor minima are unchanged.",
                 "A1 mini with a 0.4 mm nozzle is the proposed fallback printing process.",
                 "Tangent continuity is not required in this demonstration.",
                 "Measured minimum wall thickness must be checked after shape edits; section inset alone is not proof of normal wall thickness."],
)
