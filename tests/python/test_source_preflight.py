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
