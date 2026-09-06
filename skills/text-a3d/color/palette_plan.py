"""Reduce an analyzed reference palette into a deterministic printable palette."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np


def rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array([int(value[index:index + 2], 16) for index in (0, 2, 4)], dtype=float)


def distance(left: str, right: str) -> float:
    # Weighted RGB is deterministic and adequate for filament shortlist planning.
    delta = (rgb(left) - rgb(right)) * np.array([0.30, 0.59, 0.11])
    return float(np.linalg.norm(delta))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", help="JSON from reference_analyze.py")
    parser.add_argument("--max-colors", type=int, default=4)
    parser.add_argument("--keep", action="append", default=[])
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.max_colors < 2:
        parser.error("--max-colors must be at least 2")

    analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    source = [
        {"hex": item["hex"].upper(), "pixels": int(item["pixels"])}
        for item in analysis.get("palette", [])
    ]
    if not source:
        print(json.dumps({"error": "analysis contains no palette"}))
        return 2

    available = {item["hex"] for item in source}
    selected = []
    for value in args.keep:
        value = value.upper()
        if value not in available:
            print(json.dumps({"error": f"kept color is absent: {value}"}))
            return 2
        if value not in selected:
            selected.append(value)
    if len(selected) > args.max_colors:
        print(json.dumps({"error": "kept colors exceed --max-colors"}))
        return 2

    if not selected:
        selected.append(max(source, key=lambda item: item["pixels"])["hex"])
    while len(selected) < min(args.max_colors, len(source)):
        candidates = [item for item in source if item["hex"] not in selected]
        winner = max(
            candidates,
            key=lambda item: item["pixels"] * min(
                distance(item["hex"], chosen) ** 2 for chosen in selected
            ),
        )
        selected.append(winner["hex"])

    assignments = []
    totals = {value: 0 for value in selected}
    for item in source:
        target = min(selected, key=lambda value: distance(item["hex"], value))
        totals[target] += item["pixels"]
        assignments.append({
            "distance": round(distance(item["hex"], target), 4),
            "pixels": item["pixels"],
            "source": item["hex"],
            "target": target,
        })
    result = {
        "assignments": assignments,
        "method": "weighted-farthest-first-then-nearest",
        "palette": [
            {"hex": value, "mapped_pixels": totals[value]} for value in selected
        ],
        "pass": True,
        "schema": "filament-palette-plan/v2",
        "source_color_count": len(source),
        "target_color_count": len(selected),
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
