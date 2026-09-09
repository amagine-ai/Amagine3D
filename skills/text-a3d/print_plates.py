"""Shared plate inventory for export, validation and per-plate audits.

Legacy stl/3mf references identify the first plate. Each part's plate-print
transform is local to the plate that owns it; plates never share one mesh.
"""

import math


def print_plates(report):
    if not isinstance(report, dict):
        return []
    data = report.get("backendData", {})
    if not isinstance(data, dict):
        return []
    if "printPlates" in data:
        plates = data["printPlates"]
        return [p for p in plates if isinstance(p, dict)] if isinstance(plates, list) else []
    parts = report.get("parts", {})
    return [{"id": "01", "parts": sorted(parts) if isinstance(parts, dict) else [],
             "stlKey": "stl", "threeMfKey": "3mf", "geometry": data.get("printPlate")}]


def plate_for_artifact(report, key):
    return next((p for p in print_plates(report) if key is not None and key in (p.get("stlKey"), p.get("threeMfKey"))), None)


def plate_collection_errors(report, geometry_errors):
    data = report.get("backendData", {})
    if not isinstance(data, dict) or "printPlates" not in data:
        return []
    plates = data["printPlates"]
    if report.get("backend") != "brep-assembly" or not isinstance(plates, list) or len(plates) < 2:
        return ["backendData.printPlates requires at least two BRep assembly plates"]
    errors, seen, paths = [], [], []
    parts = report.get("parts", {})
    artifacts = report.get("artifacts", {})
    if not isinstance(parts, dict) or not isinstance(artifacts, dict):
        return ["printPlates requires valid parts and artifacts"]
    for index, plate in enumerate(plates, 1):
        label = f"backendData.printPlates[{index - 1}]"
        pid = f"{index:02d}"
        if not isinstance(plate, dict) or set(plate) != {"id", "parts", "stlKey", "threeMfKey", "geometry"}:
            errors.append(f"{label} has invalid fields")
            continue
        expected_stl = "stl" if index == 1 else f"plate:{pid}:stl"
        expected_3mf = "3mf" if index == 1 else f"plate:{pid}:3mf"
        if (plate["id"], plate["stlKey"], plate["threeMfKey"]) != (pid, expected_stl, expected_3mf):
            errors.append(f"{label} must use ordered IDs and matching artifact keys")
        owners = plate["parts"]
        if not isinstance(owners, list) or not owners or not all(isinstance(p, str) and p in parts for p in owners):
            errors.append(f"{label}.parts must identify manufactured parts")
            continue
        seen.extend(owners)
        geometry = plate["geometry"]
        fields = {"bodyCount", "boundsMm", "isVolume", "valid", "volumeMm3"}
        if not isinstance(geometry, dict) or set(geometry) != fields | {"layout"}:
            errors.append(f"{label}.geometry has invalid fields")
            continue
        errors.extend(geometry_errors({k: geometry[k] for k in fields}, label + ".geometry"))
        if geometry["bodyCount"] != len(owners):
            errors.append(f"{label} solid count differs from its part inventory")
        layout = geometry["layout"]
        if not isinstance(layout, dict) or not isinstance(layout.get("transforms"), dict) or set(layout["transforms"]) != set(owners):
            errors.append(f"{label}.layout must cover exactly its parts")
        for key in (expected_stl, expected_3mf):
            ref = artifacts.get(key, {})
            if not isinstance(ref, dict):
                ref = {}
            if ref.get("coordinateFrame") != "plate-print":
                errors.append(f"{label} lacks a bound plate-print artifact {key}")
            if key == expected_3mf and (ref.get("verified") is not True or ref.get("validator") != "lib3mf"):
                errors.append(f"{label} 3MF must be verified by lib3mf")
            if isinstance(ref.get("path"), str):
                paths.append(ref["path"])
            else:
                errors.append(f"{label} artifact {key} has an invalid path")
        volumes = [parts[p].get("semantic", {}).get("volumeMm3")
                   if isinstance(parts[p], dict) and isinstance(parts[p].get("semantic"), dict) else None
                   for p in owners] + [geometry["volumeMm3"]]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in volumes):
            errors.append(f"{label} requires finite geometry volumes")
            continue
        expected_volume = sum(volumes[:-1])
        if abs(geometry["volumeMm3"] - expected_volume) > max(0.001, expected_volume * 1e-6):
            errors.append(f"{label} volume differs from its assigned parts")
    if sorted(seen) != sorted(parts):
        errors.append("printPlates must cover each manufactured part exactly once")
    if len(set(paths)) != len(paths):
        errors.append("printPlates must use distinct artifact files")
    if not isinstance(plates[0], dict) or plates[0].get("geometry") != data.get("printPlate"):
        errors.append("printPlate must match the first printPlates geometry")
    return errors
