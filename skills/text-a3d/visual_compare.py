"""Compare two geometric revisions using one orthographic frame, without alignment."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from cpu_z_buffer import (
    BufferPool, MeshInput, RenderLimits, SUPPORTED_VIEWS, mesh_bounds,
    render_view, triangle_count,
)
from render_preview import _render_inputs


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def compare_revisions(before, after, *, view="front", size=480, limits=RenderLimits()):
    """Return an image and projected occupancy changes in unchanged input coordinates.

    Inputs must share units and a semantic frame. Pixel changes are view-dependent
    observations, not volume differences or an aesthetic/engineering pass score.
    """
    if size < 320:
        raise ValueError("comparison panel size must be at least 320")
    combined = [*before, *after]
    if triangle_count(combined) > limits.max_triangles:
        raise ValueError("combined triangle count exceeds the render limit")
    frame = mesh_bounds(combined)
    panels, masks = [], []
    for inputs in (before, after):
        # A common neutral material makes changes in shape legible regardless of
        # authored colors; the coverage mask comes from depth, not RGB thresholds.
        neutral = [MeshInput(item.name, item.mesh, (122, 163, 199), item.path) for item in inputs]
        pool = BufferPool()
        rendered = render_view(neutral, view, size, limits=limits, pool=pool, frame_bounds=frame)
        panels.append(rendered.image)
        masks.append(np.isfinite(pool.depth))
    old, new = masks
    overlap, removed, added = old & new, old & ~new, new & ~old
    difference = np.full((size, size, 3), 255, dtype=np.uint8)
    difference[overlap] = (180, 188, 194)
    difference[removed] = (210, 68, 69)
    difference[added] = (20, 151, 167)
    panels.append(Image.fromarray(difference))
    header, footer, gutter = 32, 42, 12
    image = Image.new("RGB", (3 * size + 4 * gutter, size + header + footer), "white")
    draw = ImageDraw.Draw(image)
    for index, (panel, label) in enumerate(zip(panels, ("BEFORE", "AFTER", "PROJECTED CHANGE"))):
        left = gutter + index * (size + gutter)
        image.paste(panel, (left, header))
        draw.text((left + 8, 10), label, fill=(30, 40, 50))
    draw.text((gutter + 8, header + size + 8),
              f"{view} | same coordinates and scale | red: before only | teal: after only | gray: both",
              fill=(30, 40, 50))
    union = int((old | new).sum())
    observations = {
        "schema": "a3d-revision-comparison/v1",
        "view": view, "projection": "orthographic", "panelPixels": size,
        "coordinateFrame": "shared-input-coordinates", "alignmentApplied": False,
        "frameBoundsMm": [corner.tolist() for corner in frame],
        "beforeBoundsMm": [corner.tolist() for corner in mesh_bounds(before)],
        "afterBoundsMm": [corner.tolist() for corner in mesh_bounds(after)],
        "projectedPixels": {
            "before": int(old.sum()), "after": int(new.sum()),
            "common": int(overlap.sum()), "beforeOnly": int(removed.sum()),
            "afterOnly": int(added.sum()),
            "intersectionOverUnion": int(overlap.sum()) / union if union else None,
        },
        "scope": "Finite-resolution projected occupancy only; occluded changes may be invisible. No quality pass/fail or 3D volume inference.",
    }
    return image, observations


def _workspace_path(value, workspace):
    path = Path(value).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError(f"path must remain inside the current workspace: {value}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--view", choices=SUPPORTED_VIEWS, default="front")
    parser.add_argument("--size", type=int, default=480)
    parser.add_argument("--out")
    parser.add_argument("--report")
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve()
        sources = [_workspace_path(value, workspace) for value in (args.before, args.after)]
        if any(path.suffix.lower() not in {".stl", ".glb", ".3mf"} for path in sources):
            raise ValueError("compare expects STL, GLB or 3MF in the same coordinate frame and millimetres")
        destination = _workspace_path(args.out or f"{sources[1].stem}_{args.view}_comparison.png", workspace)
        report = _workspace_path(args.report or destination.with_suffix(".json"), workspace)
        outputs = (destination, report)
        aliases = any(output.exists() and source.exists() and output.samefile(source)
                      for output in outputs for source in sources)
        aliases |= destination.exists() and report.exists() and destination.samefile(report)
        if aliases or destination == report or destination in sources or report in sources:
            raise ValueError("comparison outputs must differ from each other and both inputs")
        bindings = [{"path": str(path), "sha256": _digest(path)} for path in sources]
        inputs = [_render_inputs(path, (122, 163, 199)) for path in sources]
        image, result = compare_revisions(*inputs, view=args.view, size=args.size)
        if any(_digest(path) != binding["sha256"] for path, binding in zip(sources, bindings)):
            raise ValueError("a comparison input changed during rendering; retry with frozen revisions")
        destination.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")
        result.update({"before": bindings[0], "after": bindings[1],
                       "preview": {"path": str(destination), "sha256": _digest(destination)}})
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        report.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
