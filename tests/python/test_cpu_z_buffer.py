from __future__ import annotations

import ast
from pathlib import Path
import sys
import time
import unittest

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from cpu_z_buffer import (  # noqa: E402
    BACKGROUND,
    CONTACT_VIEWS,
    SUPPORTED_VIEWS,
    MeshInput,
    RenderLimits,
    render_contact_sheet,
    render_view,
)


def _box(
    name: str,
    extents: tuple[float, float, float],
    center: tuple[float, float, float],
    color: tuple[int, int, int],
) -> MeshInput:
    transform = np.eye(4)
    transform[:3, 3] = center
    mesh = trimesh.creation.box(extents=extents, transform=transform)
    return MeshInput(name, mesh, color)


def _array(rendered) -> np.ndarray:
    return np.asarray(rendered.image)


def _foreground(image: np.ndarray) -> np.ndarray:
    return np.any(image != np.asarray(BACKGROUND, dtype=np.uint8), axis=2)


class CpuZBufferRegressionTests(unittest.TestCase):
    def test_all_supported_cameras_render_a_stable_region(self) -> None:
        cube = _box("cube", (4.0, 3.0, 2.0), (0.0, 0.0, 0.0), (120, 170, 210))
        coverages = {}
        for view in SUPPORTED_VIEWS:
            rendered = render_view([cube], view, 192)
            mask = _foreground(_array(rendered))
            coverages[view] = float(mask.mean())
            self.assertGreater(coverages[view], 0.10, view)
            self.assertLess(coverages[view], 0.90, view)
            self.assertGreater(rendered.stats.triangles_rasterized, 0, view)

        # Opposite orthographic cameras must agree on the box's outline area.
        self.assertLess(abs(coverages["top"] - coverages["bottom"]), 0.01)
        self.assertEqual(
            CONTACT_VIEWS,
            ("isometric", "front", "side", "top", "bottom"),
        )

    def test_double_through_hole_plate_has_no_false_top_face_groove(self) -> None:
        # Five coplanar solids form a plate with two fully enclosed square holes.
        # Their top faces contain many independent triangles and shared seams.
        pieces = [
            _box("top-rail", (12.0, 2.0, 1.0), (0.0, 2.0, 0.0), (122, 163, 199)),
            _box("bottom-rail", (12.0, 2.0, 1.0), (0.0, -2.0, 0.0), (122, 163, 199)),
            _box("left-web", (2.5, 2.0, 1.0), (-4.75, 0.0, 0.0), (122, 163, 199)),
            _box("center-web", (3.0, 2.0, 1.0), (0.0, 0.0, 0.0), (122, 163, 199)),
            _box("right-web", (2.5, 2.0, 1.0), (4.75, 0.0, 0.0), (122, 163, 199)),
        ]
        image = _array(render_view(pieces, "top", 256))
        mask = _foreground(image)

        # Flat coplanar faces have one shaded material color: no diagonal line,
        # triangle shadow, or fake recessed seam is allowed inside the plate.
        solid_colors = np.unique(image[mask], axis=0)
        self.assertEqual(len(solid_colors), 1, solid_colors)

        center_y = image.shape[0] // 2
        self.assertTrue(np.all(image[center_y, 73] == BACKGROUND))
        self.assertTrue(np.all(image[center_y, 183] == BACKGROUND))
        self.assertTrue(np.any(image[center_y, 128] != BACKGROUND))

    def test_groove_and_step_keep_their_vertical_occlusion_order(self) -> None:
        regions = [
            _box("left-base", (2.0, 2.0, 1.0), (-2.0, 0.0, 0.5), (100, 170, 110)),
            _box("groove-floor", (2.0, 2.0, 0.3), (0.0, 0.0, 0.15), (60, 110, 220)),
            _box("right-base", (2.0, 2.0, 1.0), (2.0, 0.0, 0.5), (100, 170, 110)),
            _box("step", (1.2, 2.0, 1.0), (2.0, 0.0, 1.5), (230, 120, 45)),
        ]
        image = _array(render_view(regions, "front", 256))
        nonwhite = _foreground(image)
        self.assertGreater(float(nonwhite.mean()), 0.10)

        orange = (image[:, :, 0] > image[:, :, 1] * 1.4) & nonwhite
        blue = (image[:, :, 2] > image[:, :, 0] * 2.0) & nonwhite
        green = (image[:, :, 1] > image[:, :, 0] * 1.3) & nonwhite
        self.assertGreater(int(orange.sum()), 100)
        self.assertGreater(int(blue.sum()), 100)
        self.assertGreater(int(green.sum()), 100)
        self.assertLess(float(np.argwhere(orange)[:, 0].mean()), float(np.argwhere(green)[:, 0].mean()))
        self.assertGreater(float(np.argwhere(blue)[:, 0].mean()), float(np.argwhere(orange)[:, 0].mean()))

    def test_near_entity_covers_far_entity_even_when_far_is_submitted_last(self) -> None:
        near = _box("near", (3.0, 0.5, 3.0), (0.0, -2.0, 0.0), (255, 20, 20))
        far = _box("far", (3.0, 0.5, 3.0), (0.0, 2.0, 0.0), (20, 20, 255))
        image = _array(render_view([near, far], "front", 192))
        center = image[96, 96]
        self.assertGreater(int(center[0]), int(center[2]) * 4, center)

    def test_adjacent_color_regions_have_no_background_crack_or_cross_fill(self) -> None:
        left = _box("red", (2.0, 3.0, 1.0), (-1.0, 0.0, 0.0), (240, 30, 30))
        right = _box("blue", (2.0, 3.0, 1.0), (1.0, 0.0, 0.0), (30, 30, 240))
        image = _array(render_view([left, right], "top", 256))
        row = image[128]
        occupied = np.any(row != BACKGROUND, axis=1)
        indices = np.flatnonzero(occupied)
        self.assertGreater(len(indices), 100)
        self.assertTrue(occupied[indices[0] : indices[-1] + 1].all())

        left_half = row[indices[0] : 128]
        right_half = row[129 : indices[-1] + 1]
        self.assertGreater(float(np.mean(left_half[:, 0] > left_half[:, 2])), 0.98)
        self.assertGreater(float(np.mean(right_half[:, 2] > right_half[:, 0])), 0.98)

    def test_limits_fail_closed_and_two_x_is_optional(self) -> None:
        cube = _box("cube", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (120, 170, 210))
        with self.assertRaisesRegex(ValueError, "configured maximum"):
            render_view([cube], "top", 128, limits=RenderLimits(max_triangles=11))
        with self.assertRaisesRegex(ValueError, "supersample"):
            render_view([cube], "top", 128, supersample=3)
        with self.assertRaisesRegex(ValueError, "resolution"):
            render_view(
                [cube],
                "top",
                200,
                supersample=2,
                limits=RenderLimits(max_resolution=320),
            )
        with self.assertRaisesRegex(ValueError, "between 320 and 2048"):
            RenderLimits(max_resolution=2049)
        rendered = render_view([cube], "top", 128, supersample=2)
        self.assertEqual(rendered.image.size, (128, 128))

    def test_contact_sheet_is_sequential_and_reuses_one_panel_buffer(self) -> None:
        cube = _box("cube", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (120, 170, 210))
        rendered = render_contact_sheet([cube], 320, title="test")
        self.assertEqual(rendered.image.size, (320, 320))
        self.assertEqual([item.view for item in rendered.stats], list(CONTACT_VIEWS))
        expected_peak = max(item.buffer_bytes for item in rendered.stats)
        self.assertEqual(rendered.peak_buffer_bytes, expected_peak)

    def test_medium_mesh_runtime_stays_bounded_without_parallel_workers(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=2.0)
        model = MeshInput("medium", mesh, (120, 170, 210))
        started = time.perf_counter()
        rendered = render_contact_sheet([model], 320, title="medium")
        elapsed = time.perf_counter() - started
        self.assertEqual(rendered.stats[0].triangles_input, len(mesh.faces))
        self.assertLess(elapsed, 30.0)
        self.assertLess(rendered.peak_buffer_bytes, 2 * 1024 * 1024)

    def test_renderer_imports_only_headless_cpu_image_dependencies(self) -> None:
        source = (SKILL / "cpu_z_buffer.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue({"numpy", "PIL", "trimesh"}.issubset(imports))
        self.assertTrue(
            {"matplotlib", "OpenGL", "pyvista", "pyrender", "multiprocessing"}.isdisjoint(imports)
        )


if __name__ == "__main__":
    unittest.main()
