export const PYTHON_BUILD_SCRIPT = String.raw`
import ast
import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys

ALLOWED_IMPORTS = {"build123d", "amagine_cad", "math"}
FORBIDDEN_NAMES = {
    "compile", "eval", "exec", "getattr", "globals", "input", "locals",
    "open", "setattr", "vars", "__builtins__", "__import__", "breakpoint",
    "help",
}
HELPERS = {
    "single-color": {
        "bevel_edges_checked", "observe_feature", "publish_model",
        "round_edges_checked", "subtract_checked",
    },
    "multi-color": {
        "bevel_edges_checked", "observe_feature", "publish_color_model",
        "round_edges_checked", "subtract_checked",
    },
}

import build123d as _trusted_build123d
BUILD123D_EXPORTS = set(_trusted_build123d.__all__)

workspace = Path(_amagine_workspace)
workspace.mkdir(parents=True, exist_ok=True)
out_dir = workspace / "cad_out"
if out_dir.exists():
    shutil.rmtree(out_dir)
source_path = workspace / "model.py"
source_path.write_text(_amagine_source, encoding="utf-8")

source_hash = hashlib.sha256(_amagine_source.encode("utf-8")).hexdigest()
if source_hash != _amagine_source_hash:
    raise ValueError(
        f"SourceHashConflict: expected {_amagine_source_hash}, received {source_hash}"
    )

tree = ast.parse(_amagine_source, filename=str(source_path))
parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        roots = [alias.name.split(".")[0] for alias in node.names]
        if any(root != "math" for root in roots):
            raise PermissionError(f"Forbidden import: {roots}")
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if node.level or root not in ALLOWED_IMPORTS:
            raise PermissionError(f"Forbidden import: {node.module}")
        names = {alias.name for alias in node.names}
        if root == "amagine_cad" and (
            "*" in names or not names.issubset(HELPERS[_amagine_workflow])
        ):
            raise PermissionError(f"Cross-profile helper import: {sorted(names)}")
        if root == "amagine_cad" and any(alias.asname for alias in node.names):
            raise PermissionError("amagine_cad imports cannot be aliased")
        if root == "build123d" and any(
            name != "*" and name not in BUILD123D_EXPORTS for name in names
        ):
            raise PermissionError(f"Non-public build123d import: {sorted(names)}")
        if root == "build123d" and any(
            name.startswith(("export_", "import_"))
            or name in {"FontManager", "Mesher"}
            for name in names
        ):
            raise PermissionError("Direct CAD file IO is forbidden")
        if root == "math" and any(name.startswith("_") for name in names):
            raise PermissionError(f"Private math import: {sorted(names)}")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in FORBIDDEN_NAMES:
            raise PermissionError(f"Forbidden call: {node.func.id}")
        if (
            node.func.id.startswith(("export_", "import_"))
            or node.func.id in {"FontManager", "Mesher"}
        ):
            raise PermissionError(f"Direct CAD file IO is forbidden: {node.func.id}")
        if node.func.id in {"publish_model", "publish_color_model"}:
            if any(isinstance(argument, ast.Starred) for argument in node.args):
                raise PermissionError("Publisher does not accept expanded arguments")
            if any(keyword.arg is None for keyword in node.keywords):
                raise PermissionError("Publisher does not accept expanded keywords")
            output_values = [
                keyword.value for keyword in node.keywords if keyword.arg == "out_dir"
            ]
            if len(node.args) >= 3:
                output_values.append(node.args[2])
            if any(
                not isinstance(value, ast.Constant) or value.value != "cad_out"
                for value in output_values
            ):
                raise PermissionError("Publisher output directory escaped cad_out")
    elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
        raise PermissionError(f"Forbidden name: {node.id}")
    elif (
        isinstance(node, ast.Name)
        and node.id in {"publish_model", "publish_color_model"}
        and isinstance(node.ctx, ast.Load)
    ):
        parent = parents.get(node)
        if not isinstance(parent, ast.Call) or parent.func is not node:
            raise PermissionError("Publisher helpers cannot be stored or passed as values")
    elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
        raise PermissionError(f"Forbidden dunder attribute: {node.attr}")

call_names = {
    node.func.id
    for node in ast.walk(tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
}
if _amagine_workflow == "single-color" and "publish_color_model" in call_names:
    raise PermissionError("Single-color source cannot call publish_color_model")
if _amagine_workflow == "multi-color" and "publish_model" in call_names:
    raise PermissionError("Multi-color source cannot call publish_model")

overrides = json.loads(_amagine_overrides_json)
applied = set()
for node in tree.body:
    target = None
    value_node = None
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        target, value_node = node.targets[0].id, node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target, value_node = node.target.id, node.value
    if target in overrides and isinstance(value_node, ast.Constant):
        replacement = overrides[target]
        current = value_node.value
        compatible = (
            isinstance(current, type(replacement))
            and isinstance(current, (bool, int, float, str))
        ) or (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and isinstance(replacement, (int, float))
            and not isinstance(replacement, bool)
        )
        if not compatible:
            raise ValueError(f"Override type mismatch for {target}")
        value_node.value = replacement
        applied.add(target)

missing = sorted(set(overrides) - applied)
if missing:
    raise ValueError(f"AST override rejected non-literal or missing parameters: {missing}")
ast.fix_missing_locations(tree)

sys.path.insert(0, str(workspace))
sys.modules.pop("amagine_cad", None)
sys.modules.pop("amagine_three_mf", None)

real_import = builtins.__import__
def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if level or root not in ALLOWED_IMPORTS:
        raise PermissionError(f"Runtime import blocked: {name}")
    return real_import(name, globals, locals, fromlist, level)

safe_builtins = dict(vars(builtins))
safe_builtins["__import__"] = restricted_import
for forbidden in FORBIDDEN_NAMES - {"__import__"}:
    safe_builtins.pop(forbidden, None)

class BoundedWriter:
    def __init__(self, limit=65536):
        self.limit = limit
        self.buffer = io.StringIO()
        self.written = 0

    def write(self, value):
        remaining = max(self.limit - self.written, 0)
        if remaining:
            self.buffer.write(value[:remaining])
            self.written += min(len(value), remaining)
        return len(value)

    def flush(self):
        pass

    def getvalue(self):
        return self.buffer.getvalue()

stdout = BoundedWriter()
namespace = {
    "__builtins__": safe_builtins,
    "__name__": "__main__",
    "__file__": str(source_path),
}
previous_cwd = os.getcwd()
try:
    os.chdir(workspace)
    with contextlib.redirect_stdout(stdout):
        exec(compile(tree, str(source_path), "exec"), namespace)
finally:
    os.chdir(previous_cwd)
    if str(workspace) in sys.path:
        sys.path.remove(str(workspace))

reports = list(out_dir.glob("*.amagine.json"))
if len(reports) != 1:
    raise RuntimeError(f"Expected one build report, found {len(reports)}")
report = json.loads(reports[0].read_text(encoding="utf-8"))
qa_targets = json.loads(_amagine_qa_targets_json)
color_plan = json.loads(_amagine_color_plan_json)
mechanism_specs = json.loads(_amagine_mechanisms_json)
feature_specs = json.loads(_amagine_feature_checks_json)

def check(check_id, passed, message, expected=None, actual=None, tolerance=None, warning=False):
    item = {
        "id": check_id,
        "status": "warning" if warning else ("passed" if passed else "failed"),
        "message": message,
    }
    if expected is not None:
        item["expected"] = expected
    if actual is not None:
        item["actual"] = actual
    if tolerance is not None:
        item["tolerance"] = tolerance
    return item

def load_qa(stl_path, components, include_overall_targets=True):
    spec = importlib.util.spec_from_file_location(
        "amagine_mesh_audit", workspace / "amagine_mesh_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected_dimensions = {}
    if include_overall_targets:
        for axis in ("x", "y", "z"):
            key = f"size{axis.upper()}"
            if key in qa_targets:
                expected_dimensions[axis] = qa_targets[key]
    result = module.audit_mesh(
        str(stl_path),
        expected_components=components,
        expected_dimensions=expected_dimensions,
        dimension_tolerance=qa_targets.get("dimensionTolerance", 0.5),
        expected_volume=(
            qa_targets.get("volume") if include_overall_targets else None
        ),
        volume_tolerance_percent=qa_targets.get("volumeTolerancePercent", 10),
    )
    return (0 if result.get("passed") else 1), result

def normalized_direction(values):
    magnitude = math.sqrt(sum(float(value) ** 2 for value in values))
    if magnitude <= 1e-12:
        raise ValueError("motion direction is zero")
    return tuple(float(value) / magnitude for value in values)

def motion_sample_count(motion):
    requested = int(motion.get("minimumSamples", 3))
    if motion["type"] == "rotation":
        geometric = math.ceil(abs(float(motion["angleDegrees"])) / 10.0)
    elif motion["type"] == "translation":
        geometric = math.ceil(abs(float(motion["distanceMm"])) / 2.0)
    else:
        geometric = max(
            math.ceil(abs(float(motion["angleDegrees"])) / 10.0),
            math.ceil(abs(float(motion["distanceMm"])) / 2.0),
        )
    return min(max(requested, geometric, 3), 144)

def transform_motion(shape, motion, fraction):
    from build123d import Axis as _TrustedAxis, Location as _TrustedLocation
    motion_type = motion["type"]
    result = shape
    if motion_type in {"rotation", "screw"}:
        direction = normalized_direction(motion["axisDirection"])
        result = result.rotate(
            _TrustedAxis(tuple(motion["axisOrigin"]), direction),
            float(motion["angleDegrees"]) * fraction,
        )
    if motion_type in {"translation", "screw"}:
        direction = normalized_direction(
            motion["direction"]
            if motion_type == "translation"
            else motion["axisDirection"]
        )
        distance = float(motion["distanceMm"]) * fraction
        result = result.moved(_TrustedLocation(tuple(value * distance for value in direction)))
    return result

def pairwise_collision_volume(pose, moving_ids, stationary_ids):
    total = 0.0
    worst_pair = None
    worst_pair_volume = 0.0
    for moving_id in moving_ids:
        for stationary_id in stationary_ids:
            intersection = pose[moving_id] & pose[stationary_id]
            if intersection is not None:
                pair_volume = float(intersection.volume)
                total += pair_volume
                if pair_volume > worst_pair_volume:
                    worst_pair_volume = pair_volume
                    worst_pair = {
                        "movingBodyId": moving_id,
                        "stationaryBodyId": stationary_id,
                        "collisionVolumeMm3": round(pair_volume, 6),
                    }
    return total, worst_pair

def describe_pose(pose_context):
    if not pose_context or pose_context.get("stage") == "canonical":
        return "the canonical pose"
    return (
        f"motion {pose_context.get('motionIndex')} "
        f"({pose_context.get('motionType')}) sample "
        f"{pose_context.get('sampleIndex')}/{pose_context.get('sampleCount')} "
        f"(fraction={pose_context.get('fraction')})"
    )

artifacts = []
checks = []
region_reports = None
mechanism_reports = None
overlap_check = None
readback_check = None

def observed_feature_metric(observation, metric):
    if metric == "sizeX":
        return observation.get("size_x")
    if metric == "sizeY":
        return observation.get("size_y")
    if metric == "sizeZ":
        return observation.get("size_z")
    if metric == "volumeMm3":
        return observation.get("volume")
    axis = {"X": 0, "Y": 1, "Z": 2}.get(metric[-1:])
    if axis is None:
        return None
    if metric.startswith("min"):
        return (observation.get("min") or [None, None, None])[axis]
    if metric.startswith("max"):
        return (observation.get("max") or [None, None, None])[axis]
    if metric.startswith("center"):
        low = (observation.get("min") or [None, None, None])[axis]
        high = (observation.get("max") or [None, None, None])[axis]
        return None if low is None or high is None else (low + high) / 2.0
    return None

observations = report.get("observations", {})
for target in feature_specs:
    observation = observations.get(target["featureId"])
    actual = (
        None if observation is None
        else observed_feature_metric(observation, target["metric"])
    )
    tolerance = float(target["tolerance"])
    passed = (
        actual is not None
        and abs(float(actual) - float(target["expected"])) <= tolerance
    )
    checks.append(check(
        f"feature-{target['id']}", passed,
        f"Observed feature {target['featureId']} {target['metric']} matches the frozen target."
        if passed
        else f"Observed feature {target['featureId']} {target['metric']} is missing or outside tolerance.",
        expected=target["expected"], actual=actual, tolerance=tolerance,
    ))

for keep_out_id, keep_out in report.get("keep_outs", {}).items():
    known = bool(keep_out.get("known"))
    maximum = keep_out.get("max_overlap_mm3")
    passed = known and maximum is not None and float(maximum) <= 0.01
    checks.append(check(
        f"keep-out-{keep_out_id}", passed,
        f"Reserved keep-out {keep_out_id} is clear of every published body."
        if passed
        else f"Reserved keep-out {keep_out_id} intersects published geometry or could not be evaluated.",
        expected=0.01, actual=keep_out, tolerance=0.01,
    ))

issues = report.get("issues", [])
missed = [
    issue for issue in issues
    if issue.startswith("BOOLEAN_NO_EFFECT") or issue.startswith("BOOLEAN_FAILED")
]
checks.append(check(
    "missed-cut", not missed,
    "No missed or failed boolean cuts." if not missed else "; ".join(missed),
    expected=0, actual=len(missed),
))
finishing = [
    issue for issue in issues
    if issue.startswith("ROUND_FAILED")
    or issue.startswith("ROUND_PARTIAL")
    or issue.startswith("BEVEL_FAILED")
    or issue.startswith("BEVEL_PARTIAL")
]
checks.append(check(
    "finishing-issues", not finishing,
    "All rounding and bevel operations succeeded."
    if not finishing else "; ".join(finishing),
    expected=0, actual=len(finishing),
))

if _amagine_workflow == "single-color":
    parts = report.get("parts")
    if parts:
        # Multi-body design: every part is its own printable solid. QA the
        # assembly overall dims/volume, then mesh-QA each part's own STL.
        body_overlaps = report.get("overlaps_mm3", {})
        max_body_overlap = max(body_overlaps.values(), default=0.0)
        unknown_body_overlaps = [
            issue for issue in issues if issue.startswith("OVERLAP_UNKNOWN")
        ]
        checks.append(check(
            "assembly-overlap-known", not unknown_body_overlaps,
            "Every printable-body pair produced an exact intersection result."
            if not unknown_body_overlaps else "; ".join(unknown_body_overlaps),
            expected=0, actual=len(unknown_body_overlaps),
        ))
        checks.append(check(
            "assembly-body-overlap", max_body_overlap <= 0.01,
            "Pairwise printable-body overlap must not exceed 0.01 mm³.",
            expected=0.01, actual=max_body_overlap, tolerance=0.01,
        ))
        assembly_bbox = report.get("assembly_bbox", {})
        for axis in ("x", "y", "z"):
            key = f"size{axis.upper()}"
            if key not in qa_targets:
                continue
            actual_size = assembly_bbox.get(f"size_{axis}")
            expected_size = qa_targets[key]
            tolerance = qa_targets.get("dimensionTolerance", 0.5)
            dimension_ok = (
                actual_size is not None
                and abs(actual_size - expected_size) <= tolerance
            )
            checks.append(check(
                f"assembly-dim-{axis}", dimension_ok,
                f"Assembly {axis.upper()} dimension matches the design target.",
                expected=expected_size, actual=actual_size, tolerance=tolerance,
            ))
        if "volume" in qa_targets:
            actual_volume = report.get("assembly_volume_mm3")
            expected_volume = qa_targets["volume"]
            tolerance_percent = qa_targets.get("volumeTolerancePercent", 10)
            volume_delta = (
                abs(actual_volume - expected_volume) / expected_volume * 100
                if actual_volume is not None else float("inf")
            )
            checks.append(check(
                "assembly-volume", volume_delta <= tolerance_percent,
                "Assembly volume matches the design target.",
                expected=expected_volume, actual=actual_volume,
                tolerance=tolerance_percent,
            ))
        expected_part_count = qa_targets.get("componentCount", len(parts))
        checks.append(check(
            "assembly-components",
            len(parts) == expected_part_count,
            "Named printable-body count matches the design target.",
            expected=expected_part_count, actual=len(parts),
        ))
        for part_name, part in parts.items():
            cad_solid_count = part.get("solid_count")
            checks.append(check(
                f"cad-{part_name}-shape-valid",
                part.get("is_valid") is True,
                f"Printable body {part_name} is a valid OpenCascade shape.",
                expected=True, actual=part.get("is_valid"),
            ))
            checks.append(check(
                f"cad-{part_name}-component-count",
                cad_solid_count == 1,
                f"Printable body {part_name} should contain one connected CAD solid; mesh connectivity is authoritative for printable output.",
                expected=1, actual=cad_solid_count,
                warning=cad_solid_count != 1,
            ))
            stl_path = Path(part["stl_file"])
            qa_exit, raw_qa = load_qa(
                stl_path,
                1,
                include_overall_targets=False,
            )
            for item in raw_qa.get("checks", []):
                checks.append(check(
                    f"mesh-{part_name}-{item['id']}",
                    item["passed"], item["message"],
                ))
            artifacts.append({
                "kind": "stl", "path": str(stl_path),
                "fileName": Path(part["stl_file"]).name,
                "mediaType": "model/stl",
            })
            artifacts.append({
                "kind": "step", "path": part["step_file"],
                "fileName": Path(part["step_file"]).name,
                "mediaType": "model/step",
            })
    else:
        step_path = Path(report["step_file"])
        stl_path = Path(report["stl_file"])
        qa_exit, raw_qa = load_qa(
            stl_path, qa_targets.get("componentCount", 1)
        )
        checks.append(check(
            "shape-valid", report.get("is_valid") is True,
            "OpenCascade shape validity check.", expected=True,
            actual=report.get("is_valid"),
        ))
        from build123d import import_step as _trusted_import_step
        reread_shape = _trusted_import_step(str(step_path))
        reread_valid_value = reread_shape.is_valid
        reread_valid = bool(
            reread_valid_value() if callable(reread_valid_value) else reread_valid_value
        )
        checks.append(check(
            "step-readback", reread_valid,
            "Exported STEP re-opened as a valid OpenCascade shape.",
            expected=True, actual=reread_valid,
        ))
        for item in raw_qa.get("checks", []):
            checks.append(check(
                f"mesh-{item['id']}", item["passed"], item["message"]
            ))
        artifacts.extend([
            {"kind": "step", "path": str(step_path), "fileName": "model.step", "mediaType": "model/step"},
            {"kind": "stl", "path": str(stl_path), "fileName": "model.stl", "mediaType": "model/stl"},
        ])
else:
    # Region IDs are the frozen machine keys used by model.py/publish_color_model.
    # Region names are human-readable labels and may contain spaces or change
    # independently of the generated Python identifiers.
    expected_regions = {item["id"]: item for item in color_plan.get("regions", [])}
    actual_regions = report.get("parts", {})
    region_reports = []
    region_name_check = check(
        "color-region-set",
        set(actual_regions) == set(expected_regions),
        "Actual color regions must exactly match the frozen color plan.",
        expected=sorted(expected_regions), actual=sorted(actual_regions),
    )
    checks.append(region_name_check)
    for region_id, region in expected_regions.items():
        actual = actual_regions.get(region_id)
        region_checks = []
        if actual is None:
            region_checks.append(check(
                "region-present", False, f"Missing region ID '{region_id}'.",
                expected=True, actual=False,
            ))
            region_reports.append({
                "regionId": region["id"],
                "componentCount": 0,
                "watertight": False,
                "checks": region_checks,
            })
            continue
        stl_path = Path(actual["stl_file"])
        qa_exit, raw_qa = load_qa(
            stl_path, region["expectedComponentCount"], include_overall_targets=False
        )
        for item in raw_qa.get("checks", []):
            region_checks.append(check(
                f"mesh-{item['id']}", item["passed"], item["message"]
            ))
        region_reports.append({
            "regionId": region["id"],
            "componentCount": int(raw_qa.get("component_count", 0)),
            "watertight": bool(raw_qa.get("watertight")),
            "checks": region_checks,
        })
        artifacts.append({
            "kind": "region-stl", "path": str(stl_path),
            "fileName": f"{region_id}.stl", "mediaType": "model/stl",
            "regionName": region_id,
        })

    assembly_bbox = report.get("assembly_bbox", {})
    for axis in ("x", "y", "z"):
        key = f"size{axis.upper()}"
        if key not in qa_targets:
            continue
        actual_size = assembly_bbox.get(f"size_{axis}")
        expected_size = qa_targets[key]
        tolerance = qa_targets.get("dimensionTolerance", 0.5)
        dimension_ok = (
            actual_size is not None and abs(actual_size - expected_size) <= tolerance
        )
        checks.append(check(
            f"assembly-dim-{axis}", dimension_ok,
            f"Assembly {axis.upper()} dimension matches the design target.",
            expected=expected_size, actual=actual_size, tolerance=tolerance,
        ))
    if "volume" in qa_targets:
        actual_volume = report.get("assembly_volume_mm3")
        expected_volume = qa_targets["volume"]
        tolerance_percent = qa_targets.get("volumeTolerancePercent", 10)
        volume_delta = (
            abs(actual_volume - expected_volume) / expected_volume * 100
            if actual_volume is not None else float("inf")
        )
        checks.append(check(
            "assembly-volume", volume_delta <= tolerance_percent,
            "Assembly volume matches the design target.",
            expected=expected_volume, actual=actual_volume,
            tolerance=tolerance_percent,
        ))

    overlaps = report.get("overlaps_mm3", {})
    max_overlap = max(overlaps.values(), default=0.0)
    overlap_check = check(
        "color-region-overlap", max_overlap <= 0.01,
        "Pairwise color-region overlap must not exceed 0.01 mm³.",
        expected=0.01, actual=max_overlap, tolerance=0.01,
    )

    threemf_name = report.get("threemf_file")
    verify_summary = None
    verify_error = None
    if threemf_name:
        export_spec = importlib.util.spec_from_file_location(
            "amagine_three_mf", workspace / "amagine_three_mf.py"
        )
        export_module = importlib.util.module_from_spec(export_spec)
        export_spec.loader.exec_module(export_module)
        try:
            verify_summary = export_module.inspect_3mf(threemf_name)
            verify_exit = 0
        except Exception as exc:
            verify_exit = 1
            verify_error = str(exc)
    else:
        verify_exit = 1
        verify_error = "3MF export missing"
    expected_object_count = len(expected_regions)
    actual_object_count = (
        verify_summary.get("object_count", 0) if verify_summary else 0
    )
    readback_ok = verify_exit == 0 and actual_object_count == expected_object_count
    readback_check = check(
        "3mf-readback", readback_ok,
        "Amagine3D 3MF readback succeeded."
        if readback_ok else f"3MF readback failed: {verify_error or verify_summary}",
        expected=expected_object_count, actual=actual_object_count,
    )
    if threemf_name:
        artifacts.append({
            "kind": "model-3mf", "path": threemf_name,
            "fileName": "model.3mf", "mediaType": "model/3mf",
        })
    if report.get("step_file"):
        artifacts.append({
            "kind": "step", "path": report["step_file"],
            "fileName": "model.step", "mediaType": "model/step",
        })
    else:
        checks.append(check(
            "assembly-step", False,
            "Best-effort colored assembly STEP was not generated.", warning=True,
        ))

if mechanism_specs:
    from build123d import import_step as _trusted_import_step
    mechanism_reports = []
    part_records = report.get("parts") or {}
    actual_body_ids = set(part_records)
    imported_shapes = {}
    shape_load_errors = {}
    for body_id, part_record in part_records.items():
        step_file = part_record.get("step_file")
        if not step_file:
            shape_load_errors[body_id] = "exported body STEP is missing"
            continue
        try:
            imported_shapes[body_id] = _trusted_import_step(str(step_file))
        except Exception as exc:
            shape_load_errors[body_id] = str(exc)

    for mechanism in mechanism_specs:
        mechanism_id = mechanism["id"]
        prefix = f"mechanism-{mechanism_id}"
        moving_ids = list(mechanism["movingBodyIds"])
        stationary_ids = list(mechanism["stationaryBodyIds"])
        expected_body_ids = set(moving_ids + stationary_ids)
        mechanism_checks = []
        body_set_ok = actual_body_ids == expected_body_ids
        mechanism_checks.append(check(
            f"{prefix}-body-set", body_set_ok,
            "Published bodies exactly match the frozen mechanism partition."
            if body_set_ok
            else "Published bodies do not match the frozen moving/stationary partition.",
            expected=sorted(expected_body_ids), actual=sorted(actual_body_ids),
        ))
        missing_shapes = sorted(expected_body_ids - set(imported_shapes))
        relevant_load_errors = {
            body_id: shape_load_errors[body_id]
            for body_id in sorted(expected_body_ids)
            if body_id in shape_load_errors
        }
        shapes_known = not missing_shapes and not relevant_load_errors
        mechanism_checks.append(check(
            f"{prefix}-step-readback", shapes_known,
            "Every mechanism body re-opened from its exported STEP."
            if shapes_known
            else f"Mechanism STEP readback failed: missing={missing_shapes}, errors={relevant_load_errors}",
            expected=sorted(expected_body_ids),
            actual=sorted(expected_body_ids & set(imported_shapes)),
        ))

        motion_state = {
            "sampledPoseCount": 0,
            "maxCollisionVolumeMm3": 0.0,
            "worstCollision": None,
        }
        collision_errors = []
        clearance_state = {
            item["id"]: {
                "minimum": None,
                "maximum": None,
                "minimumPose": None,
                "maximumPose": None,
                "samples": 0,
                "errors": [],
            }
            for item in mechanism.get("clearanceChecks", [])
        }

        def evaluate_pose(pose, include_intermediate_clearance, pose_context):
            motion_state["sampledPoseCount"] += 1
            try:
                collision, worst_pair = pairwise_collision_volume(
                    pose, moving_ids, stationary_ids
                )
                if collision > motion_state["maxCollisionVolumeMm3"]:
                    motion_state["maxCollisionVolumeMm3"] = collision
                    motion_state["worstCollision"] = {
                        "pose": pose_context,
                        "totalCollisionVolumeMm3": round(collision, 6),
                        "worstPair": worst_pair,
                    }
            except Exception as exc:
                collision_errors.append(str(exc))
            for clearance in mechanism.get("clearanceChecks", []):
                if (
                    clearance["poseScope"] == "intermediate"
                    and not include_intermediate_clearance
                ):
                    continue
                state = clearance_state[clearance["id"]]
                try:
                    distance = float(
                        pose[clearance["leftBodyId"]].distance_to(
                            pose[clearance["rightBodyId"]]
                        )
                    )
                    if state["minimum"] is None or distance < state["minimum"]:
                        state["minimum"] = distance
                        state["minimumPose"] = pose_context
                    if state["maximum"] is None or distance > state["maximum"]:
                        state["maximum"] = distance
                        state["maximumPose"] = pose_context
                    state["samples"] += 1
                except Exception as exc:
                    state["errors"].append(str(exc))

        if shapes_known:
            current_pose = {
                body_id: imported_shapes[body_id] for body_id in expected_body_ids
            }
            evaluate_pose(
                current_pose,
                False,
                {
                    "stage": "canonical",
                    "motionIndex": None,
                    "sampleIndex": 0,
                    "sampleCount": 0,
                    "fraction": 0.0,
                },
            )
            for motion_index, motion in enumerate(mechanism["motions"]):
                segment_start = {
                    body_id: current_pose[body_id] for body_id in moving_ids
                }
                sample_count = motion_sample_count(motion)
                for sample_index in range(1, sample_count + 1):
                    fraction = sample_index / sample_count
                    candidate_pose = dict(current_pose)
                    for body_id in moving_ids:
                        candidate_pose[body_id] = transform_motion(
                            segment_start[body_id], motion, fraction
                        )
                    evaluate_pose(
                        candidate_pose,
                        sample_index < sample_count,
                        {
                            "stage": "motion",
                            "motionIndex": motion_index,
                            "motionType": motion["type"],
                            "sampleIndex": sample_index,
                            "sampleCount": sample_count,
                            "fraction": round(fraction, 6),
                        },
                    )
                current_pose = candidate_pose

        collision_tolerance = float(
            mechanism.get("collisionToleranceMm3", 0.01)
        )
        max_collision = motion_state["maxCollisionVolumeMm3"]
        collision_known = shapes_known and not collision_errors
        collision_ok = collision_known and max_collision <= collision_tolerance
        worst_collision = motion_state["worstCollision"]
        collision_location = "an unknown sampled pose"
        if worst_collision:
            worst_pair = worst_collision.get("worstPair") or {}
            collision_location = (
                f"{worst_pair.get('movingBodyId')} against "
                f"{worst_pair.get('stationaryBodyId')} at "
                f"{describe_pose(worst_collision.get('pose'))}"
            )
        mechanism_checks.append(check(
            f"{prefix}-motion-collision", collision_ok,
            "The complete sampled motion path is collision-free."
            if collision_ok
            else (
                f"Motion collision evaluation failed: {collision_errors}"
                if collision_errors
                else f"The sampled motion path contains body interference: {collision_location}."
            ),
            expected=collision_tolerance,
            actual={
                "maxCollisionVolumeMm3": round(max_collision, 6),
                "worstCollision": worst_collision,
            },
            tolerance=collision_tolerance,
        ))

        observed_clearances = []
        observed_maximum_clearances = []
        for clearance in mechanism.get("clearanceChecks", []):
            state = clearance_state[clearance["id"]]
            minimum = state["minimum"]
            maximum = state["maximum"]
            clearance_known = (
                shapes_known
                and not state["errors"]
                and state["samples"] > 0
                and minimum is not None
                and maximum is not None
            )
            clearance_ok = (
                clearance_known
                and minimum + 1e-6 >= clearance["minimumMm"]
                and (
                    clearance.get("maximumMm") is None
                    or maximum <= clearance["maximumMm"] + 1e-6
                )
            )
            if minimum is not None:
                observed_clearances.append(float(minimum))
            if maximum is not None:
                observed_maximum_clearances.append(float(maximum))
            expected_clearance = {"minimumMm": clearance["minimumMm"]}
            if clearance.get("maximumMm") is not None:
                expected_clearance["maximumMm"] = clearance["maximumMm"]
            actual_clearance = None
            if minimum is not None and maximum is not None:
                actual_clearance = {
                    "minimumMm": round(float(minimum), 6),
                    "maximumMm": round(float(maximum), 6),
                    "minimumPose": state["minimumPose"],
                    "maximumPose": state["maximumPose"],
                }
            clearance_failures = []
            if minimum is not None and minimum + 1e-6 < clearance["minimumMm"]:
                clearance_failures.append(
                    f"minimum {round(float(minimum), 6)} mm at "
                    f"{describe_pose(state['minimumPose'])} is below "
                    f"{clearance['minimumMm']} mm"
                )
            if (
                maximum is not None
                and clearance.get("maximumMm") is not None
                and maximum > clearance["maximumMm"] + 1e-6
            ):
                clearance_failures.append(
                    f"maximum {round(float(maximum), 6)} mm at "
                    f"{describe_pose(state['maximumPose'])} exceeds "
                    f"{clearance['maximumMm']} mm"
                )
            mechanism_checks.append(check(
                f"{prefix}-clearance-{clearance['id']}", clearance_ok,
                "The declared running-clearance range is preserved over its sampled poses."
                if clearance_ok
                else (
                    f"Clearance evaluation failed: {state['errors']}"
                    if state["errors"]
                    else "The declared running-clearance range was violated: "
                    + "; ".join(clearance_failures)
                ),
                expected=expected_clearance,
                actual=actual_clearance,
                tolerance=0.001,
            ))

        mechanism_report = {
            "mechanismId": mechanism_id,
            "sampledPoseCount": motion_state["sampledPoseCount"],
            "maxCollisionVolumeMm3": round(max_collision, 6),
            "checks": mechanism_checks,
        }
        if observed_clearances:
            mechanism_report["minimumClearanceMm"] = round(
                min(observed_clearances), 6
            )
        if observed_maximum_clearances:
            mechanism_report["maximumClearanceMm"] = round(
                max(observed_maximum_clearances), 6
            )
        mechanism_reports.append(mechanism_report)

all_checks = list(checks)
if region_reports:
    all_checks.extend(
        item for region in region_reports for item in region["checks"]
    )
if mechanism_reports:
    all_checks.extend(
        item for mechanism in mechanism_reports for item in mechanism["checks"]
    )
if overlap_check:
    all_checks.append(overlap_check)
if readback_check:
    all_checks.append(readback_check)

qa_report = {
    "schemaVersion": 1,
    "runId": _amagine_run_id,
    "workflowKind": _amagine_workflow,
    "status": "failed" if any(item["status"] == "failed" for item in all_checks) else "passed",
    "checks": checks,
}
if _amagine_workflow == "multi-color":
    qa_report["regionReports"] = region_reports
    qa_report["overlapCheck"] = overlap_check
    qa_report["threeMfReadbackCheck"] = readback_check
if mechanism_reports is not None:
    qa_report["mechanismReports"] = mechanism_reports

json.dumps({
    "buildReport": report,
    "qaReport": qa_report,
    "artifacts": artifacts,
    "stdout": stdout.getvalue(),
})
`;
