"""Write the fixed fixture brief after exploring installed_module_build.py.

Only interface construction metadata comes from P. Overall and purchased-module
requirements remain independently specified here when geometry controls change.
"""
import os
from pathlib import Path
import sys

from installed_module_build import P

ROOT = Path(__file__).resolve().parent


def feature(id, kind, acceptance, **fields):
    return {"id": id, "kind": kind, "evidence": "Proposed installation fixture relationship", "acceptance": acceptance, **fields}


def main():
    sys.path.insert(0, os.environ["AMAGINE3D_SKILL_DIR"])
    from authoring import write_intent

    frame_features = [
        feature("frame-body", "envelope", "One continuous rectangular receiving frame with a viewing-face shoulder."),
        feature("module-space", "cavity", "The independently specified width 50 x height 30 x depth 5 mm module fits behind the viewing-face shoulder and inserts from the rear (+Y) before the cover is fitted.",
                face="back", direction="+Y", edge_crossing="forbidden",
                installation_checks=["clearance", "insertion", "support", "retention", "passage"]),
        feature("viewing-window", "hole", "An unobstructed optical passage connects the outside to the module face; no printed screen fills it.",
                face="front", direction="-Y", edge_crossing="forbidden"),
        feature("frame-locator", "interface", "A socket locates the removable cover independently of its screws.", face="back", direction="+Y", edge_crossing="forbidden"),
    ]
    cover_features = [
        feature("cover-body", "envelope", "A complete removable rear closure supports two internal retaining pads."),
        feature("cover-collar", "interface", "One hollow locating collar enters the frame socket."),
        feature("module-retainers", "interface", f"Pads restrict module withdrawal to {P['retainer_gap']:g} mm when the cover is fastened."),
    ]
    fasteners = []
    for index in range(4):
        name = f"corner-{index + 1}"
        clearance, pilot, boss = f"cover-{name}", f"frame-{name}-pilot", f"frame-{name}-boss"
        cover_features.append(feature(clearance, "hole", "An accessible through hole clears the screw shank.", face="back", direction="through-Y", edge_crossing="forbidden"))
        frame_features.extend([
            feature(pilot, "hole", "A coaxial blind pilot accepts the proposed plastics screw.", face="back", direction="+Y", edge_crossing="forbidden"),
            feature(boss, "interface", "Continuous receiver material surrounds the blind pilot."),
        ])
        fasteners.append({"id": name, "clearance_feature": clearance, "pilot_feature": pilot, "boss_feature": boss})

    fastening = {key: value for key, value in P["fastening"].items() if key != "engagement_mm"}
    fastening.update({
        "cover_thickness_mm": P["cover_thickness"],
        "locator_pairs": [{"id": "cover-location", "male_feature": "cover-collar", "female_feature": "frame-locator"}],
        "fasteners": fasteners,
    })
    under_head_length = (P["cover_thickness"] - (P["fastening"].get("head_recess_depth_mm") or 0.0)
                         + P["fastening"]["engagement_mm"])
    write_intent(
        ROOT / "installed_module_intent.json",
        profile_path=ROOT / "installed_module_printer-profile.json", part="installed-module",
        task_mode="specification", representation="full-3d", manufacturing_mode="multipart",
        dimensions_mm={axis: {"value": value, "source": "inferred", "confidence": "medium"}
                       for axis, value in zip("xyz", (80.0, 16.0, 60.0))},
        parts={
            "frame": {"role": "receiving window frame", "acceptance": "Window, module space, viewing-face support and screw receivers form one part.", "features": frame_features},
            "cover": {"role": "removable rear cover", "acceptance": "A located and screwed closure retains the purchased module.", "features": cover_features},
        },
        interfaces=[{
            "id": "cover-fastening", "between": ["cover", "frame"], "connection": "self-tapping-screw",
            "assembly_axis": "-Y", "engagement_mm": P["fastening"]["engagement_mm"],
            "features": ["cover-collar", "frame-locator"] + [value for item in fasteners for key, value in item.items() if key != "id"],
            "fastening": fastening,
            "acceptance": "The collar controls lateral position; four coaxial screw pairs clamp the rear cover. Remove the cover before inserting or withdrawing the module.",
        }],
        color_regions=[{"name": part, "part": part, "hex": color, "purpose": "distinguish manufactured parts",
                        "boundary": "whole physical part", "evidence": "Proposed fixture colors", "continuity": "separate-part"}
                       for part, color in (("frame", "#D8D2C8"), ("cover", "#536D78"))],
        palette_reduction={"applied": False, "reason": "One material per manufactured part"},
        support_policy="supports-allowed", minimum_wall_target_mm=1.2,
        critical_features=["module-space", "viewing-window", "module-retainers"], reference_view="isometric",
        landmarks=["A front (-Y) rectangular viewing window in front of a module installed from the rear (+Y); a separate complete rear closure."],
        assumptions=[
            "This example brief fixes the assembly at width 80 x height 60 x depth 16 mm, with bottom z=0, and the module at width 50 x height 30 x depth 5 mm. Construction parameter edits do not revise these requirements.",
            "The required module envelope is width 50 x height 30 x depth 5 mm. Adapting to another supplier component requires independently revising its specification and active area, not copying dimensions from generated geometry.",
            "The optional module reference is purchased hardware, excluded from all manufacturing geometry; no dummy lens is required.",
            f"Proposed plastics screws require an under-head length of {under_head_length:g} mm for {P['fastening']['engagement_mm']:g} mm engagement while preserving {P['fastening']['pilot_tip_clearance_mm']:g} mm tip clearance. If supplier hardware has a different length, redesign the receiver and revalidate the fit for the actual screw and material.",
            "Installation checks establish authored clearance, optical passage and geometric stops, not electrical operation, fastening strength or clamping force.",
        ],
    )


if __name__ == "__main__":
    main()
