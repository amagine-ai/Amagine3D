"""Resolve a pinned Bambu machine, nozzle, process, and tool profile."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


CATALOG_PATH = Path(__file__).with_name("references") / "bambu-profiles.json"


def load_catalog() -> dict:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != "amagine-bambu-profile-catalog/v1":
        raise ValueError("unsupported Bambu profile catalog schema")
    return data


def resolve_machine(catalog: dict, value: str | None) -> tuple[str, dict, bool]:
    assumed = value is None
    requested = value or catalog["default_machine"]
    machines = catalog["machines"]
    normalized = requested.strip().casefold()
    for machine_id, machine in machines.items():
        if normalized in {machine_id.casefold(), machine["name"].casefold()}:
            return machine_id, machine, assumed
    available = ", ".join(sorted(machines))
    raise ValueError(f"unknown Bambu machine {requested!r}; choose one of: {available}")


def resolve_profile(
    catalog: dict,
    *,
    machine_name: str | None,
    nozzle: float,
    tool_index: int,
) -> dict:
    machine_id, machine, assumed = resolve_machine(catalog, machine_name)
    process_key = f"{nozzle:g}"
    process = catalog["processes"].get(process_key)
    if process is None:
        available = ", ".join(sorted(catalog["processes"]))
        raise ValueError(f"unsupported nozzle {nozzle:g} mm; choose one of: {available}")
    tools = {int(item["index"]): item for item in machine["tools"]}
    if tool_index not in tools:
        available = ", ".join(str(index) for index in sorted(tools))
        raise ValueError(
            f"{machine['name']} has no tool {tool_index}; choose one of: {available}"
        )
    tool = tools[tool_index]
    defaults = catalog["process_defaults"]
    outer = float(process["outer_wall_line_width_mm"])
    inner = float(process["inner_wall_line_width_mm"])
    loops = int(process["wall_loops"])
    return {
        "derived": {
            "arachne_min_bead_mm": round(
                nozzle * defaults["arachne_min_bead_nozzle_percent"] / 100, 5
            ),
            "arachne_min_feature_mm": round(
                nozzle * defaults["arachne_min_feature_nozzle_percent"] / 100, 5
            ),
            "process_wall_target_mm": round(outer + inner * max(loops - 1, 0), 5),
            "single_line_floor_mm": outer,
        },
        "id": f"bbl-{machine_id}-{process_key}-t{tool_index}-standard",
        "machine": {
            "bed_polygon_mm": machine["bed_polygon_mm"],
            "excluded_polygons_mm": machine["excluded_polygons_mm"],
            "id": machine_id,
            "name": machine["name"],
            "printable_height_mm": machine["printable_height_mm"],
            "selected_tool": tool,
        },
        "process": {**defaults, **process},
        "schema": "evidence-bambu-printer-profile/v1",
        "selection": {
            "assumed_default_machine": assumed,
            "tool_index": tool_index,
        },
        "upstream": catalog["upstream"],
        "vendor": "Bambu Lab",
    }


def serialize(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", help="machine ID or official Bambu model name")
    parser.add_argument("--nozzle", type=float, default=0.4)
    parser.add_argument("--tool", type=int, default=0)
    parser.add_argument("--list", action="store_true", help="list supported machines")
    parser.add_argument("--out")
    args = parser.parse_args()

    try:
        catalog = load_catalog()
        if args.list:
            result = {
                "default_machine": catalog["default_machine"],
                "machines": [
                    {
                        "id": machine_id,
                        "name": machine["name"],
                        "tools": [item["index"] for item in machine["tools"]],
                    }
                    for machine_id, machine in catalog["machines"].items()
                ],
                "nozzles_mm": [float(value) for value in catalog["processes"]],
                "schema": catalog["schema"],
                "upstream": catalog["upstream"],
            }
            print(serialize(result), end="")
            return 0
        profile = resolve_profile(
            catalog,
            machine_name=args.machine,
            nozzle=args.nozzle,
            tool_index=args.tool,
        )
    except Exception as error:
        print(json.dumps({"error": str(error), "pass": False}, indent=2))
        return 2

    payload = serialize(profile)
    if args.out:
        output = Path(args.out)
        output.write_text(payload, encoding="utf-8")
        artifact = {
            "path": str(output.resolve()),
            "profile_id": profile["id"],
            "sha256": sha256(output.read_bytes()).hexdigest(),
        }
        print(json.dumps(artifact, indent=2))
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
