from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from build123d import Box, Pos
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_diagnostics  # noqa: E402
import cad_helpers  # noqa: E402


class CadDiagnosticsTests(unittest.TestCase):
    def tearDown(self) -> None:
        cad_helpers._DEFERRED_ISSUES.clear()
        cad_helpers._EVENTS.clear()

    def test_diagnostic_payload_preserves_evidence_without_shape_assumptions(self) -> None:
        error = cad_diagnostics.CadDiagnosticError(
            check="mesh-topology",
            code="EXPORT.NON_VOLUMETRIC_MESH",
            message="mesh is open",
            part="arbitrary-part",
            observed={"boundaryEdgeCount": 4, "watertight": False},
            expected={"boundaryEdgeCount": 0, "watertight": True},
        )

        payload = cad_diagnostics.source_diagnostics_payload([error])

        self.assertEqual(
            payload["schema"],
            "evidence-cad-source-diagnostics/v1",
        )
        self.assertFalse(payload["pass"])
        self.assertEqual(payload["issues"][0]["part"], "arbitrary-part")
        self.assertEqual(
            payload["issues"][0]["observed"]["boundaryEdgeCount"],
            4,
        )

    def test_checked_union_accepts_connected_parameter_driven_geometry(self) -> None:
        body = Box(10, 10, 10)
        addition = Pos(9, 0, 0) * Box(10, 4, 4)

        result = cad_helpers.checked_union(
            body,
            addition,
            "side-feature",
            part_name="body",
        )

        self.assertEqual(len(result.solids()), 1)
        self.assertGreater(float(result.volume), float(body.volume))
        self.assertEqual(cad_helpers._EVENTS[-1]["id"], "side-feature")

    def test_checked_union_reports_disconnected_feature_with_provenance(self) -> None:
        body = Box(10, 10, 10)
        addition = Pos(30, 0, 0) * Box(4, 4, 4)
        output = io.StringIO()

        with mock.patch.dict(
            os.environ,
            {"AMAGINE3D_SOURCE_PHASE": "compile"},
            clear=False,
        ):
            unchanged = cad_helpers.checked_union(
                body,
                addition,
                "detached-feature",
                part_name="body",
            )
            with contextlib.redirect_stdout(output):
                with self.assertRaises(cad_helpers.BuildInvariantError):
                    cad_helpers._raise_deferred_source_issues()

        self.assertEqual(float(unchanged.volume), float(body.volume))
        payload = json.loads(output.getvalue())
        issue = payload["issues"][0]
        self.assertEqual(issue["code"], "SOURCE.UNION_DISCONNECTED")
        self.assertEqual(issue["featureId"], "detached-feature")
        self.assertEqual(issue["partId"], "body")
        self.assertEqual(issue["observed"]["result"]["bodyCount"], 2)

    def test_colored_export_emits_typed_mesh_topology_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh_path = root / "open.stl"
            trimesh.Trimesh(
                vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                faces=[[0, 1, 2]],
                process=False,
            ).export(mesh_path)
            output = io.StringIO()

            with mock.patch.dict(
                os.environ,
                {"AMAGINE3D_SOURCE_PHASE": "compile"},
                clear=False,
            ):
                with contextlib.redirect_stdout(output):
                    with self.assertRaises(cad_helpers.BuildInvariantError):
                        cad_helpers._write_part_color_archive(
                            [(str(mesh_path), "#112233", "freeform-shell")],
                            root / "model.3mf",
                            "model",
                        )

            payload = json.loads(output.getvalue())
            issue = payload["issues"][0]
            self.assertEqual(issue["code"], "EXPORT.NON_VOLUMETRIC_MESH")
            self.assertEqual(issue["part"], "freeform-shell")
            self.assertEqual(issue["observed"]["boundaryEdgeCount"], 3)


if __name__ == "__main__":
    unittest.main()
