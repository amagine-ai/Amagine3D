"""Absolute millimetre tessellation without reusing or changing BRep caches."""
from __future__ import annotations

import math
from typing import Any

from build123d import Vector
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS


def tessellate_brep(
    shape: Any,
    linear_tolerance_mm: float = 0.02,
    angular_tolerance_rad: float = 0.1,
) -> tuple[list[Vector], list[tuple[int, int, int]]]:
    """Triangulate actual BRep faces using absolute linear and angular precision.

    Return the same vertex/triangle format as build123d ``Shape.tessellate``.
    A separate BRep copy excludes cached meshes, so earlier display meshes or
    coarser angular settings cannot silently replace the requested precision.
    Faces, sketches and solids share this operation; solid validity, welding
    and export normalization remain the responsibility of their callers.
    """
    linear = float(linear_tolerance_mm)
    angular = float(angular_tolerance_rad)
    if not math.isfinite(linear) or linear <= 0 or not math.isfinite(angular) or angular <= 0:
        raise ValueError("BRep tessellation tolerances must be finite and positive")
    if getattr(shape, "wrapped", None) is None:
        raise ValueError("BRep tessellation requires actual geometry")

    # copyGeom=True and copyMesh=False isolate both topology and tessellation
    # from the caller. Cleaning only this copy also excludes edge polygon caches.
    wrapped = BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape()
    BRepTools.Clean_s(wrapped)
    operation = BRepMesh_IncrementalMesh(wrapped, linear, False, angular, True)
    if not operation.IsDone():
        raise RuntimeError("Absolute BRep tessellation did not complete")

    vertices, triangles = [], []
    explorer = TopExp_Explorer(wrapped, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise RuntimeError("Absolute BRep tessellation left a face without triangles")
        transform = location.Transformation()
        start = len(vertices)
        for index in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(index).Transformed(transform)
            vertices.append(Vector(point.X(), point.Y(), point.Z()))
        reverse = face.Orientation() == TopAbs_REVERSED
        for triangle in triangulation.Triangles():
            a, b, c = (triangle.Value(index) + start - 1 for index in (1, 2, 3))
            triangles.append((a, c, b) if reverse else (a, b, c))
        explorer.Next()
    return vertices, triangles
