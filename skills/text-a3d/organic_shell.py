"""Construct printable freeform shells from signed-distance fields.

The public contract is deliberately shape-agnostic: callers supply a signed
distance field in millimetres, so the outer form can follow user landmarks
instead of a hard-coded sphere or ellipsoid.  Manifold's level-set mesher and
booleans produce the canonical watertight mesh; no hole-filling repair pass is
part of this authoring path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Literal, Sequence

import manifold3d
import numpy as np
import trimesh


SignedDistance = Callable[[float, float, float], float]
FootprintDistance = Callable[[float, float], float]
CavityStrategy = Literal["open-cavity", "self-supporting-cavity"]
SHELL_SCHEMA = "evidence-organic-shell/v1"


class OrganicShellError(ValueError):
    """Raised when a requested field cannot produce a reliable print shell."""


@dataclass(frozen=True)
class OrganicShell:
    """One canonical manufacturing mesh plus its constructive evidence."""

    mesh: trimesh.Trimesh
    evidence: dict[str, Any]


@dataclass(frozen=True)
class SelfSupportingCavity:
    """A +Z-printable cavity whose roof closes through sloped contours."""

    footprint_sdf: FootprintDistance
    floor_z_mm: float
    roof_start_z_mm: float
    roof_angle_from_horizontal_deg: float
    closure_inset_mm: float
    sampled_inradius_upper_mm: float

    @property
    def apex_z_mm(self) -> float:
        angle = math.radians(self.roof_angle_from_horizontal_deg)
        return self.roof_start_z_mm + self.closure_inset_mm * math.tan(angle)

    def __call__(self, x: float, y: float, z: float) -> float:
        angle = math.radians(self.roof_angle_from_horizontal_deg)
        roof_inset = max(
            0.0,
            (float(z) - self.roof_start_z_mm) / math.tan(angle),
        )
        return min(
            float(self.footprint_sdf(float(x), float(y))) - roof_inset,
            float(z) - self.floor_z_mm,
            self.apex_z_mm - float(z),
        )


def _finite_positive(value: float, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise OrganicShellError(f"{name} must be finite and positive")
    return float(value)


def _bounds_3d(bounds_mm: Sequence[float]) -> tuple[float, ...]:
    if len(bounds_mm) != 6:
        raise OrganicShellError(
            "bounds_mm must be (min_x, min_y, min_z, max_x, max_y, max_z)"
        )
    bounds = tuple(float(value) for value in bounds_mm)
    if not all(math.isfinite(value) for value in bounds):
        raise OrganicShellError("bounds_mm must contain only finite values")
    if any(bounds[index] >= bounds[index + 3] for index in range(3)):
        raise OrganicShellError("every bounds_mm minimum must be below its maximum")
    return bounds


def _bounds_2d(bounds_mm: Sequence[float]) -> tuple[float, ...]:
    if len(bounds_mm) != 4:
        raise OrganicShellError(
            "footprint_bounds_mm must be (min_x, min_y, max_x, max_y)"
        )
    bounds = tuple(float(value) for value in bounds_mm)
    if not all(math.isfinite(value) for value in bounds):
        raise OrganicShellError(
            "footprint_bounds_mm must contain only finite values"
        )
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise OrganicShellError(
            "every footprint_bounds_mm minimum must be below its maximum"
        )
    return bounds


def _field_value(field: SignedDistance, point: Sequence[float], context: str) -> float:
    try:
        value = float(field(float(point[0]), float(point[1]), float(point[2])))
    except Exception as error:
        raise OrganicShellError(f"{context} failed at {tuple(point)}: {error}") from error
    if not math.isfinite(value):
        raise OrganicShellError(f"{context} returned a non-finite value at {tuple(point)}")
    return value


def _require_field_inside_bounds(
    field: SignedDistance,
    bounds: tuple[float, ...],
    context: str,
) -> None:
    """Reject fields that reach the sampling box and would be silently clipped."""

    axes = [
        np.linspace(bounds[index], bounds[index + 3], 5, dtype=float)
        for index in range(3)
    ]
    for fixed_axis in range(3):
        free_axes = [axis for axis in range(3) if axis != fixed_axis]
        for fixed_value in (bounds[fixed_axis], bounds[fixed_axis + 3]):
            for first in axes[free_axes[0]]:
                for second in axes[free_axes[1]]:
                    point = [0.0, 0.0, 0.0]
                    point[fixed_axis] = fixed_value
                    point[free_axes[0]] = float(first)
                    point[free_axes[1]] = float(second)
                    if _field_value(field, point, context) >= 0:
                        raise OrganicShellError(
                            f"{context} reaches bounds_mm at {tuple(point)}; "
                            "expand the bounds instead of accepting a clipped surface"
                        )


def _level_set(
    field: SignedDistance,
    bounds: tuple[float, ...],
    edge_length_mm: float,
    level_mm: float,
    tolerance_mm: float,
    context: str,
) -> manifold3d.Manifold:
    try:
        body = manifold3d.Manifold.level_set(
            field,
            bounds,
            edge_length_mm,
            level_mm,
            tolerance_mm,
        )
    except Exception as error:
        raise OrganicShellError(f"{context} level-set construction failed: {error}") from error
    if body.status() != manifold3d.Error.NoError:
        raise OrganicShellError(
            f"{context} level-set construction failed with {body.status()}"
        )
    if body.num_tri() == 0 or body.volume() <= 0:
        raise OrganicShellError(f"{context} level set is empty")
    return body


def _require_manifold(body: manifold3d.Manifold, context: str) -> None:
    if body.status() != manifold3d.Error.NoError:
        raise OrganicShellError(f"{context} failed with {body.status()}")
    if body.num_tri() == 0 or body.volume() <= 0:
        raise OrganicShellError(f"{context} produced no positive volume")


def _positive_body_count(body: manifold3d.Manifold) -> int:
    """Count material bodies while excluding negative cavity surfaces."""

    return sum(component.volume() > 0 for component in body.decompose())


def _to_trimesh(body: manifold3d.Manifold) -> trimesh.Trimesh:
    raw = body.to_mesh64()
    mesh = trimesh.Trimesh(
        vertices=np.asarray(raw.vert_properties, dtype=np.float64)[:, :3],
        faces=np.asarray(raw.tri_verts, dtype=np.int64),
        process=False,
    )
    mesh.remove_unreferenced_vertices()
    if mesh.volume < 0 and mesh.is_watertight:
        mesh.invert()
    if not mesh.is_watertight or not mesh.is_winding_consistent or not mesh.is_volume:
        raise OrganicShellError(
            "Manifold returned a mesh that is not a watertight positive volume"
        )
    return mesh


def self_supporting_cavity(
    *,
    footprint_sdf: FootprintDistance,
    footprint_bounds_mm: Sequence[float],
    floor_z_mm: float,
    roof_start_z_mm: float,
    closure_inset_mm: float,
    roof_angle_from_horizontal_deg: float = 45.0,
    verification_samples: int = 33,
) -> SelfSupportingCavity:
    """Create a cavity with no flat internal ceiling in the +Z print direction.

    ``footprint_sdf`` must be a signed distance in millimetres: positive inside
    its arbitrary 2D footprint and negative outside.  The roof shrinks that
    footprint on every layer until it closes.  A Lipschitz sampling bound
    rejects an insufficient closure inset before a 3D mesh is generated.
    """

    bounds = _bounds_2d(footprint_bounds_mm)
    floor = float(floor_z_mm)
    roof_start = float(roof_start_z_mm)
    closure = _finite_positive(closure_inset_mm, "closure_inset_mm")
    angle = float(roof_angle_from_horizontal_deg)
    if not math.isfinite(floor) or not math.isfinite(roof_start) or roof_start <= floor:
        raise OrganicShellError(
            "floor_z_mm and roof_start_z_mm must be finite, with the roof above the floor"
        )
    if not math.isfinite(angle) or not 45.0 <= angle < 90.0:
        raise OrganicShellError(
            "roof_angle_from_horizontal_deg must be in [45, 90) for support-free +Z printing"
        )
    if not isinstance(verification_samples, int) or verification_samples < 9:
        raise OrganicShellError("verification_samples must be an integer of at least 9")

    xs = np.linspace(bounds[0], bounds[2], verification_samples, dtype=float)
    ys = np.linspace(bounds[1], bounds[3], verification_samples, dtype=float)
    sampled_max = -math.inf
    for x in xs:
        for y in ys:
            try:
                value = float(footprint_sdf(float(x), float(y)))
            except Exception as error:
                raise OrganicShellError(
                    f"footprint_sdf failed at {(float(x), float(y))}: {error}"
                ) from error
            if not math.isfinite(value):
                raise OrganicShellError(
                    f"footprint_sdf returned a non-finite value at {(float(x), float(y))}"
                )
            sampled_max = max(sampled_max, value)
    spacing_x = (bounds[2] - bounds[0]) / (verification_samples - 1)
    spacing_y = (bounds[3] - bounds[1]) / (verification_samples - 1)
    sampled_upper = max(0.0, sampled_max) + 0.5 * math.hypot(spacing_x, spacing_y)
    if closure + 1e-9 < sampled_upper:
        raise OrganicShellError(
            f"closure_inset_mm {closure:g} is below the sampled footprint "
            f"inradius upper bound {sampled_upper:.6g}; the cavity would end in a flat ceiling"
        )

    return SelfSupportingCavity(
        footprint_sdf=footprint_sdf,
        floor_z_mm=floor,
        roof_start_z_mm=roof_start,
        roof_angle_from_horizontal_deg=angle,
        closure_inset_mm=closure,
        sampled_inradius_upper_mm=sampled_upper,
    )


def build_organic_shell(
    *,
    outer_sdf: SignedDistance,
    bounds_mm: Sequence[float],
    wall_thickness_mm: float,
    edge_length_mm: float,
    cavity_strategy: CavityStrategy,
    opening_sdfs: Sequence[SignedDistance] = (),
    interior_sdf: SelfSupportingCavity | None = None,
    surface_tolerance_mm: float | None = None,
) -> OrganicShell:
    """Build one watertight irregular shell with a declared print strategy.

    The outer field must return signed distance in millimetres, positive inside.
    ``open-cavity`` derives the inner surface from that same field and requires
    at least one opening field that connects the cavity to the exterior.
    ``self-supporting-cavity`` requires an interior made by
    :func:`self_supporting_cavity`; it is checked against the safe wall inset.
    """

    bounds = _bounds_3d(bounds_mm)
    wall = _finite_positive(wall_thickness_mm, "wall_thickness_mm")
    edge = _finite_positive(edge_length_mm, "edge_length_mm")
    tolerance = (
        min(edge * 0.2, wall * 0.1)
        if surface_tolerance_mm is None
        else _finite_positive(surface_tolerance_mm, "surface_tolerance_mm")
    )
    if edge > wall * 0.5:
        raise OrganicShellError(
            "edge_length_mm must be at most half wall_thickness_mm so the wall is resolved by the grid"
        )
    if tolerance > wall * 0.125:
        raise OrganicShellError(
            "surface_tolerance_mm must be at most one eighth of wall_thickness_mm"
        )
    if cavity_strategy not in {"open-cavity", "self-supporting-cavity"}:
        raise OrganicShellError(
            "cavity_strategy must be 'open-cavity' or 'self-supporting-cavity'"
        )
    openings = tuple(opening_sdfs)
    if cavity_strategy == "open-cavity":
        if not openings:
            raise OrganicShellError(
                "open-cavity requires at least one opening_sdf that reaches the exterior"
            )
        if interior_sdf is not None:
            raise OrganicShellError(
                "open-cavity derives its inner wall from outer_sdf; interior_sdf is not accepted"
            )
    else:
        if not isinstance(interior_sdf, SelfSupportingCavity):
            raise OrganicShellError(
                "self-supporting-cavity requires interior_sdf from self_supporting_cavity()"
            )
        if openings:
            raise OrganicShellError(
                "self-supporting-cavity is a closed strategy; use open-cavity when service openings exist"
            )

    _require_field_inside_bounds(outer_sdf, bounds, "outer_sdf")
    outer = _level_set(
        outer_sdf,
        bounds,
        edge,
        0.0,
        tolerance,
        "outer_sdf",
    )
    safe_inner = _level_set(
        outer_sdf,
        bounds,
        edge,
        wall,
        tolerance,
        "wall inset",
    )
    if _positive_body_count(outer) != 1:
        raise OrganicShellError("outer_sdf must describe exactly one physical body")
    if _positive_body_count(safe_inner) != 1:
        raise OrganicShellError(
            "wall inset collapsed or split; increase local radii, reduce wall thickness, or revise the field"
        )

    cavity = safe_inner
    roof_evidence: dict[str, Any] | None = None
    if interior_sdf is not None:
        _require_field_inside_bounds(interior_sdf, bounds, "interior_sdf")
        cavity = _level_set(
            interior_sdf,
            bounds,
            edge,
            0.0,
            tolerance,
            "self-supporting cavity",
        )
        outside_safe_wall = cavity - safe_inner
        if outside_safe_wall.volume() > max(1e-6, cavity.volume() * 1e-9):
            raise OrganicShellError(
                "self-supporting cavity crosses the wall-thickness inset; shrink or reposition it"
            )
        roof_evidence = {
            "apexZMm": interior_sdf.apex_z_mm,
            "closureInsetMm": interior_sdf.closure_inset_mm,
            "roofAngleFromHorizontalDeg": (
                interior_sdf.roof_angle_from_horizontal_deg
            ),
            "sampledInradiusUpperMm": interior_sdf.sampled_inradius_upper_mm,
        }

    if openings:
        opening_bodies: list[manifold3d.Manifold] = []
        for index, opening_sdf in enumerate(openings):
            # An opening is a cutter and may intentionally extend through the
            # sampling box beyond the outer body.  Level-set clipping is safe
            # here because only its intersection with ``outer`` is retained.
            opening_bodies.append(
                _level_set(
                    opening_sdf,
                    bounds,
                    edge,
                    0.0,
                    tolerance,
                    f"opening_sdfs[{index}]",
                )
            )
        opening_union = manifold3d.Manifold.batch_boolean(
            opening_bodies,
            manifold3d.OpType.Add,
        )
        _require_manifold(opening_union, "opening union")
        cavity = cavity + opening_union

    shell_body = outer - cavity
    _require_manifold(shell_body, "organic shell")
    physical_bodies = _positive_body_count(shell_body)
    if physical_bodies != 1:
        raise OrganicShellError(
            f"organic shell must be one physical body; got {physical_bodies}"
        )
    mesh = _to_trimesh(shell_body)
    surface_components = len(mesh.split(only_watertight=False))
    if cavity_strategy == "open-cavity" and surface_components != 1:
        raise OrganicShellError(
            "opening_sdfs do not connect the inner cavity to the exterior"
        )

    evidence = {
        "boundsMm": {
            "min": list(bounds[:3]),
            "max": list(bounds[3:]),
        },
        "cavityStrategy": cavity_strategy,
        "edgeLengthMm": edge,
        "openingCount": len(openings),
        "physicalBodyCount": physical_bodies,
        "printDirection": "+Z",
        "schema": SHELL_SCHEMA,
        "surfaceComponentCount": surface_components,
        "surfaceToleranceMm": tolerance,
        "wallThicknessMm": wall,
        **({"selfSupportingRoof": roof_evidence} if roof_evidence else {}),
    }
    return OrganicShell(mesh=mesh, evidence=evidence)
