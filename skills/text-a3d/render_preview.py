"""Create orthographic visual evidence for single-material STL or GLB files."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tracemalloc

from cpu_z_buffer import (
    CONTACT_VIEWS,
    DEFAULT_MATERIAL,
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


PART_TINTS = (
    DEFAULT_MATERIAL,
    (104, 145, 181),
    (140, 174, 205),
    (92, 126, 158),
    (156, 187, 214),
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _save_png(image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?")
    parser.add_argument(
        "--part",
        action="append",
        help="STL or GLB to include in a single-material multipart preview",
    )
    parser.add_argument("--out")
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
    if args.model and args.part:
        parser.error("use either the positional model or repeated --part inputs")
    if not args.model and not args.part:
        parser.error("provide a positional model or at least one --part")
    if args.part and len(args.part) < 2:
        parser.error("multipart preview requires at least two --part inputs")
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
        sources = [Path(item).resolve() for item in (args.part or [args.model])]
        destination = Path(
            args.out or sources[0].with_name(f"{sources[0].stem}_views.png")
        ).resolve()
        inputs = [
            MeshInput(
                source.stem,
                load_mesh(source),
                PART_TINTS[index % len(PART_TINTS)],
                source,
            )
            for index, source in enumerate(sources)
        ]
        count = triangle_count(inputs)
        if count > limits.max_triangles:
            raise ValueError(
                f"mesh has {count} triangles; configured maximum is "
                f"{limits.max_triangles}"
            )
        lower, upper = mesh_bounds(inputs)
        dimensions = upper - lower
        contact = render_contact_sheet(
            inputs,
            args.size,
            title=(
                "visual evidence  |  "
                f"{dimensions[0]:.2f} x {dimensions[1]:.2f} x "
                f"{dimensions[2]:.2f} mm"
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
            "checks": {
                "finite_vertices": True,
                "watertight": all(bool(item.mesh.is_watertight) for item in inputs),
                "winding_consistent": all(
                    bool(item.mesh.is_winding_consistent) for item in inputs
                ),
            },
            "dimensions_mm": [round(float(value), 4) for value in dimensions],
            "meshes": [
                {
                    "name": item.name,
                    "path": str(item.path),
                    "preview_color_rgb": list(item.color),
                    "sha256": _digest(item.path),
                }
                for item in inputs
                if item.path is not None
            ],
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
            "renderer": "cpu-z-buffer/v1",
            "schema": "evidence-render/v2",
            "supported_views": list(SUPPORTED_VIEWS),
            "views": list(CONTACT_VIEWS),
        }
        if len(inputs) == 1 and inputs[0].path is not None:
            result["mesh"] = {
                "path": str(inputs[0].path),
                "sha256": _digest(inputs[0].path),
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
