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
        witness = issue["observed"]["booleanWitness"]
        self.assertEqual(witness["coordinateFrame"], "operation-input")
        self.assertEqual(witness["units"], "mm")
        self.assertEqual(witness["unconnectedComponentCount"], 1)
        self.assertEqual(witness["components"][0]["gapMm"], 23.0)
        self.assertEqual(witness["components"][0]["ownerPointMm"][0], 5.0)
        self.assertEqual(witness["components"][0]["operandPointMm"][0], 28.0)

    def test_union_measures_each_component_without_hiding_a_detached_one(self):
        body = Box(10, 10, 10)
        addition = Pos(5, 0, 0) * Box(4, 4, 4) + Pos(30, 0, 0) * Box(4, 4, 4)
        with mock.patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile"}):
            unchanged = cad_helpers.checked_union(body, addition, "supports", part_name="body")
        issue = cad_helpers._DEFERRED_ISSUES[-1]
        self.assertEqual(issue["code"], "SOURCE.UNION_DISCONNECTED")
        witness = issue["observed"]["booleanWitness"]
        self.assertEqual((witness["operandSolidCount"], witness["unconnectedComponentCount"]), (2, 1))
        self.assertEqual(witness["unresolvedComponentCount"], 0)
        self.assertEqual({item["solidIndex"] for item in witness["components"]}, {0, 1})
        disconnected = next(item for item in witness["components"] if not item["connectedToOwner"])
        connected = next(item for item in witness["components"] if item["connectedToOwner"])
        self.assertEqual(disconnected["gapMm"], 23)
        self.assertEqual(disconnected["intersectionVolumeMm3"], 0)
        self.assertEqual(connected["intersectionVolumeMm3"], 32)
        self.assertIsNone(connected["gapMm"])
        self.assertAlmostEqual(float(unchanged.volume), 1000)
        self.assertAlmostEqual(float(body.volume), 1000)
        self.assertAlmostEqual(float(addition.volume), 128)

    def test_surface_contact_and_containment_are_not_misreported_as_a_material_gap(self):
        body = Box(10, 10, 10)
        for operand, relation, connected, intersection, gap in (
            (Pos(10, 0, 0) * Box(10, 10, 10), "touching", True, 0, 0),
            (Pos(10, 10, 0) * Box(10, 10, 10), "touching", False, 0, 0),
            (Box(2, 2, 2), "volume-overlap", True, 8, None),
        ):
            with self.subTest(relation=relation, connected=connected):
                witness = cad_helpers._boolean_witness(body, operand, operation="union")
                item = witness["components"][0]
                self.assertEqual(item["relation"], relation)
                self.assertEqual(item["connectedToOwner"], connected)
                self.assertEqual(item["intersectionVolumeMm3"], intersection)
                self.assertEqual(item["gapMm"], gap)
                if relation == "volume-overlap":
                    self.assertEqual(item["surfaceDistanceMm"], 4)

    def test_cut_in_existing_empty_space_has_no_invented_history_attribution(self):
        body = Box(10, 10, 10) - Box(6, 6, 6)
        with mock.patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile"}):
            result = cad_helpers.checked_cut(body, Box(2, 2, 2), "socket", part_name="body")
        issue = cad_helpers._DEFERRED_ISSUES[-1]
        self.assertEqual(issue["code"], "SOURCE.CUT_MISSED_OWNER")
        witness = issue["observed"]["booleanWitness"]
        self.assertEqual(witness["intersectionVolumeMm3"], 0)
        self.assertEqual(witness["removedMm3"], 0)
        self.assertTrue(witness["operandInsideOwnerBounds"])
        self.assertIn("no prior-cut or enclosed-cavity attribution", witness["containmentBasis"])
        self.assertEqual(witness["components"][0]["gapMm"], 2)
        self.assertEqual(float(result.volume), float(body.volume))

    def test_secondary_measurement_failure_keeps_the_original_boolean_error(self):
        body, addition = Box(10, 10, 10), Pos(30, 0, 0) * Box(4, 4, 4)
        with mock.patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile"}), mock.patch.object(
            type(body), "distance_to_with_closest_points", side_effect=RuntimeError("distance unavailable"),
        ):
            unchanged = cad_helpers.checked_union(body, addition, "detached")
        issue = cad_helpers._DEFERRED_ISSUES[-1]
        self.assertEqual(issue["code"], "SOURCE.UNION_DISCONNECTED")
        witness = issue["observed"]["booleanWitness"]
        self.assertEqual(witness["unresolvedComponentCount"], 1)
        self.assertIsNone(witness["components"][0]["connectedToOwner"])
        self.assertIn("distance unavailable", witness["components"][0]["measurementError"])
        self.assertAlmostEqual(float(unchanged.volume), 1000)

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
                            package_mode="separate_parts",
                        )

            payload = json.loads(output.getvalue())
            issue = payload["issues"][0]
            self.assertEqual(issue["code"], "EXPORT.NON_VOLUMETRIC_MESH")
            self.assertEqual(issue["part"], "freeform-shell")
            self.assertEqual(issue["observed"]["boundaryEdgeCount"], 3)


if __name__ == "__main__":
    unittest.main()
