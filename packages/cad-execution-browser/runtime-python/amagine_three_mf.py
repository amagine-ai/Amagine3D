"""Colored 3MF writer and readback inspector used by Amagine3D."""

from __future__ import annotations

import re

import lib3mf
import trimesh


def _wrapper():
    return lib3mf.get_wrapper()


def _color(wrapper, value: str):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError(f"Invalid RGB color {value!r}")
    channels = [int(value[index:index + 2], 16) for index in (1, 3, 5)]
    if hasattr(wrapper, "RGBAToColor"):
        return wrapper.RGBAToColor(*channels, 255)
    result = lib3mf.Color()
    result.Red, result.Green, result.Blue, result.Alpha = *channels, 255
    return result


def _identity(wrapper):
    if hasattr(wrapper, "GetIdentityTransform"):
        return wrapper.GetIdentityTransform()
    transform = lib3mf.Transform()
    for row in range(4):
        for column in range(3):
            transform.Fields[row][column] = 1.0 if row == column else 0.0
    return transform


def _position(coordinates):
    point = lib3mf.Position()
    for axis, value in enumerate(coordinates):
        point.Coordinates[axis] = float(value)
    return point


def _triangle(indices):
    triangle = lib3mf.Triangle()
    for corner, value in enumerate(indices):
        triangle.Indices[corner] = int(value)
    return triangle


def write_colored_3mf(entries, output_path: str) -> dict:
    wrapper = _wrapper()
    model = wrapper.CreateModel()
    try:
        model.SetUnit(lib3mf.ModelUnit.MilliMeter)
    except Exception:
        pass
    objects = []
    for stl_path, color, region_id in entries:
        mesh = trimesh.load_mesh(stl_path, force="mesh", process=True)
        resource = model.AddMeshObject()
        resource.SetName(region_id)
        resource.SetGeometry(
            [_position(vertex) for vertex in mesh.vertices],
            [_triangle(face) for face in mesh.faces],
        )
        color_group = model.AddColorGroup()
        color_index = color_group.AddColor(_color(wrapper, color))
        resource.SetObjectLevelProperty(color_group.GetResourceID(), color_index)
        model.AddBuildItem(resource, _identity(wrapper))
        objects.append(
            {
                "id": region_id,
                "color": color.lower(),
                "vertices": int(len(mesh.vertices)),
                "triangles": int(len(mesh.faces)),
            }
        )
    model.QueryWriter("3mf").WriteToFile(str(output_path))
    return {"path": output_path, "object_count": len(objects), "objects": objects}


def inspect_3mf(path: str) -> dict:
    wrapper = _wrapper()
    model = wrapper.CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))
    objects = []
    iterator = model.GetMeshObjects()
    while iterator.MoveNext():
        mesh = iterator.GetCurrentMeshObject()
        objects.append(
            {
                "id": mesh.GetName(),
                "vertices": int(mesh.GetVertexCount()),
                "triangles": int(mesh.GetTriangleCount()),
            }
        )
    return {"path": path, "object_count": len(objects), "objects": objects}
