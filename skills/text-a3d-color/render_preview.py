"""Render color-region STLs as orthographic, hash-bound visual evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
CAMERAS = {
    "isometric": (28, 42),
    "front": (0, -90),
    "side": (0, 0),
    "top": (89.9, -90),
}
REFERENCE_CAMERAS = {**CAMERAS, "bottom": (-89.9, -90)}
LIGHT = np.array([0.35, -0.55, 0.76], dtype=float)
LIGHT /= np.linalg.norm(LIGHT)


@dataclass
class Region:
    name: str
    path: Path
    color: str
    mesh: trimesh.Trimesh
    facecolors: np.ndarray


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rgb(value: str) -> np.ndarray:
    if not HEX.fullmatch(value):
        raise ValueError(f"invalid region color {value!r}; expected #RRGGBB")
    return np.array(
        [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)],
        dtype=float,
    )


def _region(specification: str) -> Region:
    filename, separator, color = specification.rpartition("=")
    if not separator or not filename:
        raise ValueError(f"bad --part value {specification!r}; expected path.stl=#RRGGBB")
    path = Path(filename).resolve()
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise ValueError(f"no renderable mesh in {path}")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"non-finite vertices in {path}")
    color = color.upper()
    base = _rgb(color)
    facing = np.einsum("ij,j->i", mesh.face_normals, LIGHT)
    intensity = 0.42 + 0.58 * np.clip(facing, 0.0, 1.0)
    facecolors = np.column_stack((np.outer(intensity, base), np.ones(len(intensity))))
    return Region(path.stem, path, color, mesh, facecolors)


def _bounds(regions: list[Region]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.array([region.mesh.bounds for region in regions])
    return stack[:, 0].min(axis=0), stack[:, 1].max(axis=0)


def _frame(axis, regions: list[Region], camera: tuple[float, float]) -> None:
    low, high = _bounds(regions)
    spans = high - low
    center = (low + high) / 2
    margin = max(float(spans.max()) * 0.06, 0.05)
    half = np.maximum(spans / 2 + margin, 0.05)
    triangles = np.concatenate([region.mesh.triangles for region in regions])
    colors = np.concatenate([region.facecolors for region in regions])
    axis.add_collection3d(Poly3DCollection(
        triangles,
        facecolors=colors,
        edgecolors=(0.08, 0.09, 0.10, 0.12),
        linewidths=0.10,
        antialiased=True,
    ))
    axis.set_xlim(center[0] - half[0], center[0] + half[0])
    axis.set_ylim(center[1] - half[1], center[1] + half[1])
    axis.set_zlim(center[2] - half[2], center[2] + half[2])
    axis.set_box_aspect(np.maximum(spans, 1e-6))
    axis.set_proj_type("ortho")
    axis.view_init(elev=camera[0], azim=camera[1])
    axis.set_axis_off()


def _contact(regions: list[Region], destination: Path, pixels: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(pixels / 100, pixels / 100), dpi=100)
    for index, (name, camera) in enumerate(CAMERAS.items(), start=1):
        axis = figure.add_subplot(2, 2, index, projection="3d")
        _frame(axis, regions, camera)
        axis.set_title(name, fontsize=9, color="#24313d")
    low, high = _bounds(regions)
    dimensions = high - low
    palette = "  ".join(region.color for region in regions)
    figure.suptitle(
        f"{len(regions)} regions  ·  {dimensions[0]:.2f} × "
        f"{dimensions[1]:.2f} × {dimensions[2]:.2f} mm  ·  {palette}",
        fontsize=9,
        color="#24313d",
    )
    figure.tight_layout()
    figure.savefig(destination, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _matched(regions, camera, destination: Path, pixels: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(pixels / 100, pixels / 100), dpi=100)
    axis = figure.add_subplot(1, 1, 1, projection="3d")
    _frame(axis, regions, camera)
    figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
    figure.savefig(destination, bbox_inches="tight", pad_inches=0, facecolor="white")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=900)
    parser.add_argument("--reference-view", choices=REFERENCE_CAMERAS)
    parser.add_argument("--reference-out")
    parser.add_argument("--report", help="Optional JSON evidence report")
    args = parser.parse_args()
    if bool(args.reference_view) != bool(args.reference_out):
        parser.error("--reference-view and --reference-out must be used together")
    if args.size < 320:
        parser.error("--size must be at least 320")

    try:
        regions = [_region(item) for item in args.part]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    destination = Path(args.out)
    _contact(regions, destination, args.size)
    low, high = _bounds(regions)
    result = {
        "dimensions_mm": [round(float(value), 4) for value in high - low],
        "preview": {"path": str(destination.resolve()), "sha256": _digest(destination)},
        "projection": "orthographic",
        "regions": [
            {
                "color": region.color,
                "dimensions_mm": [round(float(value), 4) for value in region.mesh.extents],
                "name": region.name,
                "path": str(region.path),
                "sha256": _digest(region.path),
                "watertight": bool(region.mesh.is_watertight),
            }
            for region in regions
        ],
        "schema": "evidence-color-render/v2",
        "views": list(CAMERAS),
    }
    if args.reference_view:
        matched = Path(args.reference_out)
        _matched(regions, REFERENCE_CAMERAS[args.reference_view], matched, args.size)
        result["matched_view"] = {
            "name": args.reference_view,
            "path": str(matched.resolve()),
            "sha256": _digest(matched),
        }
    payload = json.dumps(result, indent=2)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
