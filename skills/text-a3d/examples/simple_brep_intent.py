"""Run once before compile: a one-part BRep API coupon, not a product template."""
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ["AMAGINE3D_SKILL_DIR"])
from authoring import write_intent

ROOT = Path(__file__).resolve().parent
write_intent(
    ROOT / "simple_brep_intent.json",
    profile_path=ROOT / "simple_brep_printer-profile.json",
    part="simple-brep", task_mode="specification", representation="full-3d",
    dimensions_mm={axis: {"value": value, "source": "inferred", "confidence": "medium"}
                   for axis, value in zip("xyz", (40.0, 28.0, 10.0))},
    manufacturing_mode="single-part",
    parts={"simple-brep": {"features": [
        {"id": "body", "kind": "envelope", "evidence": "Proposed API coupon dimensions",
         "acceptance": "One 40 x 28 x 10 mm body with 2 mm edge rounds"},
        {"id": "pocket", "kind": "cavity", "face": "top", "direction": "+Z",
         "edge_crossing": "forbidden", "evidence": "Proposed blind-pocket construction example",
         "acceptance": "Centered 12 mm diameter pocket, 6 mm deep, with a 4 mm floor"},
    ]}},
    support_policy="support-free", minimum_wall_target_mm=1.2,
    critical_features=["body", "pocket"], reference_view="isometric",
    landmarks=["one rounded body", "one centered blind top pocket"],
    assumptions=["Demonstrates a single-part authoring path; its dimensions and pocket are proposed, not a default product architecture."],
)
