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
PACKAGE_MODES = {"co_print_body", "separate_parts"}
REGION_METADATA_NAME = "amagine3d-color-regions"
REGION_METADATA_NAMESPACE = "https://amagine3d.local/3mf"
REGION_METADATA_SCHEMA = "amagine3d-color-regions/v1"


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


def _package_mode(value: str | None) -> str:
    mode = value or "co_print_body"
    if mode not in PACKAGE_MODES:
        raise ValueError(f"invalid 3MF package mode: {mode}")
    return mode


def write_color_archive(
    entries,
    out_path: str,
    *,
    package_mode: str = "co_print_body",
    package_name: str | None = None,
) -> dict:
    regions = [
        RegionMesh(str(path), _color(color), str(name))
        for path, color, name in entries
    ]
    if not regions:
        raise ValueError("at least one color region is required")
    if len({region.name for region in regions}) != len(regions):
        raise ValueError("region names must be unique")
    package_mode = _package_mode(package_mode)

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

    summary = {
        "file": str(Path(out_path).resolve()),
        "objects": [],
        "package_mode": package_mode,
        "regions": [],
    }
    loaded_regions = []
    for region in regions:
        mesh = trimesh.load(region.path, force="mesh", process=False)
        if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
            raise ValueError(f"region {region.name!r} did not load as a mesh")
        loaded_regions.append((region, mesh))

    region_metadata = {
        "package_mode": package_mode,
        "package_name": package_name or Path(out_path).stem,
        "regions": [],
        "schema": REGION_METADATA_SCHEMA,
    }

    if package_mode == "co_print_body":
        object_3mf = model.AddMeshObject()
        object_3mf.SetName(package_name or Path(out_path).stem)
        vertices = []
        triangles = []
        triangle_properties = []
        vertex_offset = 0
        triangle_start = 0
        color_resource_id = palette.GetResourceID()
        for region, mesh in loaded_regions:
            for vertex in mesh.vertices:
                position = lib3mf.Position()
                for axis in range(3):
                    position.Coordinates[axis] = float(vertex[axis])
                vertices.append(position)
            for face in mesh.faces:
                triangle = lib3mf.Triangle()
                for corner in range(3):
                    triangle.Indices[corner] = int(face[corner]) + vertex_offset
                triangles.append(triangle)
                properties = lib3mf.TriangleProperties()
                properties.ResourceID = int(color_resource_id)
                for corner in range(3):
                    properties.PropertyIDs[corner] = int(palette_index[region.color])
                triangle_properties.append(properties)
            triangle_count = int(len(mesh.faces))
            region_record = {
                "color": region.color,
                "name": region.name,
                "triangle_range": {
                    "count": triangle_count,
                    "start": triangle_start,
                },
                "triangles": triangle_count,
                "vertices": int(len(mesh.vertices)),
            }
            region_metadata["regions"].append(region_record)
            summary["regions"].append(region_record)
            vertex_offset += int(len(mesh.vertices))
            triangle_start += triangle_count
        object_3mf.SetGeometry(vertices, triangles)
        object_3mf.SetAllTriangleProperties(triangle_properties)
        summary["objects"].append({
            "color": None,
            "name": package_name or Path(out_path).stem,
            "triangles": len(triangles),
            "vertices": len(vertices),
        })
        model.AddBuildItem(object_3mf, _identity(wrapper))
    else:
        mesh_objects = []
        for region, mesh in loaded_regions:
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
            mesh_objects.append(object_3mf)
            region_record = {
                "color": region.color,
                "name": region.name,
                "triangles": len(triangles),
                "vertices": len(vertices),
            }
            region_metadata["regions"].append(region_record)
            summary["objects"].append(region_record)
            summary["regions"].append(region_record)
        for object_3mf in mesh_objects:
            model.AddBuildItem(object_3mf, _identity(wrapper))

    model.GetMetaDataGroup().AddMetaData(
        REGION_METADATA_NAMESPACE,
        REGION_METADATA_NAME,
        json.dumps(region_metadata, separators=(",", ":"), sort_keys=True),
        "application/json",
        False,
    )
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


def _metadata_value(element) -> str:
    if element.text and element.text.strip():
        return element.text.strip()
    for key in ("value", "Value"):
        if key in element.attrib:
            return element.attrib[key]
    return ""


def _region_metadata(root) -> dict | None:
    for element in root.iter():
        if _local(element.tag) != "metadata":
            continue
        name = element.attrib.get("name", "")
        if name != REGION_METADATA_NAME and not name.endswith(f":{REGION_METADATA_NAME}"):
            continue
        try:
            payload = json.loads(_metadata_value(element))
        except json.JSONDecodeError:
            return None
        if payload.get("schema") == REGION_METADATA_SCHEMA:
            return payload
    return None


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


def _object_kind(element) -> str:
    for child in element:
        name = _local(child.tag)
        if name == "mesh":
            return "mesh"
        if name == "components":
            return "components"
    return "unknown"


def _component_refs(element) -> list[dict]:
    components_element = next(
        (child for child in element if _local(child.tag) == "components"),
        None,
    )
    if components_element is None:
        return []
    return [
        {
            "object_id": child.attrib.get("objectid"),
            "transform": _namespaced_attr(child, "transform"),
        }
        for child in components_element
        if _local(child.tag) == "component"
    ]


def _transform_matrix(raw: str | None) -> np.ndarray:
    matrix = np.eye(4)
    if raw is None or not raw.strip():
        return matrix
    values = [float(item) for item in raw.split()]
    if len(values) != 12:
        raise ValueError("3MF build item transform must contain 12 numbers")
    matrix[:3, :] = np.asarray(values, dtype=float).reshape((3, 4))
    return matrix


def _object_record(element, palettes: dict[str, list[str]]) -> dict:
    return {
        "color": _object_color(element, palettes),
        "id": element.attrib.get("id"),
        "kind": _object_kind(element),
        "name": element.attrib.get("name", ""),
    }


def _build_item_records(root, object_records: dict[str, dict]) -> list[dict]:
    records = []
    for element in root.iter():
        if _local(element.tag) != "item":
            continue
        object_id = element.attrib.get("objectid")
        object_record = object_records.get(object_id or "", {})
        records.append({
            "object_id": object_id,
            "object_kind": object_record.get("kind"),
            "object_name": object_record.get("name"),
            "transform": _namespaced_attr(element, "transform"),
        })
    return records


def _resolve_region_records(
    object_id: str,
    objects_by_id: dict,
    object_records: dict[str, dict],
    transform: np.ndarray,
    *,
    ancestry: tuple[str, ...] = (),
) -> list[dict]:
    if object_id in ancestry:
        raise ValueError("cyclic 3MF component reference")
    element = objects_by_id.get(object_id)
    if element is None:
        return []
    record = object_records[object_id]
    if record["kind"] == "mesh":
        return [{
            **record,
            "object_id": object_id,
            "transform": transform.round(8).tolist(),
        }]
    if record["kind"] != "components":
        return []
    regions = []
    for component in _component_refs(element):
        child_id = component.get("object_id")
        if not child_id:
            continue
        child_transform = _transform_matrix(component.get("transform"))
        regions.extend(_resolve_region_records(
            child_id,
            objects_by_id,
            object_records,
            transform @ child_transform,
            ancestry=(*ancestry, object_id),
        ))
    return regions


def _placed_region_records(
    root,
    objects_by_id: dict,
    object_records: dict[str, dict],
) -> list[dict]:
    build_items = [
        element
        for element in root.iter()
        if _local(element.tag) == "item"
    ]
    if not build_items:
        build_items = [
            ElementTree.Element("item", {"objectid": object_id})
            for object_id, record in object_records.items()
            if record["kind"] == "mesh"
        ]
    records = []
    for item in build_items:
        object_id = item.attrib.get("objectid")
        if not object_id:
            continue
        transform = _transform_matrix(_namespaced_attr(item, "transform"))
        records.extend(_resolve_region_records(
            object_id,
            objects_by_id,
            object_records,
            transform,
        ))
    return records


def _metadata_region_records(metadata: dict | None, build_items: list[dict]) -> list[dict]:
    if not isinstance(metadata, dict):
        return []
    metadata_regions = metadata.get("regions")
    if not isinstance(metadata_regions, list):
        return []
    object_id = build_items[0].get("object_id") if len(build_items) == 1 else None
    transform = _transform_matrix(
        build_items[0].get("transform") if len(build_items) == 1 else None
    )
    records = []
    for index, region in enumerate(metadata_regions):
        if not isinstance(region, dict):
            continue
        name = region.get("name")
        color = region.get("color")
        if not isinstance(name, str) or not isinstance(color, str):
            continue
        try:
            color = _color(color)
        except ValueError:
            continue
        records.append({
            "color": color,
            "id": f"{object_id or 'object'}:region:{index}",
            "kind": "mesh-region",
            "name": name,
            "object_id": object_id,
            "source": "metadata",
            "transform": transform.round(8).tolist(),
            "triangle_range": region.get("triangle_range"),
        })
    return records


def _archive_package_mode(
    build_items: list[dict],
    regions: list[dict],
    metadata: dict | None = None,
) -> str:
    if (
        isinstance(metadata, dict)
        and metadata.get("package_mode") in PACKAGE_MODES
        and len(build_items) == 1
        and regions
    ):
        return metadata["package_mode"]
    if (
        len(build_items) == 1
        and build_items[0].get("object_kind") == "components"
        and len(regions) > 1
    ):
        return "co_print_body"
    if len(build_items) > 1 and all(
        item.get("object_kind") == "mesh" for item in build_items
    ):
        return "separate_parts"
    return "custom"


def inspect_color_archive(path: str) -> dict:
    """Read names and object-level colors directly from packaged 3MF XML."""
    with ZipFile(path) as archive:
        model_name = next(
            name for name in archive.namelist()
            if name.lower().endswith(".model")
        )
        root = ElementTree.fromstring(archive.read(model_name))

    palettes = _palette_lookup(root)

    all_objects = []
    objects_by_id = {}
    object_records = {}
    for element in root.iter():
        if _local(element.tag) != "object":
            continue
        object_id = element.attrib.get("id")
        if not object_id:
            continue
        record = _object_record(element, palettes)
        objects_by_id[object_id] = element
        object_records[object_id] = record
        all_objects.append(record)
    mesh_objects = [item for item in all_objects if item["kind"] == "mesh"]
    component_objects = [item for item in all_objects if item["kind"] == "components"]
    build_items = _build_item_records(root, object_records)
    metadata = _region_metadata(root)
    regions = _metadata_region_records(
        metadata, build_items
    ) or _placed_region_records(root, objects_by_id, object_records)
    palette_colors = {item["color"] for item in regions if item.get("color")}
    if not palette_colors:
        palette_colors = {item["color"] for item in mesh_objects if item.get("color")}
    return {
        "file": str(Path(path).resolve()),
        "all_objects": all_objects,
        "build_item_count": len(build_items),
        "build_items": build_items,
        "component_object_count": len(component_objects),
        "object_count": len(mesh_objects),
        "objects": mesh_objects,
        "package_mode": _archive_package_mode(build_items, regions, metadata),
        "palette_count": len(palette_colors),
        "placed_region_count": len(regions),
        "region_metadata": metadata,
        "regions": regions,
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
    objects_by_id = {}
    object_summaries = {}
    object_records = {}
    for element in root.iter():
        if _local(element.tag) != "object":
            continue
        object_id = element.attrib.get("id")
        if not object_id:
            continue
        objects_by_id[object_id] = element
        record = _object_record(element, palettes)
        object_records[object_id] = record
        if record["kind"] == "mesh":
            mesh = _mesh_from_object(element, unit_scale)
            objects[object_id] = mesh
            object_summaries[object_id] = {
                "color": record["color"],
                "name": record["name"],
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
        if not object_id:
            continue
        transform = _transform_matrix(_namespaced_attr(item, "transform"))
        region_records = _resolve_region_records(
            object_id,
            objects_by_id,
            object_records,
            transform,
        )
        for region in region_records:
            region_id = region["object_id"]
            if region_id not in objects:
                continue
            mesh = objects[region_id].copy()
            mesh.apply_transform(np.asarray(region["transform"], dtype=float))
            placed.append(mesh)
            placed_summaries.append({
                **object_summaries.get(region_id, {}),
                "object_id": region_id,
                "transform": region["transform"],
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
    package_mode = "co_print_body"
    if args and args[0] == "--separate-parts":
        package_mode = "separate_parts"
        args = args[1:]
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
    print(json.dumps(write_color_archive(
        entries,
        output,
        package_mode=package_mode,
        package_name=Path(output).stem,
    ), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
