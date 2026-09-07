"""A two-part locating-fit coupon; run once before assembly_build.py is compiled."""
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ["AMAGINE3D_SKILL_DIR"])
from authoring import write_intent

ROOT = Path(__file__).resolve().parent

write_intent(
    ROOT / "assembly_intent.json",
    profile_path=ROOT / "assembly_printer-profile.json",
    part="assembly",
    task_mode="specification",
    representation="full-3d",
    dimensions_mm={
        axis: {"value": value, "source": "inferred", "confidence": "medium"}
        for axis, value in zip("xyz", (30.0, 24.0, 10.0))
    },
    manufacturing_mode="multipart",
    parts={
        "holder": {
            "role": "locating socket coupon",
            "acceptance": "A blind socket with a 2 mm floor supports the pin.",
            "features": [
                {"id": "holder-body", "kind": "envelope", "evidence": "Proposed coupon size", "acceptance": "30 x 24 x 6 mm block"},
                {"id": "socket", "kind": "hole", "face": "top", "direction": "+Z", "edge_crossing": "forbidden", "evidence": "The pin needs an accessible locating socket", "acceptance": "6.4 mm bore, open at the top, floor at Z=2 mm"},
            ],
        },
        "pin": {
            "role": "removable locating pin",
            "acceptance": "An 8 mm straight pin enters from above; 4 mm remains accessible for removal.",
            "features": [{"id": "pin-body", "kind": "interface", "evidence": "Proposed clearance-fit pin", "acceptance": "6 mm diameter, 8 mm long"}],
        },
    },
    interfaces=[{
        "id": "locating-fit", "connection": "pin-socket", "assembly_axis": "-Z",
        "clearances_mm": {"diameter": 0.4}, "engagement_mm": 4.0,
        "features": ["pin-body", "socket"],
        "acceptance": "Pin rests on the socket floor; 0.2 mm radial clearance permits removal. No anti-pullout retention is intended.",
    }],
    color_regions=[
        {"name": part, "part": part, "hex": color, "purpose": "distinguish the coupon parts", "boundary": "whole physical part", "evidence": "Proposed whole-part colors", "continuity": "separate-part"}
        for part, color in (("holder", "#D8D2C8"), ("pin", "#277DA1"))
    ],
    palette_reduction={"applied": False, "reason": "One material per separate part"},
    support_policy="support-free",
    minimum_wall_target_mm=1.2,
    critical_features=["socket", "pin-body"],
    reference_view="isometric",
    landmarks=["one centered pin protrudes above a rectangular holder"],
    assumptions=["A demonstration of a removable locating fit, not a retained control or service enclosure; dimensions are proposed for this coupon."],
)
