"""Render internal color-region meshes as orthographic, hash-bound evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import tracemalloc

from cpu_z_buffer import (
    CONTACT_VIEWS,
    DEFAULT_MAX_RESOLUTION,
    DEFAULT_OUTPUT_SIZE,
    DEFAULT_MAX_TRIANGLES,
    HARD_MAX_RESOLUTION,
    HARD_MAX_TRIANGLES,
    MAX_SUPERSAMPLE,
    SUPPORTED_VIEWS,
    MeshInput,
    RenderLimits,
    load_mesh,
    mesh_bounds,
    render_contact_sheet,
    render_view,
    triangle_count,
)


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Region:
    name: str
    path: Path
    color: str
    render_input: MeshInput


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rgb(value: str) -> tuple[int, int, int]:
    if not HEX.fullmatch(value):
        raise ValueError(f"invalid region color {value!r}; expected #RRGGBB")
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def _region_name(path: Path) -> str:
    marker = "-region-"
    stem = path.stem
    if marker in stem:
        return stem.rsplit(marker, 1)[1]
    return stem


def _region(specification: str) -> Region:
    filename, separator, color = specification.rpartition("=")
    if not separator or not filename:
        raise ValueError(f"bad --part value {specification!r}; expected path.stl=#RRGGBB")
    path = Path(filename).resolve()
    mesh = load_mesh(path)
    normalized_color = color.upper()
    name = _region_name(path)
    render_input = MeshInput(name, mesh, _rgb(normalized_color), path)
    return Region(name, path, normalized_color, render_input)


def _save_png(image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=DEFAULT_OUTPUT_SIZE)
    parser.add_argument(
        "--supersample",
        type=int,
        choices=range(1, MAX_SUPERSAMPLE + 1),
        default=1,
        help="Render at 1x (default) or 2x, then downsample with Pillow",
    )
    parser.add_argument(
        "--max-resolution",
        type=int,
        default=DEFAULT_MAX_RESOLUTION,
        help=f"Internal per-view pixel limit (hard maximum {HARD_MAX_RESOLUTION})",
    )
    parser.add_argument(
        "--max-triangles",
        type=int,
        default=DEFAULT_MAX_TRIANGLES,
        help=f"Input triangle limit (hard maximum {HARD_MAX_TRIANGLES})",
    )
    parser.add_argument("--reference-view", choices=SUPPORTED_VIEWS)
    parser.add_argument("--reference-out")
    parser.add_argument("--report", help="Optional JSON evidence report")
    args = parser.parse_args()
    if bool(args.reference_view) != bool(args.reference_out):
        parser.error("--reference-view and --reference-out must be used together")
    if args.size < 320:
        parser.error("--size must be at least 320")

    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        limits = RenderLimits(
            max_resolution=args.max_resolution,
            max_triangles=args.max_triangles,
        )
        if args.reference_view and args.size * args.supersample > limits.max_resolution:
            raise ValueError(
                "matched-view internal resolution exceeds the configured maximum "
                f"of {limits.max_resolution} pixels"
            )
        regions = [_region(item) for item in args.part]
        inputs = [region.render_input for region in regions]
        count = triangle_count(inputs)
        if count > limits.max_triangles:
            raise ValueError(
                f"meshes have {count} triangles; configured maximum is "
                f"{limits.max_triangles}"
            )
        destination = Path(args.out).resolve()
        low, high = mesh_bounds(inputs)
        dimensions = high - low
        palette = "  ".join(region.color for region in regions)
        contact = render_contact_sheet(
            inputs,
            args.size,
            title=(
                f"{len(regions)} regions  |  {dimensions[0]:.2f} x "
                f"{dimensions[1]:.2f} x {dimensions[2]:.2f} mm  |  {palette}"
            ),
            supersample=args.supersample,
            limits=limits,
        )
        _save_png(contact.image, destination)

        matched_stats = None
        matched_path = None
        if args.reference_view:
            matched_path = Path(args.reference_out).resolve()
            matched = render_view(
                inputs,
                args.reference_view,
                args.size,
                supersample=args.supersample,
                limits=limits,
            )
            _save_png(matched.image, matched_path)
            matched_stats = matched.stats

        _, traced_peak = tracemalloc.get_traced_memory()
        peak_buffer_bytes = max(
            contact.peak_buffer_bytes,
            matched_stats.buffer_bytes if matched_stats is not None else 0,
        )
        result = {
            "dimensions_mm": [round(float(value), 4) for value in dimensions],
            "performance": {
                "contact_sheet_seconds": round(contact.elapsed_seconds, 6),
                "parallel_views": False,
                "peak_memory_bytes": max(int(traced_peak), peak_buffer_bytes),
                "processes": 1,
                "supersample": args.supersample,
                "triangle_count": count,
                "view_count": len(CONTACT_VIEWS),
                "views": {stat.view: stat.to_dict() for stat in contact.stats},
            },
            "preview": {
                "path": str(destination),
                "sha256": _digest(destination),
            },
            "projection": "orthographic",
            "regions": [
                {
                    "color": region.color,
                    "dimensions_mm": [
                        round(float(value), 4)
                        for value in region.render_input.mesh.extents
                    ],
                    "name": region.name,
                    "path": str(region.path),
                    "sha256": _digest(region.path),
                    "watertight": bool(region.render_input.mesh.is_watertight),
                }
                for region in regions
            ],
            "renderer": "cpu-z-buffer/v1",
            "schema": "evidence-color-render/v2",
            "supported_views": list(SUPPORTED_VIEWS),
            "views": list(CONTACT_VIEWS),
        }
        if matched_path is not None and matched_stats is not None:
            result["matched_view"] = {
                "name": args.reference_view,
                "path": str(matched_path),
                "sha256": _digest(matched_path),
            }
            result["performance"]["single_view_seconds"] = round(
                matched_stats.elapsed_seconds, 6
            )
        payload = json.dumps(result, indent=2)
        if args.report:
            report = Path(args.report).resolve()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
    finally:
        if not tracing_was_active:
            tracemalloc.stop()


if __name__ == "__main__":
    raise SystemExit(main())
