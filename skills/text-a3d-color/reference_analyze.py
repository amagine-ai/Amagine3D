"""Extract an independent, deterministic contract from a reference image."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from math import gcd
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image


def _foreground_mask(rgba: np.ndarray) -> tuple[np.ndarray, str, list[int]]:
    alpha = rgba[:, :, 3]
    if alpha.min() < 250 and np.any(alpha > 16) and np.any(alpha <= 16):
        return alpha > 16, "transparent", [0, 0, 0, 0]
    rgb = rgba[:, :, :3]
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    background, _ = Counter(map(tuple, border.tolist())).most_common(1)[0]
    distance = np.linalg.norm(rgb.astype(float) - np.array(background), axis=2)
    return distance > 18, "border-color", [*map(int, background), 255]


def _run_gcd(values: np.ndarray) -> int:
    runs: list[int] = []
    for line in values:
        changes = np.flatnonzero(np.diff(line.astype(np.int8))) + 1
        lengths = np.diff(np.concatenate(([0], changes, [len(line)])))
        runs.extend(int(length) for length in lengths if length > 1)
    result = 0
    for length in runs:
        result = gcd(result, length)
    return result


def _pixel_grid(
    mask: np.ndarray, rgb: np.ndarray, bbox: list[int], color_count: int,
) -> dict | None:
    x, y, width, height = bbox
    crop = mask[y:y + height, x:x + width]
    scale = gcd(_run_gcd(crop), _run_gcd(crop.T))
    if color_count > 64:
        return None
    if scale < 1:
        scale = 1 if max(width, height) <= 64 else 0
    if scale < 1 or (scale == 1 and max(width, height) > 64):
        return None
    grid_w = int(np.ceil(width / scale))
    grid_h = int(np.ceil(height / scale))
    occupied: list[list[int]] = []
    cells: list[dict] = []
    for row in range(grid_h):
        for col in range(grid_w):
            cell = crop[
                row * scale:min((row + 1) * scale, height),
                col * scale:min((col + 1) * scale, width),
            ]
            if cell.size and float(cell.mean()) >= 0.5:
                occupied.append([col, row])
                cell_rgb = rgb[
                    y + row * scale:y + min((row + 1) * scale, height),
                    x + col * scale:x + min((col + 1) * scale, width),
                ]
                color, _ = Counter(
                    map(tuple, cell_rgb[cell].tolist()),
                ).most_common(1)[0]
                cells.append({
                    "col": col,
                    "hex": "#%02X%02X%02X" % color,
                    "row": row,
                })
    return {
        "cell_px": scale,
        "cells": cells,
        "height_cells": grid_h,
        "occupied_cells": occupied,
        "origin_px": [x, y],
        "width_cells": grid_w,
    }


def analyze(path: Path) -> dict:
    raw = path.read_bytes()
    with Image.open(path) as source:
        rgba = np.asarray(source.convert("RGBA"))
        source_mode = source.mode
    height, width = rgba.shape[:2]
    mask, background_method, background = _foreground_mask(rgba)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ValueError("reference image has no foreground")
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bbox = [x0, y0, x1 - x0, y1 - y0]
    colors = Counter(map(tuple, rgba[:, :, :3][mask].tolist()))
    grid = _pixel_grid(mask, rgba[:, :, :3], bbox, len(colors))
    palette = [
        {"hex": "#%02X%02X%02X" % color, "pixels": count}
        for color, count in colors.most_common(16)
    ]
    return {
        "background": {"method": background_method, "rgba": background},
        "foreground": {
            "aspect_ratio": round(bbox[2] / bbox[3], 6),
            "bbox_px": bbox,
            "coverage": round(float(mask.mean()), 6),
        },
        "image": {
            "aspect_ratio": round(width / height, 6),
            "height_px": height,
            "mode": source_mode,
            "sha256": sha256(raw).hexdigest(),
            "width_px": width,
        },
        "mode": "pixel-art" if grid else "general-image",
        "palette": palette,
        "palette_unique_colors": len(colors),
        **({"pixel_grid": grid} if grid else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--out", help="Optional JSON report path")
    args = parser.parse_args()
    try:
        result = analyze(Path(args.image))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 2
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
