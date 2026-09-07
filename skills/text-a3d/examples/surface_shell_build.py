"""Build only through a3d compile; a single mesh master owns the whole shell."""
import os
from pathlib import Path

import numpy as np
import trimesh
from authoring import write_scene
from geometry_binding import bind_mesh_feature

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", ROOT))
INTENT = os.environ["AMAGINE3D_INTENT_PATH"]
SCENE = os.environ.get("AMAGINE3D_SCENE_PATH", str(ROOT / "surface_shell_scene.json"))

# Dimensions are millimetres; inset is measured in each horizontal section.
WIDTH, DEPTH, HEIGHT = 100.0, 80.0, 90.0
WALL_INSET, FLOOR = 3.0, 3.0
LOWER_SHOULDER, UPPER_SHOULDER = 0.12, 0.18
SECTION_EXPONENT, AROUND, LEVELS = 3.2, 96, 41
assert 0 < FLOOR < HEIGHT and 0 < WALL_INSET < min(WIDTH, DEPTH) / 4
assert 0 <= LOWER_SHOULDER < 0.4 and 0 <= UPPER_SHOULDER < 0.4


def smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t ** 3 * (10.0 + t * (-15.0 + 6.0 * t))


def section(z, inset):
    t = z / HEIGHT
    scale = 1 - LOWER_SHOULDER * (1 - smooth(t / 0.3)) - UPPER_SHOULDER * smooth((t - 0.65) / 0.35)
    angle = np.arange(AROUND) * (2 * np.pi / AROUND)
    c, s = np.cos(angle), np.sin(angle)
    a, b = WIDTH * scale / 2, DEPTH * scale / 2
    x = a * np.sign(c) * np.abs(c) ** (2 / SECTION_EXPONENT)
    y = b * np.sign(s) * np.abs(s) ** (2 / SECTION_EXPONENT)
    # Offset along the section's analytic normal; this preserves real corners
    # more faithfully than scaling the inner ring by a constant percentage.
    normal = np.column_stack((np.sign(x) * np.abs(x / a) ** (SECTION_EXPONENT - 1) / a,
                              np.sign(y) * np.abs(y / b) ** (SECTION_EXPONENT - 1) / b))
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    return np.column_stack((x - inset * normal[:, 0], y - inset * normal[:, 1], np.full(AROUND, z)))


rings = [section(z, 0) for z in np.linspace(0, HEIGHT, LEVELS)]
rings += [section(z, WALL_INSET) for z in np.linspace(FLOOR, HEIGHT, LEVELS)]
vertices = np.vstack(rings + [np.array([[0, 0, 0], [0, 0, FLOOR]])])
faces = []


def bridge(first, second, inward=False):
    for k in range(AROUND):
        a, b = first * AROUND + k, first * AROUND + (k + 1) % AROUND
        c, d = second * AROUND + (k + 1) % AROUND, second * AROUND + k
        for face in ([a, b, c], [a, c, d]):
            faces.append(face[::-1] if inward else face)


for level in range(LEVELS - 1):
    bridge(level, level + 1)
    bridge(LEVELS + level, LEVELS + level + 1, inward=True)
bridge(LEVELS - 1, 2 * LEVELS - 1)  # Annular top rim, never a lid over the cavity.
for k in range(AROUND):
    nxt = (k + 1) % AROUND
    faces.append([len(vertices) - 2, nxt, k])
    faces.append([len(vertices) - 1, LEVELS * AROUND + k, LEVELS * AROUND + nxt])
shell = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
node = bind_mesh_feature(node_id="shell-node", feature_id="shell-surface", role="solid",
                         mesh=shell, path=OUT / "surface_shell-geometry.stl")
write_scene(SCENE, intent_path=INTENT,
            parts={"surface-shell": {"representationMaster": "mesh", "nodes": [node]}})
# The public compiler continues with hybrid export, QA and rendering from here.
