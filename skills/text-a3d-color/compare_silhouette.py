"""Compare reference and rendered silhouettes without using model claims."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image


def _mask(path: Path, threshold: float) -> np.ndarray:
    rgba = np.asarray(Image.open(path).convert("RGBA"))
    alpha = rgba[:, :, 3]
    if alpha.min() < 250 and np.any(alpha > 16) and np.any(alpha <= 16):
        mask = alpha > 16
    else:
        rgb = rgba[:, :, :3]
        border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
        background, _ = Counter(map(tuple, border.tolist())).most_common(1)[0]
        distance = np.linalg.norm(rgb.astype(float) - np.array(background), axis=2)
        mask = distance > threshold
    if not np.any(mask):
        raise ValueError(f"no foreground found in {path}")
    return mask


def _normalized(mask: np.ndarray, size: int = 512, margin: int = 16):
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = crop.shape
    scale = min((size - 2 * margin) / width, (size - 2 * margin) / height)
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    resized = Image.fromarray((crop * 255).astype(np.uint8)).resize(
        (new_width, new_height), Image.Resampling.NEAREST,
    )
    canvas = np.zeros((size, size), dtype=bool)
    left = (size - new_width) // 2
    top = (size - new_height) // 2
    canvas[top:top + new_height, left:left + new_width] = np.asarray(resized) > 0
    return canvas, width / height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("render")
    parser.add_argument("--out", help="Optional JSON report path")
    parser.add_argument("--overlay", help="Optional red/cyan/white overlay PNG")
    parser.add_argument("--background-threshold", type=float, default=24.0)
    parser.add_argument("--min-iou", type=float, default=0.75)
    args = parser.parse_args()

    try:
        reference, reference_aspect = _normalized(
            _mask(Path(args.reference), args.background_threshold),
        )
        render, render_aspect = _normalized(
            _mask(Path(args.render), args.background_threshold),
        )
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 2

    intersection = int(np.logical_and(reference, render).sum())
    union = int(np.logical_or(reference, render).sum())
    reference_pixels = int(reference.sum())
    render_pixels = int(render.sum())
    iou = intersection / union if union else 0.0
    precision = intersection / render_pixels if render_pixels else 0.0
    recall = intersection / reference_pixels if reference_pixels else 0.0
    passed = iou >= args.min_iou
    result = {
        "pass": passed,
        "iou": round(iou, 6),
        "min_iou": args.min_iou,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "reference_aspect": round(reference_aspect, 6),
        "render_aspect": round(render_aspect, 6),
        "aspect_delta": round(abs(reference_aspect - render_aspect), 6),
        "note": "Silhouette comparison only; color, depth, and semantic landmarks still require visual review.",
    }

    if args.overlay:
        overlay = np.zeros((reference.shape[0], reference.shape[1], 3), dtype=np.uint8)
        overlay[reference] = [255, 64, 64]
        overlay[render] = [64, 220, 255]
        overlay[np.logical_and(reference, render)] = [255, 255, 255]
        Image.fromarray(overlay).save(args.overlay)
        result["overlay"] = args.overlay

    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
