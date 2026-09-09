from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from build_check import audit  # noqa: E402
from build_manifest import (  # noqa: E402
    artifact_record,
    bind_inputs,
    identity_matrix,
    new_run_id,
    semantic_assembly_record,
    semantic_envelope_errors,
    semantic_envelope_tolerance_mm,
    semantic_envelope_record_rounding_mm,
    semantic_evidence_errors,
    validate_manifest,
)
from tests.python.intent_fixture import (  # noqa: E402
    PROFILE,
    intent_ref,
    write_intent,
)
from material_plan import (  # noqa: E402
    build_material_plan,
    material_record,
    source_binding,
    validate_material_plan,
    validate_material_sources,
)


def _valid_report(root: Path) -> dict:
    geometry = {
        "bodyCount": 1,
        "boundsMm": {"max": [1, 1, 1], "min": [0, 0, 0], "size": [1, 1, 1]},
        "isVolume": True,
        "valid": True,
        "volumeMm3": 1,
    }
    intent_path, _ = write_intent(
        root, part="part", feature_owners={"part-body": "part"},
        dimensions_mm=(1, 1, 1), filename="intent.json",
    )
    scene_path = root / "scene.json"
    scene_path.write_text(json.dumps({
        "schema": "evidence-semantic-scene/v1", "revision": "rev-1",
        "intentRef": intent_ref(intent_path), "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "materials": [], "parts": [{"id": "part", "representationMaster": "brep"}],
        "nodes": [{
            "id": "part-body", "partId": "part", "featureId": "part-body",
            "role": "solid", "operation": "union",
            "recipe": {"kind": "roundedBox", "parameters": {
                "sizeMm": [1, 1, 1], "radiusMm": 0.0,
            }},
        }], "interfaces": [],
    }), encoding="utf-8")
    inputs = {}
    for name, path, schema in (
        ("intent", intent_path, "evidence-cad-intent/v5"),
        ("scene", scene_path, "evidence-semantic-scene/v1"),
        ("profile", PROFILE, "evidence-bambu-printer-profile/v1"),
    ):
        inputs[name] = {
            **artifact_record(path),
            "schema": schema,
            **({"revision": "rev-1"} if name == "scene" else {}),
        }
    source = root / "part.py"
    source.write_text("# parametric source\n", encoding="utf-8")
    inputs["source"] = {
        **artifact_record(source),
        "schema": "python-source/v1",
    }
    artifacts = {}
    for key, filename in (
        ("stl:part", "part.stl"),
        ("step:part", "part.step"),
        ("glb:display", "part-display.glb"),
    ):
        path = root / filename
        if not path.exists():
            path.write_bytes(filename.encode("ascii"))
        frame = (
            "semantic"
            if key in {"step:part", "glb:display"}
            else "part-print" if key == "stl:part" else "plate-print"
        )
        artifacts[key] = artifact_record(path, coordinateFrame=frame)
    export_audit = {
        "artifacts": {
            key: {
                "errors": [],
                "pass": True,
                "path": record["path"],
                "sha256": record["sha256"],
                "type": (
                    "glb" if key == "glb:display" else "step"
                    if key.startswith("step:") else "stl"
                ),
            }
            for key, record in artifacts.items()
        },
        "errors": [],
        "pass": True,
        "schema": "evidence-export-audit/v1",
    }
    audited_geometry = {
        key: value
        for key, value in geometry.items()
        if key != "isVolume"
    }
    for key, record in export_audit["artifacts"].items():
        if key == "glb:display":
            continue
        record["expected"] = deepcopy(audited_geometry)
        record["observed"] = deepcopy(audited_geometry)
        if key.startswith("stl:"):
            record["observed"].update({
                "faceCount": 12,
                "vertexCount": 8,
                "watertight": True,
                "windingConsistent": True,
            })
    export_audit_path = root / "part_export-audit.json"
    export_audit_path.write_text(json.dumps(export_audit), encoding="utf-8")
    artifacts["exportAudit"] = artifact_record(export_audit_path)
    return {
        "artifactMatrix": {
            "parts": {
                "part": {
                    "glb": "required",
                    "step": "required",
                    "stl": "required",
                    "threeMf": "not-applicable",
                }
            }
        },
        "artifacts": artifacts,
        "autoScale": False,
        "backend": "brep-part",
        "backendData": {
            "exportAudit": export_audit,
            "parameters": {},
            "printOrientation": {
                "candidates": [{"name": "identity"}],
                "selected": {"name": "identity"},
                "strategy": "fixture-rigid-orientation",
            },
            "semanticAssembly": {
                "boundsMm": deepcopy(geometry["boundsMm"]),
                "intentSha256": inputs["intent"]["sha256"],
            },
        },
        "builtAt": "2026-09-01T00:00:00+00:00",
        "coordinateFrames": {
            "semantic": {"scale": 1.0, "units": "mm", "up": "Z"},
            "part-print": {"partTransforms": {"part": identity_matrix()}},
            "plate-print": {"partTransforms": {"part": identity_matrix()}},
        },
        "events": [],
        "features": {},
        "inputs": inputs,
        "part": "part",
        "parts": {
            "part": {
                "print": deepcopy(geometry),
                "representationMaster": "brep",
                "semantic": deepcopy(geometry),
            }
        },
        "pass": True,
        "revision": "rev-1",
        "runId": "123e4567-e89b-42d3-a456-426614174000",
        "scale": 1.0,
        "schema": "evidence-a3d-build/v1",
        "warnings": [],
    }


def _update_bound_intent(report: dict, intent: dict) -> None:
    """Keep a deliberately edited fixture coherent at both binding boundaries."""
    path = Path(report["inputs"]["intent"]["path"])
    path.write_text(json.dumps(intent), encoding="utf-8")
    digest = artifact_record(path)["sha256"]
    report["inputs"]["intent"]["sha256"] = digest
    report["backendData"]["semanticAssembly"]["intentSha256"] = digest
    scene_path = Path(report["inputs"]["scene"]["path"])
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["intentRef"] = intent_ref(path)
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    report["inputs"]["scene"]["sha256"] = artifact_record(scene_path)["sha256"]


class BuildManifestTests(unittest.TestCase):
    def test_run_ids_are_canonical_uuids_in_environment_and_manifest(self):
        canonical = "123e4567-e89b-42d3-a456-426614174000"
        with patch.dict(os.environ, {"AMAGINE3D_COMPILE_RUN_ID": canonical}):
            self.assertEqual(new_run_id(), canonical)
        for invalid in (
            "123E4567-E89B-42D3-A456-426614174000",
            "123e4567e89b42d3a456426614174000",
            "{123e4567-e89b-42d3-a456-426614174000}",
            "run-1",
        ):
            with self.subTest(invalid=invalid):
                with patch.dict(
                    os.environ,
                    {"AMAGINE3D_COMPILE_RUN_ID": invalid},
                ):
                    with self.assertRaisesRegex(ValueError, "canonical UUID"):
                        new_run_id()
                with tempfile.TemporaryDirectory() as directory:
                    report = _valid_report(Path(directory))
                    report["runId"] = invalid
                    self.assertIn(
                        "runId must be a canonical UUID",
                        validate_manifest(report),
                    )

    def _bound_brep_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        intent_path, _ = write_intent(
            root,
            part="part",
            feature_owners={"part-body": "part"},
        )
        scene_path = root / "part_scene.json"
        scene_path.write_text(json.dumps({
            "schema": "evidence-semantic-scene/v1",
            "revision": "bind-inputs-001",
            "intentRef": intent_ref(intent_path),
            "units": "mm",
            "coordinateSystem": {"handedness": "right", "up": "Z"},
            "materials": [],
            "parts": [{"id": "part", "representationMaster": "brep"}],
            "nodes": [{
                "id": "part-body",
                "partId": "part",
                "featureId": "part-body",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "roundedBox",
                    "parameters": {"sizeMm": [1, 1, 1], "radiusMm": 0.0},
                },
            }],
            "interfaces": [],
        }), encoding="utf-8")
        source_path = root / "part.py"
        source_path.write_text("# fixture source\n", encoding="utf-8")
        return intent_path, scene_path, source_path

    def test_bind_inputs_revalidates_intent_scene_and_exported_part_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path, scene_path, source_path = self._bound_brep_inputs(root)
            intent, scene, _, _ = bind_inputs(
                intent_path=str(intent_path),
                scene_path=str(scene_path),
                expected_parts={"part"},
                source_path=str(source_path),
            )
            self.assertEqual(intent["part"], "part")
            self.assertEqual({item["id"] for item in scene["parts"]}, {"part"})

            with self.assertRaisesRegex(ValueError, "intent parts do not match exported"):
                bind_inputs(
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    expected_parts={"unrelated"},
                    source_path=str(source_path),
                )

            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["visual"]["required"] = False
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            scene["intentRef"]["sha256"] = artifact_record(intent_path)["sha256"]
            scene_path.write_text(json.dumps(scene), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid intent contract"):
                bind_inputs(
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    expected_parts={"part"},
                    source_path=str(source_path),
                )

    def test_valid_manifest_and_bound_files_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            path = root / "part_report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(validate_manifest(report), [])
            self.assertTrue(audit(path)["pass"])

    def test_independent_audit_requires_noncritical_physical_bindings_not_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            intent_path = Path(report["inputs"]["intent"]["path"])
            intent = json.loads(intent_path.read_text())
            intent["features"].append({
                "id": "attachment", "part": "part", "kind": "detail",
                "evidence": "A physical attachment is required",
                "acceptance": "The attachment is bound to this owner",
            })
            self.assertNotIn("attachment", intent["printability"]["critical_features"])
            _update_bound_intent(report, intent)
            scene_path = Path(report["inputs"]["scene"]["path"])
            scene = json.loads(scene_path.read_text())
            attachment = deepcopy(scene["nodes"][0])
            attachment.update(id="attachment", featureId="attachment")
            scene["nodes"].append(attachment)
            report["events"] = [{"kind": "union", "id": "attachment", "part": "part", "added_mm3": 1}]
            report["features"]["attachment"] = deepcopy(report["parts"]["part"]["semantic"])
            path = root / "part_report.json"

            def check(changed_scene):
                scene_path.write_text(json.dumps(changed_scene))
                report["inputs"]["scene"]["sha256"] = artifact_record(scene_path)["sha256"]
                path.write_text(json.dumps(report))
                return audit(path)

            self.assertTrue(check(scene)["pass"])
            missing = deepcopy(scene)
            missing["nodes"].pop()
            display = deepcopy(missing)
            display["nodes"].append({
                "id": "attachment-appearance", "partId": "part", "featureId": "attachment",
                "role": "display-only", "operation": "none", "physicalFeatureRef": "part-body",
                "recipe": {"kind": "displayComponent", "parameters": {
                    "sourceMesh": "attachment.ply", "appearance": {
                        "baseColor": "#111417", "metallic": 0.0, "roughness": 0.28,
                    },
                }},
            })
            for name, changed in (("event and observation only", missing), ("display impostor", display)):
                with self.subTest(name=name):
                    result = check(changed)
                    self.assertFalse(result["pass"])
                    self.assertTrue(any("missing physical feature bindings" in error and "attachment" in error
                                        for error in result["errors"]), result)

    def test_independent_audit_requires_same_scene_intent_path_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            scene_path = Path(report["inputs"]["scene"]["path"])
            scene = json.loads(scene_path.read_text())
            copy_path = root / "different_intent.json"
            copy_path.write_bytes(Path(report["inputs"]["intent"]["path"]).read_bytes())
            scene["intentRef"] = intent_ref(copy_path)
            scene_path.write_text(json.dumps(scene))
            report["inputs"]["scene"]["sha256"] = artifact_record(scene_path)["sha256"]
            errors = semantic_evidence_errors(report, root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("same intent path and hash", errors[0])
            scene["intentRef"] = intent_ref(Path(report["inputs"]["intent"]["path"]))
            scene_path.write_text(json.dumps(scene))
            errors = semantic_evidence_errors(report, root)
            self.assertTrue(any("inputs.scene hash does not match" in error for error in errors), errors)

    def test_mesh_master_cannot_claim_step(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["backend"] = "hybrid-mesh"
            report["parts"]["part"]["representationMaster"] = "mesh"
            errors = validate_manifest(report)
            self.assertTrue(any("step must be not-applicable" in item for item in errors))
            self.assertTrue(any("step:part is forbidden" in item for item in errors))

    def test_section_dimensions_require_the_owners_semantic_brep_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            intent_path = Path(report["inputs"]["intent"]["path"])
            intent = json.loads(intent_path.read_text())
            intent.update(part="part", manufacturing={"mode": "single-part"}, features=[{
                "id": "part-body", "part": "part", "kind": "envelope",
                "evidence": "The top outside dimension belongs to this part",
                "acceptance": "Top outside width is 1 mm",
                "section_dimensions": [{"plane": {"axis": "z", "coordinate_mm": 1},
                                        "outer_envelope": {"width_u_mm": {"value": 1}}}],
            }])
            _update_bound_intent(report, intent)
            self.assertEqual(semantic_evidence_errors(report, root), [])

            for name in ("missing STEP", "print coordinates", "mesh owner"):
                with self.subTest(name=name):
                    changed = deepcopy(report)
                    if name == "missing STEP":
                        changed["artifacts"].pop("step:part")
                    elif name == "print coordinates":
                        changed["artifacts"]["step:part"]["coordinateFrame"] = "part-print"
                    else:
                        changed["backend"] = "hybrid-mesh"
                        changed["parts"]["part"]["representationMaster"] = "mesh"
                        changed["artifacts"].pop("step:part")
                    errors = semantic_evidence_errors(changed, root)
                    self.assertTrue(any("section dimensions for part-body" in error for error in errors), errors)

            # Multipart export reserves step:assembly for the aggregate; a physical
            # part with that name cannot obtain its own section proof from the union.
            intent["manufacturing"] = {"mode": "multipart", "parts": [{"name": "assembly"}, {"name": "other"}]}
            intent["features"][0]["part"] = "assembly"
            intent_path.write_text(json.dumps(intent))
            report["inputs"]["intent"]["sha256"] = artifact_record(intent_path)["sha256"]
            report["backend"] = "brep-assembly"
            report["parts"] = {name: deepcopy(report["parts"]["part"]) for name in ("assembly", "other")}
            report["artifacts"]["step:assembly"] = report["artifacts"].pop("step:part")
            errors = semantic_evidence_errors(report, root)
            self.assertTrue(any("owner assembly collides" in error for error in errors), errors)

    def test_hybrid_cannot_omit_print_package_and_material_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["backend"] = "hybrid-mesh"
            report["parts"]["part"]["representationMaster"] = "mesh"
            report["artifactMatrix"]["parts"]["part"]["step"] = "not-applicable"
            report["artifacts"].pop("step:part")
            errors = validate_manifest(report)
            self.assertTrue(
                any("hybrid-mesh requires 3MF" in item for item in errors),
                errors,
            )

    def test_scaled_print_transform_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["coordinateFrames"]["part-print"]["partTransforms"]["part"][0][0] = 2
            errors = validate_manifest(report)
            self.assertTrue(any("scaling is forbidden" in item for item in errors))

    def test_file_audit_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            path = root / "part_report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            (root / "part.stl").write_text("tampered", encoding="utf-8")
            result = audit(path)
            self.assertFalse(result["pass"])
            self.assertTrue(any("sha256 does not match" in item for item in result["errors"]))

    def test_file_audit_rejects_a_failed_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            report["pass"] = False
            path = root / "part_report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            result = audit(path)
            self.assertFalse(result["pass"])
            self.assertIn(
                "build report pass must be true before delivery",
                result["errors"],
            )

    def test_every_input_requires_a_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["inputs"]["scene"].pop("path")
            self.assertIn(
                "inputs.scene.path must be a non-empty string",
                validate_manifest(report),
            )

    def test_hybrid_forbids_an_invented_python_source(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["backend"] = "hybrid-mesh"
            report["parts"]["part"]["representationMaster"] = "mesh"
            report["artifactMatrix"]["parts"]["part"].update(
                {"step": "not-applicable", "threeMf": "required"}
            )
            report["artifacts"].pop("step:part")
            three_mf = Path(directory) / "part.3mf"
            material_plan = Path(directory) / "part_material-plan.json"
            three_mf.write_bytes(b"3MF")
            material_plan.write_text("{}", encoding="utf-8")
            report["artifacts"]["3mf"] = artifact_record(three_mf)
            report["artifacts"]["materialPlan"] = artifact_record(material_plan)
            report["materialPlan"] = {}
            errors = validate_manifest(report)
            self.assertTrue(
                any("inputs must contain exactly" in item for item in errors),
                errors,
            )

    def test_legacy_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            report = deepcopy(_valid_report(Path(directory)))
            report["schema"] = "evidence-cad-build/v4"
            self.assertIn(
                "schema must be evidence-a3d-build/v1",
                validate_manifest(report),
            )

    def test_unknown_top_level_and_artifact_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["legacyPayload"] = {"schema": "evidence-cad-build/v4"}
            report["artifacts"]["legacyReport"] = {
                "path": report["artifacts"]["glb:display"]["path"],
                "sha256": report["artifacts"]["glb:display"]["sha256"],
            }
            errors = validate_manifest(report)
            self.assertTrue(any("top-level fields must be exactly" in item for item in errors))
            self.assertTrue(any("artifacts must contain exactly" in item for item in errors))

    def test_backend_data_and_part_fields_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["backendData"]["legacyPayload"] = {"acceptedByOldReaders": True}
            report["parts"]["part"]["legacyPayload"] = {"acceptedByOldReaders": True}
            errors = validate_manifest(report)
            self.assertTrue(any("backendData fields must be exactly" in item for item in errors))
            self.assertTrue(any("parts.part fields must be exactly" in item for item in errors))

    def test_semantic_assembly_is_required_and_must_equal_part_union(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            report["backendData"].pop("semanticAssembly")
            self.assertTrue(any(
                "semanticAssembly" in item for item in validate_manifest(report)
            ))

            report = _valid_report(Path(directory))
            report["backendData"]["semanticAssembly"]["boundsMm"] = {
                "max": [2, 1, 1],
                "min": [0, 0, 0],
                "size": [2, 1, 1],
            }
            self.assertTrue(any(
                "union of parts" in item for item in validate_manifest(report)
            ))
            # Ordinary dimension allowances must not widen canonical record consistency.
            report = _valid_report(Path(directory))
            bounds = report["backendData"]["semanticAssembly"]["boundsMm"]
            bounds["max"][0] = bounds["size"][0] = 1.00021
            self.assertTrue(any("union of parts" in item for item in validate_manifest(report)))

            report = _valid_report(Path(directory))
            report["backendData"]["semanticAssembly"]["legacyBounds"] = {}
            self.assertTrue(any(
                "semanticAssembly fields must be exactly" in item
                for item in validate_manifest(report)
            ))

            report = _valid_report(Path(directory))
            report["backendData"]["semanticAssembly"]["intentSha256"] = "0" * 64
            self.assertTrue(any(
                "must match inputs.intent.sha256" in item
                for item in validate_manifest(report)
            ))

    def test_brep_envelope_accepts_record_rounding_without_half_mm_design_freedom(self):
        with tempfile.TemporaryDirectory() as directory:
            parts = _valid_report(Path(directory))["parts"]
            digest = "a" * 64
            accepted_intent = {
                "dimensions_mm": {
                    axis: {"value": value}
                    for axis, value in zip("xyz", (1.01019, 1.0, 1.0), strict=True)
                }
            }
            self.assertEqual(
                semantic_assembly_record(parts, digest, accepted_intent)["boundsMm"]["size"],
                [1.0, 1.0, 1.0],
            )
            rejected_intent = deepcopy(accepted_intent)
            rejected_intent["dimensions_mm"]["x"]["value"] = 1.01021
            with self.assertRaisesRegex(ValueError, "differs from intent"):
                semantic_assembly_record(parts, digest, rejected_intent)
            accepted_intent["dimensions_mm"]["x"].update(value=1.00029, measurement_precision_mm=0.0001)
            semantic_assembly_record(parts, digest, accepted_intent)
            accepted_intent["dimensions_mm"]["x"]["value"] = 1.00031
            with self.assertRaisesRegex(ValueError, "differs from intent"):
                semantic_assembly_record(parts, digest, accepted_intent)

    def test_real_loft_overshoot_is_design_error_while_mesh_tolerance_is_separate(self):
        intent = {"dimensions_mm": {
            axis: {"value": value, "source": "user"}
            for axis, value in zip("xyz", (82.0, 62.0, 95.0))
        }}
        # Dev-03 STEP readback, also confirmed by mesh extrema. Nominal section
        # widths do not constrain the interpolating loft's global envelope.
        observed = {"size": [82.077456067, 62.258062358, 95.0000001]}
        for backend in ("brep-part", "brep-assembly", "brep-color-regions"):
            errors = semantic_envelope_errors(observed, intent,
                tolerance_mm=semantic_envelope_tolerance_mm(backend),
                record_rounding_mm=semantic_envelope_record_rounding_mm(backend))
            self.assertEqual(len(errors), 2)
            self.assertIn("target 82..82 mm", errors[0])
            self.assertIn("observed 82.0774561 mm", errors[0])
            self.assertIn("delta from nominal +0.077456067", errors[0])
        self.assertEqual(semantic_envelope_errors(observed, intent,
            tolerance_mm=semantic_envelope_tolerance_mm("hybrid-mesh")), [])
        for axis, upper in (("x", 82.1), ("y", 62.3)):
            intent["dimensions_mm"][axis]["constraint"] = {
                "kind": "range", "min_mm": intent["dimensions_mm"][axis]["value"],
                "max_mm": upper,
            }
        self.assertEqual(semantic_envelope_errors(observed, intent,
            tolerance_mm=semantic_envelope_tolerance_mm("brep-part")), [])

    def test_ordinary_dimension_range_accepts_print_scale_errors_without_rounding_evidence(self):
        intent = {"dimensions_mm": {
            "x": {"value": 54, "constraint": {"kind": "range", "min_mm": 53.9, "max_mm": 54.1}},
            "y": {"value": 1}, "z": {"value": 1},
        }}
        for width in (54.09942521921779, 54.0001013664441, 53.9, 54.109, 53.891):
            self.assertEqual(semantic_envelope_errors(
                {"size": [width, 1, 1]}, intent), [])
        # Both display as 54.11; acceptance must use raw values, not rounding.
        self.assertEqual(f"{54.109:.2f}", f"{54.111:.2f}")
        for width in (54.111, 53.889):
            self.assertTrue(semantic_envelope_errors({"size": [width, 1, 1]}, intent))
        for height, passes in ((1.009, True), (0.991, True), (1.011, False), (0.989, False)):
            self.assertEqual(not semantic_envelope_errors({"size": [54, height, 1]}, intent), passes)
        strict = deepcopy(intent)
        strict["dimensions_mm"]["x"]["measurement_precision_mm"] = 0.0001
        self.assertTrue(semantic_envelope_errors(
            {"size": [54.10015, 1, 1]}, strict))
        self.assertEqual(semantic_envelope_errors({"size": [54.10005, 1, 1]}, strict), [])
        # An explicit approximation override cannot erase an explicit precision target.
        self.assertTrue(semantic_envelope_errors(
            {"size": [54.10015, 1, 1]}, strict, tolerance_mm=0.5))
        strict["dimensions_mm"]["x"].pop("constraint")
        self.assertTrue(semantic_envelope_errors({"size": [54.00015, 1, 1]}, strict))
        self.assertEqual(semantic_envelope_errors({"size": [54.00005, 1, 1]}, strict), [])

    def test_step_readback_cannot_hide_design_error_inside_representation_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            export_audit = report["backendData"]["exportAudit"]
            measured = export_audit["artifacts"]["step:part"]["observed"]["boundsMm"]
            measured["max"][0] = measured["size"][0] = 1.01015
            export_path = root / "part_export-audit.json"
            export_path.write_text(json.dumps(export_audit), encoding="utf-8")
            report["artifacts"]["exportAudit"] = artifact_record(export_path)
            report_path = root / "part_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            result = audit(report_path)
            self.assertFalse(result["pass"])
            self.assertEqual(len(result["errors"]), 1)
            self.assertIn("hash-bound STEP readback: semantic envelope dimension x differs", result["errors"][0])
            # Record allowance must not leak into the actual STEP check.
            measured["max"][0] = measured["size"][0] = 1.00999
            export_path.write_text(json.dumps(export_audit), encoding="utf-8")
            report["artifacts"]["exportAudit"] = artifact_record(export_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(audit(report_path)["pass"])
            intent_path = Path(report["inputs"]["intent"]["path"])
            intent = json.loads(intent_path.read_text())
            intent["dimensions_mm"]["x"]["measurement_precision_mm"] = 0.0001
            _update_bound_intent(report, intent)
            measured["max"][0] = measured["size"][0] = 1.00015
            export_path.write_text(json.dumps(export_audit), encoding="utf-8")
            report["artifacts"]["exportAudit"] = artifact_record(export_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            errors = audit(report_path)["errors"]
            self.assertTrue(any("hash-bound STEP readback: semantic envelope dimension x differs" in error for error in errors))

    def test_endpoint_rounding_cannot_be_mistaken_for_geometric_size_error(self):
        nominal, kernel_padding = 10.0000999, 1e-7
        low, high = -nominal / 2 - kernel_padding, nominal / 2 + kernel_padding
        precise = {"size": [high - low, 1, 1]}
        rounded = {"min": [round(low, 4), 0, 0],
                   "max": [round(high, 4), 1, 1],
                   "size": [round(high, 4) - round(low, 4), 1, 1]}
        intent = {"dimensions_mm": {axis: {"value": size}
            for axis, size in zip("xyz", [nominal, 1, 1])}}
        intent["dimensions_mm"]["x"]["measurement_precision_mm"] = 0.0001
        self.assertGreater(rounded["size"][0] - nominal, 0.0001)
        semantic_assembly_record({"part": {"semantic": {"boundsMm": rounded}}}, "a" * 64, intent)
        self.assertEqual(semantic_envelope_errors(precise, intent, tolerance_mm=0.0001), [])

    def test_build_check_rejects_brep_semantic_bounds_that_miss_step_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            changed = {
                "max": [1.06, 1, 1],
                "min": [0, 0, 0],
                "size": [1.06, 1, 1],
            }
            report["parts"]["part"]["semantic"]["boundsMm"] = deepcopy(changed)
            report["backendData"]["semanticAssembly"]["boundsMm"] = deepcopy(changed)
            report_path = root / "wrong-step-bounds_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = audit(report_path)
            self.assertFalse(result["pass"])
            self.assertTrue(any(
                "does not match its hash-bound STEP readback" in item
                for item in result["errors"]
            ))

    def test_measured_envelope_uses_only_explicit_design_freedom(self):
        with tempfile.TemporaryDirectory() as directory:
            parts = _valid_report(Path(directory))["parts"]
            intent = {"dimensions_mm": {axis: {"value": 2.0, "source": "inferred"} for axis in "xyz"}}
            with self.assertRaisesRegex(ValueError, "differs from intent"):
                semantic_assembly_record(parts, "a" * 64, intent)
            for dimension in intent["dimensions_mm"].values():
                dimension["constraint"] = {"kind": "range", "min_mm": 1.0, "max_mm": 2.0}
            actual = semantic_assembly_record(parts, "a" * 64, intent)
            self.assertEqual(actual["boundsMm"]["size"], [1.0, 1.0, 1.0])
            intent["dimensions_mm"]["x"]["constraint"]["min_mm"] = 1.5001
            with self.assertRaisesRegex(ValueError, "differs from intent"):
                semantic_assembly_record(parts, "a" * 64, intent)

    def test_malformed_envelope_constraint_remains_an_audit_error(self):
        from build_manifest import semantic_envelope_errors
        for constraint in (None, {"kind": "range"}, {"kind": "range", "min_mm": 1, "max_mm": "invalid"}):
            with self.subTest(constraint=constraint):
                errors = semantic_envelope_errors({"size": [1, 1, 1]}, {"dimensions_mm": {
                    axis: {"value": 1, "constraint": constraint} for axis in "xyz"}})
                self.assertEqual(len(errors), 3)
                self.assertTrue(all("invalid constraint" in item for item in errors))

    def test_build_check_rejects_semantic_envelope_that_misses_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _valid_report(root)
            intent_path = Path(report["inputs"]["intent"]["path"])
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["dimensions_mm"]["x"]["value"] = 2
            _update_bound_intent(report, intent)
            report_path = root / "wrong-envelope_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = audit(report_path)
            self.assertFalse(result["pass"])
            self.assertTrue(any(
                "semantic envelope dimension x differs from intent" in item
                for item in result["errors"]
            ))

    def test_parameter_descriptors_are_closed_and_numerically_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _valid_report(Path(directory))
            descriptor = {
                "affects": ["part-body"],
                "default": 2.0,
                "group": "Envelope",
                "group_zh": "外形",
                "label": "Width",
                "label_zh": "宽度",
                "maximum": 4.0,
                "minimum": 1.0,
                "step": 0.5,
                "unit": "mm",
                "value": 2.5,
            }
            report["backendData"]["parameters"] = {"overall-width": descriptor}
            self.assertEqual(validate_manifest(report), [])

            descriptor["legacyDefault"] = 2.0
            self.assertTrue(any(
                "unsupported or missing fields" in item
                for item in validate_manifest(report)
            ))
            descriptor.pop("legacyDefault")
            descriptor["value"] = 2.25
            self.assertTrue(any(
                ".value must align with step" in item
                for item in validate_manifest(report)
            ))
            descriptor["value"] = float("inf")
            self.assertTrue(any(
                "numeric fields must be finite" in item
                for item in validate_manifest(report)
            ))

    def test_material_source_bindings_are_exact_and_provenance_checked(self):
        declared = material_record(
            "orange",
            "#F05A35",
            status="declared",
        )
        assignment = {
            "materialId": "orange",
            "part": "part",
            "region": "accent",
            "scope": "brep-region",
        }
        plan = build_material_plan(
            part="part",
            package_mode="co_print_body",
            materials=[declared],
            assignments=[assignment],
            source_bindings=[source_binding(
                material=declared,
                part="part",
                region="accent",
                scope="brep-region",
                source_id="accent",
                source_kind="intent-color-region",
            )],
        )
        intent = {"color_regions": [{
            "hex": "#F05A35",
            "name": "accent",
            "part": "part",
        }]}
        scene = {"materials": [], "parts": [{"id": "part"}]}
        self.assertEqual(validate_material_sources(plan, intent, scene), [])

        old = deepcopy(plan)
        old["intentBindings"] = old.pop("sourceBindings")
        self.assertTrue(validate_material_plan(old))
        unknown = deepcopy(plan)
        unknown["sourceBindings"][0]["sourceId"] = "not-declared"
        self.assertTrue(any(
            "unknown intent color region" in item
            for item in validate_material_sources(unknown, intent, scene)
        ))

    def test_scene_material_and_appearance_sources_are_distinct(self):
        def proposed(material_id: str, color: str) -> dict:
            return material_record(
                material_id,
                color,
                status="proposed",
            )

        for source_kind, source_id, material_id, scene in (
            (
                "scene-part-material",
                "mat",
                "mat",
                {
                    "materials": [{"color": "#ABCDEF", "id": "mat"}],
                    "parts": [{"id": "part", "materialId": "mat"}],
                },
            ),
            (
                "scene-part-appearance",
                "part",
                "proposed-part",
                {
                    "materials": [],
                    "parts": [{"color": "#123456", "id": "part"}],
                },
            ),
        ):
            color = "#ABCDEF" if material_id == "mat" else "#123456"
            material = proposed(material_id, color)
            plan = build_material_plan(
                part="part",
                package_mode="co_print_body",
                materials=[material],
                assignments=[{
                    "materialId": material_id,
                    "part": "part",
                    "region": None,
                    "scope": "whole-part",
                }],
                source_bindings=[source_binding(
                    material=material,
                    part="part",
                    region=None,
                    scope="whole-part",
                    source_id=source_id,
                    source_kind=source_kind,
                )],
            )
            self.assertEqual(validate_material_sources(plan, {}, scene), [])

            wrong_kind = deepcopy(plan)
            wrong_kind["sourceBindings"][0]["sourceKind"] = (
                "scene-part-appearance"
                if source_kind == "scene-part-material"
                else "scene-part-material"
            )
            self.assertTrue(validate_material_sources(wrong_kind, {}, scene))


if __name__ == "__main__":
    unittest.main()
