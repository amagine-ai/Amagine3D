"""Headless orthographic rendering with a bounded, single-process CPU Z-buffer.

trimesh owns mesh I/O and geometry data, NumPy owns projection and triangle
coverage, and Pillow owns image output.  The module deliberately has no window,
GPU, OpenGL, or multiprocessing dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import time
from typing import Iterable, Sequence

# Keep vendor BLAS implementations from turning small camera transforms into a
# multi-core workload. Callers can explicitly override these before startup.
for _thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_variable, "1")

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import trimesh


HARD_MAX_RESOLUTION = 2048
HARD_MAX_TRIANGLES = 2_000_000
DEFAULT_OUTPUT_SIZE = 640
DEFAULT_MAX_RESOLUTION = 1280
DEFAULT_MAX_TRIANGLES = 500_000
MAX_SUPERSAMPLE = 2
CONTACT_VIEWS = ("isometric", "front", "side", "top", "bottom")
SUPPORTED_VIEWS = CONTACT_VIEWS
BACKGROUND = (255, 255, 255)
DEFAULT_MATERIAL = (122, 163, 199)
LIGHT = np.array((0.35, -0.55, 0.76), dtype=np.float64)
LIGHT /= np.linalg.norm(LIGHT)


def _camera_direction(elevation: float, azimuth: float) -> tuple[float, float, float]:
    elevation_radians = math.radians(elevation)
    azimuth_radians = math.radians(azimuth)
    return (
        math.cos(elevation_radians) * math.cos(azimuth_radians),
        math.cos(elevation_radians) * math.sin(azimuth_radians),
        math.sin(elevation_radians),
    )


CAMERA_DIRECTIONS = {
    "isometric": _camera_direction(28.0, 42.0),
    "front": _camera_direction(0.0, -90.0),
    "side": _camera_direction(0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}


@dataclass(frozen=True)
class RenderLimits:
    """Per-invocation safety limits; hard caps cannot be raised from the CLI."""

    max_resolution: int = DEFAULT_MAX_RESOLUTION
    max_triangles: int = DEFAULT_MAX_TRIANGLES
    max_supersample: int = MAX_SUPERSAMPLE

    def __post_init__(self) -> None:
        if not 320 <= self.max_resolution <= HARD_MAX_RESOLUTION:
            raise ValueError(
                f"max resolution must be between 320 and {HARD_MAX_RESOLUTION}"
            )
        if not 1 <= self.max_triangles <= HARD_MAX_TRIANGLES:
            raise ValueError(
                f"max triangles must be between 1 and {HARD_MAX_TRIANGLES}"
            )
        if not 1 <= self.max_supersample <= MAX_SUPERSAMPLE:
            raise ValueError(f"max supersample must be between 1 and {MAX_SUPERSAMPLE}")


@dataclass(frozen=True)
class MeshInput:
    name: str
    mesh: trimesh.Trimesh
    color: tuple[int, int, int] = DEFAULT_MATERIAL
    path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mesh, trimesh.Trimesh) or self.mesh.is_empty:
            raise ValueError(f"no renderable mesh in {self.name}")
        if not np.isfinite(self.mesh.vertices).all():
            raise ValueError(f"non-finite mesh vertices in {self.name}")
        if len(self.color) != 3 or any(value < 0 or value > 255 for value in self.color):
            raise ValueError(f"invalid RGB color for {self.name}: {self.color!r}")


@dataclass
class RenderStats:
    view: str
    elapsed_seconds: float
    triangles_input: int
    backface_culled: int
    frustum_culled: int
    frustum_clipped: int
    screen_culled: int
    degenerate_culled: int
    triangles_rasterized: int
    covered_pixels: int
    buffer_bytes: int

    def to_dict(self) -> dict[str, int | float | str]:
        result = asdict(self)
        result["elapsed_seconds"] = round(self.elapsed_seconds, 6)
        return result


@dataclass
class ViewRender:
    image: Image.Image
    stats: RenderStats


@dataclass
class ContactRender:
    image: Image.Image
    stats: list[RenderStats]
    elapsed_seconds: float
    peak_buffer_bytes: int


class BufferPool:
    """One depth/color allocation reused by sequential view renders."""

    def __init__(self) -> None:
        self.depth: np.ndarray | None = None
        self.color: np.ndarray | None = None
        self.max_bytes = 0

    def prepare(
        self,
        width: int,
        height: int,
        background: tuple[int, int, int] = BACKGROUND,
    ) -> tuple[np.ndarray, np.ndarray]:
        shape = (height, width)
        if self.depth is None or self.depth.shape != shape:
            self.depth = np.empty(shape, dtype=np.float32)
            self.color = np.empty((*shape, 3), dtype=np.uint8)
        assert self.color is not None
        self.depth.fill(-np.inf)
        self.color[:, :] = background
        self.max_bytes = max(self.max_bytes, self.depth.nbytes + self.color.nbytes)
        return self.depth, self.color


def load_mesh(path: Path | str, *, process: bool = True) -> trimesh.Trimesh:
    source = Path(path).resolve()
    mesh = trimesh.load(source, force="mesh", process=process)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise ValueError(f"no renderable mesh in {source}")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"non-finite mesh vertices in {source}")
    return mesh


def triangle_count(meshes: Sequence[MeshInput]) -> int:
    return sum(len(item.mesh.faces) for item in meshes)


def mesh_bounds(meshes: Sequence[MeshInput]) -> tuple[np.ndarray, np.ndarray]:
    _validate_meshes(meshes)
    bounds = np.array([item.mesh.bounds for item in meshes], dtype=np.float64)
    return bounds[:, 0].min(axis=0), bounds[:, 1].max(axis=0)


def _validate_meshes(meshes: Sequence[MeshInput]) -> None:
    if not meshes:
        raise ValueError("at least one mesh is required")


def _validate_request(
    meshes: Sequence[MeshInput],
    width: int,
    height: int,
    supersample: int,
    limits: RenderLimits,
) -> None:
    _validate_meshes(meshes)
    if width < 1 or height < 1:
        raise ValueError("render width and height must be positive")
    if supersample < 1 or supersample > limits.max_supersample:
        raise ValueError(
            f"supersample must be between 1 and {limits.max_supersample}"
        )
    if max(width, height) * supersample > limits.max_resolution:
        raise ValueError(
            "internal render resolution exceeds the configured maximum "
            f"of {limits.max_resolution} pixels"
        )
    count = triangle_count(meshes)
    if count > limits.max_triangles:
        raise ValueError(
            f"mesh has {count} triangles; configured maximum is {limits.max_triangles}"
        )


def _camera_basis(view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if view not in CAMERA_DIRECTIONS:
        raise ValueError(f"unsupported view {view!r}; choose from {SUPPORTED_VIEWS}")
    eye = np.asarray(CAMERA_DIRECTIONS[view], dtype=np.float64)
    eye /= np.linalg.norm(eye)
    up_hint = (
        np.array((0.0, 1.0, 0.0), dtype=np.float64)
        if abs(float(eye[2])) > 0.95
        else np.array((0.0, 0.0, 1.0), dtype=np.float64)
    )
    right = np.cross(up_hint, eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    up /= np.linalg.norm(up)
    return right, up, eye


def _face_colors(mesh: trimesh.Trimesh, base_color: tuple[int, int, int]) -> np.ndarray:
    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    facing_light = np.einsum("ij,j->i", normals, LIGHT)
    intensity = 0.42 + 0.58 * np.clip(facing_light, 0.0, 1.0)
    base = np.asarray(base_color, dtype=np.float64)
    return np.clip(np.rint(intensity[:, None] * base[None, :]), 0, 255).astype(
        np.uint8
    )


def _edge(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    x: np.ndarray,
    y: np.ndarray,
) -> np.ndarray:
    return (bx - ax) * (y - ay) - (by - ay) * (x - ax)


def _clip_polygon_axis(
    polygon: np.ndarray,
    axis: int,
    boundary: float,
    keep_greater: bool,
) -> np.ndarray:
    if len(polygon) == 0:
        return polygon

    def inside(point: np.ndarray) -> bool:
        return bool(
            point[axis] >= boundary if keep_greater else point[axis] <= boundary
        )

    output: list[np.ndarray] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside != previous_inside:
            denominator = current[axis] - previous[axis]
            amount = 0.0 if abs(float(denominator)) < 1e-15 else (
                boundary - previous[axis]
            ) / denominator
            output.append(previous + amount * (current - previous))
        if current_inside:
            output.append(current)
        previous = current
        previous_inside = current_inside
    return np.asarray(output, dtype=np.float64).reshape((-1, 3))


def _clip_to_viewport(
    triangle: np.ndarray,
    width: int,
    height: int,
    near_depth: float,
    far_depth: float,
) -> np.ndarray:
    polygon = triangle
    for axis, boundary, keep_greater in (
        (0, 0.0, True),
        (0, float(width), False),
        (1, 0.0, True),
        (1, float(height), False),
        (2, near_depth, True),
        (2, far_depth, False),
    ):
        polygon = _clip_polygon_axis(polygon, axis, boundary, keep_greater)
        if len(polygon) < 3:
            break
    return polygon


def _rasterize_triangle(
    triangle: np.ndarray,
    face_color: np.ndarray,
    depth_buffer: np.ndarray,
    color_buffer: np.ndarray,
    depth_epsilon: float,
) -> bool:
    height, width = depth_buffer.shape
    points = triangle.copy()
    area = float(
        _edge(
            points[0, 0],
            points[0, 1],
            points[1, 0],
            points[1, 1],
            np.asarray(points[2, 0]),
            np.asarray(points[2, 1]),
        )
    )
    if abs(area) <= 1e-10:
        return False
    if area < 0.0:
        points[[1, 2]] = points[[2, 1]]
        area = -area

    min_x = max(0, int(math.ceil(float(points[:, 0].min()) - 0.5)))
    max_x = min(width - 1, int(math.floor(float(points[:, 0].max()) - 0.5)))
    min_y = max(0, int(math.ceil(float(points[:, 1].min()) - 0.5)))
    max_y = min(height - 1, int(math.floor(float(points[:, 1].max()) - 0.5)))
    if min_x > max_x or min_y > max_y:
        return False

    xs = np.arange(min_x, max_x + 1, dtype=np.float64)[None, :] + 0.5
    edge_tolerance = max(area * 1e-12, 1e-9)
    wrote_pixel = False
    for row_start in range(min_y, max_y + 1, 32):
        row_stop = min(row_start + 32, max_y + 1)
        ys = np.arange(row_start, row_stop, dtype=np.float64)[:, None] + 0.5
        weight0 = _edge(
            points[1, 0], points[1, 1], points[2, 0], points[2, 1], xs, ys
        )
        weight1 = _edge(
            points[2, 0], points[2, 1], points[0, 0], points[0, 1], xs, ys
        )
        weight2 = _edge(
            points[0, 0], points[0, 1], points[1, 0], points[1, 1], xs, ys
        )
        covered = (
            (weight0 >= -edge_tolerance)
            & (weight1 >= -edge_tolerance)
            & (weight2 >= -edge_tolerance)
        )
        if not covered.any():
            continue
        candidate_depth = (
            weight0 * points[0, 2]
            + weight1 * points[1, 2]
            + weight2 * points[2, 2]
        ) / area
        current_depth = depth_buffer[row_start:row_stop, min_x : max_x + 1]
        nearer = covered & (candidate_depth > current_depth + depth_epsilon)
        if not nearer.any():
            continue
        current_depth[nearer] = candidate_depth[nearer]
        color_slice = color_buffer[row_start:row_stop, min_x : max_x + 1]
        color_slice[nearer] = face_color
        wrote_pixel = True
    return wrote_pixel


def _render_internal(
    meshes: Sequence[MeshInput],
    view: str,
    width: int,
    height: int,
    pool: BufferPool,
    background: tuple[int, int, int],
) -> ViewRender:
    started = time.perf_counter()
    right, up, eye = _camera_basis(view)
    basis = np.column_stack((right, up, eye))
    bounds = np.asarray([item.mesh.bounds for item in meshes], dtype=np.float64)
    world_center = (bounds[:, 0].min(axis=0) + bounds[:, 1].max(axis=0)) / 2.0
    camera_vertices = [
        np.einsum(
            "ij,jk->ik",
            np.asarray(item.mesh.vertices, dtype=np.float64) - world_center,
            basis,
        )
        for item in meshes
    ]
    camera_low = np.min(
        np.asarray([vertices.min(axis=0) for vertices in camera_vertices]), axis=0
    )
    camera_high = np.max(
        np.asarray([vertices.max(axis=0) for vertices in camera_vertices]), axis=0
    )
    screen_low = camera_low[:2]
    screen_high = camera_high[:2]
    screen_center = (screen_low + screen_high) / 2.0
    raw_span = screen_high - screen_low
    world_span = max(float((camera_high - camera_low).max()), 1e-9)
    span = np.maximum(raw_span, world_span * 1e-6)
    margin = 0.08
    scale = min(
        width / (float(span[0]) * (1.0 + margin * 2.0)),
        height / (float(span[1]) * (1.0 + margin * 2.0)),
    )
    half_x = width / (2.0 * scale)
    half_y = height / (2.0 * scale)
    near_depth = float(camera_low[2]) - world_span * 1e-6
    far_depth = float(camera_high[2]) + world_span * 1e-6
    depth_epsilon = max((far_depth - near_depth) * 1e-7, 1e-10)
    depth_buffer, color_buffer = pool.prepare(width, height, background)

    triangles_input = triangle_count(meshes)
    backface_culled = 0
    frustum_culled = 0
    frustum_clipped = 0
    screen_culled = 0
    degenerate_culled = 0
    triangles_rasterized = 0

    for item, vertices in zip(meshes, camera_vertices):
        faces = np.asarray(item.mesh.faces, dtype=np.int64)
        normals = np.asarray(item.mesh.face_normals, dtype=np.float64)
        facing = np.einsum("ij,j->i", normals, eye)
        front_mask = facing > 1e-12
        backface_culled += int((~front_mask).sum())
        if not front_mask.any():
            continue
        faces = faces[front_mask]
        colors = _face_colors(item.mesh, item.color)[front_mask]
        camera_triangles = vertices[faces]

        left = screen_center[0] - half_x
        right_plane = screen_center[0] + half_x
        bottom = screen_center[1] - half_y
        top = screen_center[1] + half_y
        outside = (
            np.all(camera_triangles[:, :, 0] < left, axis=1)
            | np.all(camera_triangles[:, :, 0] > right_plane, axis=1)
            | np.all(camera_triangles[:, :, 1] < bottom, axis=1)
            | np.all(camera_triangles[:, :, 1] > top, axis=1)
            | np.all(camera_triangles[:, :, 2] < near_depth, axis=1)
            | np.all(camera_triangles[:, :, 2] > far_depth, axis=1)
        )
        frustum_culled += int(outside.sum())
        if outside.all():
            continue
        camera_triangles = camera_triangles[~outside]
        colors = colors[~outside]

        screen_triangles = np.empty_like(camera_triangles)
        screen_triangles[:, :, 0] = (
            (camera_triangles[:, :, 0] - screen_center[0]) * scale + width / 2.0
        )
        screen_triangles[:, :, 1] = (
            (screen_center[1] - camera_triangles[:, :, 1]) * scale + height / 2.0
        )
        screen_triangles[:, :, 2] = camera_triangles[:, :, 2]
        offscreen = (
            np.all(screen_triangles[:, :, 0] < 0.0, axis=1)
            | np.all(screen_triangles[:, :, 0] > width, axis=1)
            | np.all(screen_triangles[:, :, 1] < 0.0, axis=1)
            | np.all(screen_triangles[:, :, 1] > height, axis=1)
        )
        screen_culled += int(offscreen.sum())
        if offscreen.all():
            continue
        screen_triangles = screen_triangles[~offscreen]
        colors = colors[~offscreen]

        partially_clipped = (
            np.any(screen_triangles[:, :, 0] < 0.0, axis=1)
            | np.any(screen_triangles[:, :, 0] > width, axis=1)
            | np.any(screen_triangles[:, :, 1] < 0.0, axis=1)
            | np.any(screen_triangles[:, :, 1] > height, axis=1)
            | np.any(screen_triangles[:, :, 2] < near_depth, axis=1)
            | np.any(screen_triangles[:, :, 2] > far_depth, axis=1)
        )
        frustum_clipped += int(partially_clipped.sum())

        for triangle, color, clipped in zip(
            screen_triangles, colors, partially_clipped
        ):
            polygon = (
                _clip_to_viewport(
                    triangle, width, height, near_depth, far_depth
                )
                if clipped
                else triangle
            )
            if len(polygon) < 3:
                degenerate_culled += 1
                continue
            wrote_triangle = False
            for index in range(1, len(polygon) - 1):
                clipped_triangle = np.stack(
                    (polygon[0], polygon[index], polygon[index + 1])
                )
                wrote_triangle |= _rasterize_triangle(
                    clipped_triangle,
                    color,
                    depth_buffer,
                    color_buffer,
                    depth_epsilon,
                )
            if wrote_triangle:
                triangles_rasterized += 1
            else:
                degenerate_culled += 1

    image = Image.fromarray(color_buffer.copy(), mode="RGB")
    elapsed = time.perf_counter() - started
    return ViewRender(
        image=image,
        stats=RenderStats(
            view=view,
            elapsed_seconds=elapsed,
            triangles_input=triangles_input,
            backface_culled=backface_culled,
            frustum_culled=frustum_culled,
            frustum_clipped=frustum_clipped,
            screen_culled=screen_culled,
            degenerate_culled=degenerate_culled,
            triangles_rasterized=triangles_rasterized,
            covered_pixels=int(np.count_nonzero(np.isfinite(depth_buffer))),
            buffer_bytes=depth_buffer.nbytes + color_buffer.nbytes,
        ),
    )


def render_view(
    meshes: Sequence[MeshInput],
    view: str,
    width: int,
    height: int | None = None,
    *,
    supersample: int = 1,
    limits: RenderLimits = RenderLimits(),
    pool: BufferPool | None = None,
    background: tuple[int, int, int] = BACKGROUND,
) -> ViewRender:
    output_height = height if height is not None else width
    _validate_request(meshes, width, output_height, supersample, limits)
    render_pool = pool if pool is not None else BufferPool()
    result = _render_internal(
        meshes,
        view,
        width * supersample,
        output_height * supersample,
        render_pool,
        background,
    )
    if supersample == 1:
        return result
    result.image = result.image.resize(
        (width, output_height), resample=Image.Resampling.LANCZOS
    )
    return result


def _centered_text(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    text: str,
    fill: tuple[int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    left, top, right, bottom = bounds
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2),
        text,
        fill=fill,
        font=font,
    )


def render_contact_sheet(
    meshes: Sequence[MeshInput],
    pixels: int,
    *,
    title: str,
    supersample: int = 1,
    limits: RenderLimits = RenderLimits(),
    views: Iterable[str] = CONTACT_VIEWS,
) -> ContactRender:
    requested_views = tuple(views)
    if len(requested_views) not in {4, 5}:
        raise ValueError("the contact sheet requires four or five views")
    if pixels < 320:
        raise ValueError("contact sheet size must be at least 320")
    if pixels > limits.max_resolution:
        raise ValueError(
            f"contact sheet size exceeds the configured maximum of {limits.max_resolution}"
        )

    started = time.perf_counter()
    sheet = Image.new("RGB", (pixels, pixels), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    header_height = max(34, pixels // 20)
    gutter = max(8, pixels // 75)
    label_height = max(18, pixels // 40)
    columns = 3 if len(requested_views) == 5 else 2
    rows = math.ceil(len(requested_views) / columns)
    panel_width = (pixels - gutter * (columns + 1)) // columns
    panel_height = (
        pixels - header_height - gutter * (rows + 1) - label_height * rows
    ) // rows
    if panel_width < 1 or panel_height < 1:
        raise ValueError("contact sheet size is too small for requested views")
    _validate_request(meshes, panel_width, panel_height, supersample, limits)

    _centered_text(
        draw,
        (gutter, 0, pixels - gutter, header_height),
        title,
        (36, 49, 61),
        font,
    )
    pool = BufferPool()
    stats: list[RenderStats] = []
    for index, view in enumerate(requested_views):
        column = index % columns
        row = index // columns
        left = gutter + column * (panel_width + gutter)
        top = header_height + gutter + row * (panel_height + label_height + gutter)
        rendered = render_view(
            meshes,
            view,
            panel_width,
            panel_height,
            supersample=supersample,
            limits=limits,
            pool=pool,
        )
        sheet.paste(rendered.image, (left, top))
        _centered_text(
            draw,
            (left, top + panel_height, left + panel_width, top + panel_height + label_height),
            view,
            (36, 49, 61),
            font,
        )
        stats.append(rendered.stats)
    return ContactRender(
        image=sheet,
        stats=stats,
        elapsed_seconds=time.perf_counter() - started,
        peak_buffer_bytes=pool.max_bytes,
    )
