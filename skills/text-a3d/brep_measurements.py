"""Measure final BRep sections, independently of source construction parameters.

``measure_section(part, Plane.XY.offset(95))`` measures the actual z=95 section.
Its width/depth are along the plane's explicit u/v directions; its center is the
outer bounds midpoint, not the material centroid. Compare parallel sections in
the same frame to inspect mouth/foot widths and center displacement. No section
is selected implicitly, and these measurements do not establish global minima.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

from build123d import Face, Plane, Shape, import_step, section
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps


_NOTES = [
    "Coordinates are the input part coordinates; no print-pose transform is applied.",
    "BRep intersections and bounds retain kernel precision; areas use adaptive integration with relative target 1e-9, not mesh sampling.",
    "Width u and depth v follow the reported plane axes; center is the outer bounds midpoint, not a centroid.",
    "Material islands are section faces per solid; holes are their bounded inner loops, not proven through passages.",
    "Areas are summed per solid without unioning overlaps; overlapping solids can double count material.",
    "Empty means no positive-area section returned; a tangent plane may still touch an edge or point. Inspect nearby specified planes when needed.",
    "Finite sections do not prove global wall thickness, connectivity, or satisfaction of unsampled requirements.",
]
_SUMMARY_SECTION_LIMIT = 8


def _xyz(value) -> list[float]:
    return [float(v) for v in value]


def _bounds(shape: Shape) -> dict:
    # Analytic BRep bounds, without using or clearing a cached triangulation and
    # without inflating by stored shape tolerances. Kernel precision still applies.
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    if box.IsVoid():
        raise ValueError("cannot bound an empty BRep")
    values = box.Get()
    low, high = list(values[:3]), list(values[3:])
    return {"min": low, "max": high, "size": [b - a for a, b in zip(low, high)]}


def _envelope(bounds: dict, plane: Plane) -> dict:
    low, high = bounds["min"][:2], bounds["max"][:2]
    center = [(a + b) / 2 for a, b in zip(low, high)]
    return {
        "min_uv_mm": low, "max_uv_mm": high,
        "width_u_mm": high[0] - low[0], "depth_v_mm": high[1] - low[1],
        "center_uv_mm": center,
        "center_world_mm": _xyz(plane.from_local_coords((*center, 0))),
    }


def _surface_properties(face: Face) -> tuple[float, list[float], float]:
    # Default non-adaptive quadrature can lose area on trimmed B-spline curves.
    properties = GProp_GProps()
    error = BRepGProp.SurfaceProperties_s(face.wrapped, properties, 1e-9, False)
    return float(properties.Mass()), list(properties.CentreOfMass().Coord()), float(error)


def _boundary(wire, plane: Plane) -> dict:
    if not wire.is_closed:
        raise ValueError("section returned an open boundary for a material face")
    area, _, error = _surface_properties(Face(wire))
    return {
        "envelope": _envelope(_bounds(plane.to_local_coords(wire)), plane),
        "enclosed_area_mm2": area, "relative_area_error_estimate": error,
        "perimeter_mm": float(wire.length),
    }


def _solids(shape: Shape) -> list:
    if not isinstance(shape, Shape) or not shape.is_valid:
        raise ValueError("measurement requires a valid BRep containing solids")
    solids = list(shape.solids())
    if not solids:
        raise ValueError("measurement requires at least one solid")
    return solids


def measure_section(shape: Shape, plane: Plane, *, label: str | None = None) -> dict:
    """Measure a specified plane in the final part's coordinates, in mm/mm².

    Each solid's positive-area section faces remain separate material islands.
    ``outer_envelope`` encloses all islands (including intervening empty space);
    it is null for an empty section. A hole belongs to its particular island.
    Coplanar end faces count as sections when returned by the kernel. No plane
    offset, tolerance-based pass/fail, source station, or mesh sampling is used.
    """
    if not isinstance(plane, Plane) or not all(
        math.isfinite(v) for axis in (plane.origin, plane.x_dir, plane.y_dir, plane.z_dir) for v in axis
    ):
        raise ValueError("plane must have finite origin and axes")
    # section() may clean its input's triangulation cache; isolate the caller.
    solids = _solids(deepcopy(shape))
    islands = []
    for solid_index, solid in enumerate(solids):
        # build123d uses a finite cutting face. Recenter that face on the solid's
        # projection, so an equivalent plane with a distant in-plane origin
        # cannot miss the solid. Measurement coordinates still use the input plane.
        box = _bounds(solid)
        center = tuple((a + b) / 2 for a, b in zip(box["min"], box["max"]))
        local = plane.to_local_coords(center)
        cutting_plane = Plane(origin=plane.from_local_coords((local.X, local.Y, 0)),
                              x_dir=plane.x_dir, z_dir=plane.z_dir)
        cut = section(solid, section_by=cutting_plane)
        for face in cut.faces():
            area, centroid, error = _surface_properties(face)
            if area <= 0:
                continue
            islands.append({
                "solid_index": solid_index,
                "island_index": len(islands),
                "material_area_mm2": area,
                "material_centroid_world_mm": centroid,
                "relative_area_error_estimate": error,
                "outer": _boundary(face.outer_wire(), plane),
                "holes": [_boundary(wire, plane) for wire in face.inner_wires()],
            })
    envelope = None
    if islands:
        boxes = [island["outer"]["envelope"] for island in islands]
        envelope = _envelope({
            "min": [min(box["min_uv_mm"][i] for box in boxes) for i in range(2)],
            "max": [max(box["max_uv_mm"][i] for box in boxes) for i in range(2)],
        }, plane)
    return {
        "schema": "brep-section/v1", "label": label,
        "units": {"length": "mm", "area": "mm2"},
        "coordinate_frame": "input-part",
        "plane": {"origin_mm": _xyz(plane.origin), "u_dir": _xyz(plane.x_dir),
                  "v_dir": _xyz(plane.y_dir), "normal_dir": _xyz(plane.z_dir)},
        "status": "material" if islands else "empty",
        "outer_envelope": envelope,
        "material_island_count": len(islands),
        "hole_count": sum(len(island["holes"]) for island in islands),
        "sum_material_area_mm2": sum(island["material_area_mm2"] for island in islands),
        "material_islands": islands,
        "notes": list(_NOTES),
    }


def measure_step(path: str | Path, sections: Mapping[str, Plane] | None = None) -> dict:
    """Read a final STEP; bind its SHA-256, world bounds and named exact sections.

    ``sections={"foot": Plane.XY, "mouth": Plane.XY.offset(95)}`` preserves the
    caller's chosen planes and labels. STEP is imported in millimetres. Python
    build source and intent parameters are never read or executed.
    """
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in {".step", ".stp"}:
        raise ValueError("measurement input must be a STEP/STP file")
    if sections is not None and not isinstance(sections, Mapping):
        raise ValueError("sections must map labels to Plane objects")
    input_hash = sha256(path.read_bytes()).hexdigest()
    shape = import_step(path)
    solids = _solids(shape)
    result = {
        "schema": "brep-measurements/v1",
        "input": {"path": str(path), "sha256": input_hash, "format": "STEP"},
        "units": {"length": "mm", "area": "mm2", "volume": "mm3"},
        "coordinate_frame": "input-part",
        "world_bounds_mm": _bounds(shape), "solid_count": len(solids),
        "sum_solid_volume_mm3": sum(float(solid.volume) for solid in solids),
        "sections": [measure_section(shape, plane, label=label)
                     for label, plane in (sections or {}).items()],
        "notes": list(_NOTES),
    }
    if sha256(path.read_bytes()).hexdigest() != input_hash:
        raise ValueError("STEP changed during measurement; result discarded")
    return result


def main(argv: list[str] | None = None) -> int:
    """Write a full report and print a bounded summary with final section sizes."""
    from cad_compile import _workspace_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    for axis in "xyz":
        parser.add_argument(f"--section-{axis}", action="append", type=float, default=[],
                            metavar="MM", help=f"Repeat for exact world {axis} coordinates in mm")
    parser.add_argument("--out", type=Path,
                        help="Full report path (default: <model-stem>_measurements.json in workspace)")
    args = parser.parse_args(argv)
    try:
        workspace = args.workspace.resolve(strict=True)
        model = _workspace_path(workspace, args.model, "model", must_exist=True)
        output = _workspace_path(workspace, args.out or Path(f"{model.stem}_measurements.json"),
                                 "output", must_exist=False)
        if output == model or (output.exists() and output.samefile(model)):
            raise ValueError("output must not overwrite the STEP input")
        planes = {}
        for axis in "xyz":
            for value in getattr(args, f"section_{axis}"):
                if not math.isfinite(value):
                    raise ValueError("section coordinates must be finite")
                origin = tuple(value if a == axis else 0 for a in "xyz")
                # y cuts use u=+X, v=+Z, normal=-Y for a right-handed frame.
                normal = {"x": (1, 0, 0), "y": (0, -1, 0), "z": (0, 0, 1)}[axis]
                u_dir = (0, 1, 0) if axis == "x" else (1, 0, 0)
                planes[f"{axis}={value!r}"] = Plane(origin=origin, x_dir=u_dir, z_dir=normal)
        result = measure_step(model, planes)
        encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
        output.write_text(encoded, encoding="utf-8")
        fields = ("label", "plane", "status", "outer_envelope", "material_island_count",
                  "hole_count", "sum_material_area_mm2")
        summaries = [{key: cut[key] for key in fields}
                     for cut in result["sections"][:_SUMMARY_SECTION_LIMIT]]
        print(json.dumps({
            "schema": "brep-measurement-summary/v1", "input": result["input"],
            "fullResult": {"path": str(output), "sha256": sha256(encoded.encode("utf-8")).hexdigest()},
            "units": result["units"], "coordinate_frame": result["coordinate_frame"],
            "world_bounds_mm": result["world_bounds_mm"], "solid_count": result["solid_count"],
            "sections": summaries, "section_count": len(result["sections"]),
            "returned_section_count": len(summaries),
            "omitted_section_count": len(result["sections"]) - len(summaries),
            "scope": "Specified sections only, not a global proof. Remaining sections, material islands, holes and limitations are in fullResult.",
        }, allow_nan=False))
    except (ValueError, OSError) as error:
        parser.exit(2, f"measurement error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
