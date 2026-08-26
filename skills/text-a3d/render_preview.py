"""Create orthographic visual evidence for a single-material STL."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tracemalloc


SKILLS_ROOT = Path(__file__).resolve().parents[1]
if str(SKILLS_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILLS_ROOT))

from cpu_z_buffer import (  # noqa: E402
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
    render_contact_sheet,
    render_view,
    triangle_count,
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _save_png(image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl")
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
        source = Path(args.stl).resolve()
        destination = Path(
            args.out or source.with_name(f"{source.stem}_views.png")
        ).resolve()
        mesh = load_mesh(source)
        inputs = [MeshInput(source.stem, mesh, DEFAULT_MATERIAL, source)]
        count = triangle_count(inputs)
        if count > limits.max_triangles:
            raise ValueError(
                f"mesh has {count} triangles; configured maximum is "
                f"{limits.max_triangles}"
            )
        dimensions = mesh.extents
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
                "watertight": bool(mesh.is_watertight),
                "winding_consistent": bool(mesh.is_winding_consistent),
            },
            "dimensions_mm": [round(float(value), 4) for value in mesh.extents],
            "mesh": {"path": str(source), "sha256": _digest(source)},
            "performance": {
                "four_view_seconds": round(contact.elapsed_seconds, 6),
                "parallel_views": False,
                "peak_memory_bytes": max(int(traced_peak), peak_buffer_bytes),
                "processes": 1,
                "supersample": args.supersample,
                "triangle_count": count,
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
    sys.exit(main())
