from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import source_preflight  # noqa: E402


class SourcePreflightTests(unittest.TestCase):
    def test_reports_unbound_build_reference_before_geometry_execution(self) -> None:
        errors = source_preflight.validate_source_text(
            "from build123d import Box\nbody = Box(20, 20, 5)\nparts = {'body': body, 'fixture': missing_fixture}\n"
        )
        self.assertEqual([(e["check"], e["name"], e["line"]) for e in errors],
                         [("source-binding", "missing_fixture", 3)])

    def test_module_builtins_and_file_context_are_valid(self) -> None:
        self.assertEqual(source_preflight.validate_source_text(
            "from build123d import Box\nfrom pathlib import Path\nroot = Path(__file__).parent\nbody = Box(20, 20, max(1, 5))\n"
        ), [])

    def test_rejects_silent_finish_loss_but_allows_real_alternatives(self) -> None:
        source = """from build123d import Box, Pos
def finish(shape):
    try:
        return shape.fillet(8, shape.edges())
    except Exception:
        return Pos(0, 0, 2) * shape
"""
        errors = source_preflight.validate_source_text(source)
        self.assertEqual([e["check"] for e in errors], ["construction-fallback"])
        self.assertIn("unchanged input", errors[0]["message"])
        self.assertEqual(source_preflight.validate_source_text(source.replace(
            "return Pos(0, 0, 2) * shape", "return shape.fillet(2, shape.edges())"
        )), [])
        self.assertEqual(source_preflight.validate_source_text(source.replace(
            "return Pos(0, 0, 2) * shape", "raise"
        )), [])
        for alternative in ("return shape.cut(cutter)", "return rebuild(shape)"):
            self.assertEqual(source_preflight.validate_source_text(source.replace(
                "return Pos(0, 0, 2) * shape", alternative
            )), [])
        swallowed = """from build123d import Box
def finish(shape):
    try:
        shape = shape.fillet(8, shape.edges())
    except Exception:
        pass
    return shape
"""
        self.assertEqual([e["check"] for e in source_preflight.validate_source_text(swallowed)], ["construction-fallback"])

    def test_module_missing_name_guard_is_not_a_definite_failure(self) -> None:
        guarded = """from build123d import Box
try:
    body = optional_shape
except NameError:
    body = Box(20, 20, 5)
"""
        self.assertEqual(source_preflight.validate_source_text(guarded), [])
        errors = source_preflight.validate_source_text(guarded + "other = optional_shape\n")
        self.assertEqual([(item["check"], item["line"]) for item in errors], [("source-binding", 6)])

    def test_rejects_intent_writer_import_but_allows_scene_writer(self) -> None:
        errors = source_preflight.validate_source_text(
            "from authoring import write_intent, write_scene\nwrite_scene(...)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "contract-authoring")
        self.assertEqual(errors[0]["module"], "authoring")
        self.assertEqual(errors[0]["name"], "write_intent")

    def test_rejects_qualified_intent_writer_call(self) -> None:
        errors = source_preflight.validate_source_text(
            "import authoring as contracts\ncontracts.write_intent(...)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "contract-authoring")
        self.assertEqual(errors[0]["name"], "write_intent")

    def test_rejects_unknown_build123d_from_import(self) -> None:
        errors = source_preflight.validate_source_text(
            "from build123d import Ellipsoid, Sphere\nshape = Sphere(1)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "api-symbol")
        self.assertEqual(errors[0]["name"], "Ellipsoid")
        self.assertEqual(errors[0]["line"], 1)

    def test_rejects_build123d_symbol_used_without_import(self) -> None:
        errors = source_preflight.validate_source_text(
            "\n".join(
                [
                    "from build123d import Sphere",
                    "shape = scale(Sphere(1), by=(2, 3, 4))",
                    "",
                ]
            )
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "api-binding")
        self.assertEqual(errors[0]["name"], "scale")
        self.assertEqual(errors[0]["line"], 2)

    def test_rejects_unknown_attribute_on_build123d_module_alias(self) -> None:
        errors = source_preflight.validate_source_text(
            "import build123d as bd\nshape = bd.Ellipsoid(1, 2, 3)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "api-symbol")
        self.assertEqual(errors[0]["name"], "Ellipsoid")
        self.assertEqual(errors[0]["line"], 2)

    def test_reports_independent_api_errors_together(self) -> None:
        errors = source_preflight.validate_source_text(
            "\n".join(
                [
                    "from build123d import Ellipsoid, Sphere",
                    "shape = scale(Sphere(1), by=(2, 3, 4))",
                    "",
                ]
            )
        )
        self.assertEqual(
            {(error["check"], error["name"]) for error in errors},
            {("api-symbol", "Ellipsoid"), ("api-binding", "scale")},
        )

    def test_rejects_post_mesh_hole_filling(self) -> None:
        errors = source_preflight.validate_source_text(
            "import trimesh\ntrimesh.repair.fill_holes(mesh)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "post-mesh-repair")
        self.assertEqual(errors[0]["name"], "fill_holes")

    def test_rejects_imported_post_mesh_repair(self) -> None:
        errors = source_preflight.validate_source_text(
            "from trimesh.repair import fix_winding\nfix_winding(mesh)\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "post-mesh-repair")
        self.assertEqual(errors[0]["name"], "fix_winding")

    def test_accepts_valid_explicit_build123d_symbols(self) -> None:
        errors = source_preflight.validate_source_text(
            "\n".join(
                [
                    "from build123d import Sphere, scale",
                    "shape = scale(Sphere(1), by=(2, 3, 4))",
                    "",
                ]
            )
        )
        self.assertEqual(errors, [])

    def test_does_not_treat_ordinary_or_locally_bound_names_as_api_errors(self) -> None:
        errors = source_preflight.validate_source_text(
            "\n".join(
                [
                    "def scale(value):",
                    "    return value",
                    "def evaluate(Sphere):",
                    "    return scale(Sphere) + ordinary_unknown_name",
                    "",
                ]
            )
        )
        self.assertEqual(errors, [])

    def test_reports_syntax_errors_without_loading_the_api_catalog(self) -> None:
        errors = source_preflight.validate_source_text("if True print('bad')\n")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["check"], "syntax")
        self.assertEqual(errors[0]["line"], 1)

    def test_audit_binds_the_exact_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "build.py"
            payload = b"from build123d import Sphere\nshape = Sphere(1)\n"
            path.write_bytes(payload)
            result = source_preflight.audit(path)
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["schema"], source_preflight.PREFLIGHT_SCHEMA)
        self.assertEqual(result["source"]["path"], str(path.resolve()))
        self.assertEqual(result["source"]["sha256"], sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
