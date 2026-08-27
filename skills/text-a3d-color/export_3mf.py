"""Write and independently inspect a region-colored 3MF archive.

Unlike a mesh-count-only check, inspection reads the XML package back and
reports the color actually attached to every named object.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree
from zipfile import ZipFile

import lib3mf
import numpy as np
import trimesh


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
UNIT_TO_MM = {
    "centimeter": 10.0,
    "foot": 304.8,
    "inch": 25.4,
    "meter": 1000.0,
    "micron": 0.001,
    "millimeter": 1.0,
}


@dataclass(frozen=True)
class RegionMesh:
    path: str
    color: str
    name: str


def _color(value: str) -> str:
    if not HEX.fullmatch(value):
        raise ValueError(f"invalid RGB color: {value}")
    return value.upper()


def _lib_color(wrapper, value: str):
    red, green, blue = (int(value[index:index + 2], 16) for index in (1, 3, 5))
    try:
        return wrapper.RGBAToColor(red, green, blue, 255)
    except AttributeError:
        result = lib3mf.Color()
        result.Red, result.Green, result.Blue, result.Alpha = red, green, blue, 255
        return result


def _identity(wrapper):
    try:
        return wrapper.GetIdentityTransform()
    except AttributeError:
        result = lib3mf.Transform()
        for row in range(4):
            for column in range(3):
                result.Fields[row][column] = 1.0 if row == column else 0.0
        return result


def write_color_archive(entries, out_path: str) -> dict:
    regions = [RegionMesh(str(path), _color(color), str(name)) for path, color, name in entries]
    if not regions:
        raise ValueError("at least one color region is required")
    if len({region.name for region in regions}) != len(regions):
        raise ValueError("region names must be unique")

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    try:
        model.SetUnit(lib3mf.ModelUnit.MilliMeter)
    except Exception:
        pass

    palette = model.AddColorGroup()
    palette_index: dict[str, int] = {}
    for value in dict.fromkeys(region.color for region in regions):
        palette_index[value] = palette.AddColor(_lib_color(wrapper, value))

    summary = {"file": str(Path(out_path).resolve()), "objects": []}
    for region in regions:
        mesh = trimesh.load(region.path, force="mesh", process=False)
        object_3mf = model.AddMeshObject()
        object_3mf.SetName(region.name)

        vertices = []
        for vertex in mesh.vertices:
            position = lib3mf.Position()
            for axis in range(3):
                position.Coordinates[axis] = float(vertex[axis])
            vertices.append(position)
        triangles = []
        for face in mesh.faces:
            triangle = lib3mf.Triangle()
            for corner in range(3):
                triangle.Indices[corner] = int(face[corner])
            triangles.append(triangle)

        object_3mf.SetGeometry(vertices, triangles)
        object_3mf.SetObjectLevelProperty(
            palette.GetResourceID(), palette_index[region.color],
        )
        model.AddBuildItem(object_3mf, _identity(wrapper))
        summary["objects"].append({
            "color": region.color,
            "name": region.name,
            "triangles": len(triangles),
            "vertices": len(vertices),
        })

    model.QueryWriter("3mf").WriteToFile(str(out_path))
    summary["inspection"] = inspect_color_archive(out_path)
    return summary


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespaced_attr(element, local_name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local(key) == local_name:
            return value
    return None


def _palette_lookup(root) -> dict[str, list[str]]:
    palettes: dict[str, list[str]] = {}
    for element in root.iter():
        if _local(element.tag) != "colorgroup":
            continue
        palettes[element.attrib["id"]] = [
            child.attrib["color"].upper()
            for child in element
            if _local(child.tag) == "color"
        ]
    return palettes


def _object_color(element, palettes: dict[str, list[str]]) -> str | None:
    palette = palettes.get(element.attrib.get("pid", ""), [])
    try:
        index = int(element.attrib.get("pindex", "0"))
    except ValueError:
        return None
    color = palette[index] if index < len(palette) else None
    return color[:7] if color and len(color) >= 7 else color


def _mesh_from_object(element, unit_scale: float) -> trimesh.Trimesh:
    mesh_element = next(
        (child for child in element if _local(child.tag) == "mesh"),
        None,
    )
    if mesh_element is None:
        return trimesh.Trimesh(vertices=[], faces=[], process=False)
    vertices_element = next(
        (child for child in mesh_element if _local(child.tag) == "vertices"),
        None,
    )
    triangles_element = next(
        (child for child in mesh_element if _local(child.tag) == "triangles"),
        None,
    )
    vertices = []
    if vertices_element is not None:
        for vertex in vertices_element:
            if _local(vertex.tag) != "vertex":
                continue
            vertices.append([
                float(vertex.attrib[axis]) * unit_scale
                for axis in ("x", "y", "z")
            ])
    faces = []
    if triangles_element is not None:
        for triangle in triangles_element:
            if _local(triangle.tag) != "triangle":
                continue
            faces.append([
                int(triangle.attrib[index])
                for index in ("v1", "v2", "v3")
            ])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def _transform_matrix(raw: str | None) -> np.ndarray:
    matrix = np.eye(4)
    if raw is None or not raw.strip():
        return matrix
    values = [float(item) for item in raw.split()]
    if len(values) != 12:
        raise ValueError("3MF build item transform must contain 12 numbers")
    matrix[:3, :] = np.asarray(values, dtype=float).reshape((3, 4))
    return matrix


def inspect_color_archive(path: str) -> dict:
    """Read names and object-level colors directly from packaged 3MF XML."""
    with ZipFile(path) as archive:
        model_name = next(
            name for name in archive.namelist()
            if name.lower().endswith(".model")
        )
        root = ElementTree.fromstring(archive.read(model_name))

    palettes = _palette_lookup(root)

    objects = []
    for element in root.iter():
        if _local(element.tag) != "object":
            continue
        objects.append({
            "color": _object_color(element, palettes),
            "id": element.attrib.get("id"),
            "name": element.attrib.get("name", ""),
        })
    build_items = [
        {
            "object_id": element.attrib.get("objectid"),
            "transform": _namespaced_attr(element, "transform"),
        }
        for element in root.iter()
        if _local(element.tag) == "item"
    ]
    return {
        "file": str(Path(path).resolve()),
        "build_item_count": len(build_items),
        "build_items": build_items,
        "object_count": len(objects),
        "objects": objects,
        "palette_count": len({item["color"] for item in objects}),
        "unit": root.attrib.get("unit", "millimeter"),
    }


def load_color_archive_mesh(path: str) -> tuple[trimesh.Trimesh, dict]:
    """Return the placed aggregate geometry from a 3MF package plus metadata."""
    with ZipFile(path) as archive:
        model_name = next(
            name for name in archive.namelist()
            if name.lower().endswith(".model")
        )
        root = ElementTree.fromstring(archive.read(model_name))

    unit = root.attrib.get("unit", "millimeter").lower()
    if unit not in UNIT_TO_MM:
        raise ValueError(f"unsupported 3MF unit: {unit}")
    palettes = _palette_lookup(root)
    unit_scale = UNIT_TO_MM[unit]
    objects = {}
    object_summaries = {}
    for element in root.iter():
        if _local(element.tag) != "object":
            continue
        object_id = element.attrib.get("id")
        if not object_id:
            continue
        mesh = _mesh_from_object(element, unit_scale)
        objects[object_id] = mesh
        object_summaries[object_id] = {
            "color": _object_color(element, palettes),
            "name": element.attrib.get("name", ""),
            "triangles": int(len(mesh.faces)),
            "vertices": int(len(mesh.vertices)),
        }

    build_items = [
        element
        for element in root.iter()
        if _local(element.tag) == "item"
    ]
    placed = []
    placed_summaries = []
    source_items = build_items or []
    if not source_items:
        source_items = [
            ElementTree.Element("item", {"objectid": object_id})
            for object_id in objects
        ]
    for item in source_items:
        object_id = item.attrib.get("objectid")
        if object_id not in objects:
            continue
        mesh = objects[object_id].copy()
        transform = _transform_matrix(_namespaced_attr(item, "transform"))
        mesh.apply_transform(transform)
        placed.append(mesh)
        placed_summaries.append({
            "object_id": object_id,
            "transform": transform.round(8).tolist(),
            **object_summaries.get(object_id, {}),
        })
    if not placed:
        raise ValueError("3MF package contains no placed mesh objects")
    mesh = trimesh.util.concatenate(placed)
    summary = inspect_color_archive(path)
    summary["placed_objects"] = placed_summaries
    return mesh, summary


# Compatibility with previously generated sources.
write_3mf = write_color_archive
verify_3mf = inspect_color_archive


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] in {"--inspect", "--verify"}:
        print(json.dumps(inspect_color_archive(args[1]), indent=2))
        return 0
    if len(args) < 2:
        print(__doc__)
        return 2
    output = args[0]
    entries = []
    for specification in args[1:]:
        mesh_path, separator, color = specification.rpartition("=")
        if not separator:
            print(json.dumps({"error": f"expected mesh.stl=#RRGGBB: {specification}"}))
            return 2
        name = Path(mesh_path).stem
        entries.append((mesh_path, color, name))
    print(json.dumps(write_color_archive(entries, output), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
