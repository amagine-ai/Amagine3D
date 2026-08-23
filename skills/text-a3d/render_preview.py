"""Create orthographic visual evidence for a single-material STL."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


@dataclass(frozen=True)
class View:
    label: str
    elevation: float
    azimuth: float


VIEWS = {
    "isometric": View("isometric", 28, 42),
    "front": View("front", 0, -90),
    "side": View("side", 0, 0),
    "top": View("top", 89.9, -90),
}
LIGHT = np.array([0.35, -0.55, 0.76], dtype=float)
LIGHT /= np.linalg.norm(LIGHT)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise ValueError(f"no renderable mesh in {path}")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"non-finite mesh vertices in {path}")
    return mesh


def _shading(mesh: trimesh.Trimesh) -> np.ndarray:
    facing = np.einsum("ij,j->i", mesh.face_normals, LIGHT)
    intensity = 0.38 + 0.62 * np.clip(facing, 0.0, 1.0)
    material = np.array([0.48, 0.64, 0.78])
    return np.column_stack((np.outer(intensity, material), np.ones(len(intensity))))


def _frame(ax, mesh: trimesh.Trimesh, colors: np.ndarray, view: View) -> None:
    bounds = mesh.bounds
    center = bounds.mean(axis=0)
    spans = bounds[1] - bounds[0]
    margin = max(float(spans.max()) * 0.06, 0.05)
    half = np.maximum(spans / 2 + margin, 0.05)
    collection = Poly3DCollection(
        mesh.triangles,
        facecolors=colors,
        edgecolors=(0.10, 0.14, 0.18, 0.16),
        linewidths=0.12,
        antialiased=True,
    )
    ax.add_collection3d(collection)
    ax.set_xlim(center[0] - half[0], center[0] + half[0])
    ax.set_ylim(center[1] - half[1], center[1] + half[1])
    ax.set_zlim(center[2] - half[2], center[2] + half[2])
    ax.set_box_aspect(np.maximum(spans, 1e-6))
    ax.set_proj_type("ortho")
    ax.view_init(elev=view.elevation, azim=view.azimuth)
    ax.set_axis_off()


def _save_contact(mesh, colors, destination: Path, pixels: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(pixels / 100, pixels / 100), dpi=100)
    for index, view in enumerate(VIEWS.values(), start=1):
        axis = figure.add_subplot(2, 2, index, projection="3d")
        _frame(axis, mesh, colors, view)
        axis.set_title(view.label, fontsize=9, color="#24313d")
    dimensions = mesh.extents
    figure.suptitle(
        "visual evidence  ·  "
        f"{dimensions[0]:.2f} × {dimensions[1]:.2f} × {dimensions[2]:.2f} mm",
        fontsize=10,
        color="#24313d",
    )
    figure.tight_layout()
    figure.savefig(destination, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _save_matched(mesh, colors, view: View, destination: Path, pixels: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(pixels / 100, pixels / 100), dpi=100)
    axis = figure.add_subplot(1, 1, 1, projection="3d")
    _frame(axis, mesh, colors, view)
    figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
    figure.savefig(destination, bbox_inches="tight", pad_inches=0, facecolor="white")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl")
    parser.add_argument("--out")
    parser.add_argument("--size", type=int, default=900)
    parser.add_argument("--reference-view", choices=VIEWS)
    parser.add_argument("--reference-out")
    parser.add_argument("--report", help="Optional JSON evidence report")
    args = parser.parse_args()
    if bool(args.reference_view) != bool(args.reference_out):
        parser.error("--reference-view and --reference-out must be used together")
    if args.size < 320:
        parser.error("--size must be at least 320")

    source = Path(args.stl).resolve()
    destination = Path(args.out or source.with_name(f"{source.stem}_views.png"))
    mesh = _load(source)
    colors = _shading(mesh)
    _save_contact(mesh, colors, destination, args.size)

    result = {
        "checks": {
            "finite_vertices": True,
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
        },
        "dimensions_mm": [round(float(value), 4) for value in mesh.extents],
        "mesh": {"path": str(source), "sha256": _digest(source)},
        "preview": {"path": str(destination.resolve()), "sha256": _digest(destination)},
        "projection": "orthographic",
        "schema": "evidence-render/v2",
        "views": list(VIEWS),
    }
    if args.reference_view:
        matched = Path(args.reference_out)
        _save_matched(mesh, colors, VIEWS[args.reference_view], matched, args.size)
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
