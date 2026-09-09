"""Preview BRep geometry from a BuildSession source before final acceptance.

Before intent, use BuildSession(..., part_names=(...)) and its normal export().
For an existing intent-bound source, supply --intent INTENT.json. The same source
can later run through a3d compile; export_draft(parts) also remains available.

Drafts use the same managed Python, process deadline and workspace path boundary
as compile. Author source is trusted Python, not an OS-sandboxed program. No
draft artifact is a final build report, scene, QA result or publication pointer.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import uuid4

from cad_compile import CommandRunner, ConfigurationError, _agent_summary, _positive_timeout, _workspace_path, _write_json
from cad_diagnostics import SOURCE_DIAGNOSTICS_SCHEMA


DRAFT_SCHEMA = "a3d-draft-result/v1"
GEOMETRY_SCHEMA = "a3d-draft-geometry/v1"
CONSTRUCTION_FEATURE_SCOPE = (
    "Registered construction features only; not intent requirements or a complete operation inventory. "
    "Some finish operations and native edits are not registered."
)
SOURCE_GUIDANCE = ("Before intent, use BuildSession(..., part_names=(...)) and export(); "
                   "for an existing intent-bound source, supply --intent INTENT.json. "
                   "export_draft(parts) is also supported. Final acceptance uses a3d compile.")


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}


def _construction_features(features: Mapping[str, Any], parts) -> dict[str, dict[str, str]]:
    if not isinstance(features, Mapping):
        raise ValueError("draft construction_features must map IDs to owner and role")
    records = {}
    for feature_id, feature in features.items():
        if not isinstance(feature_id, str) or not feature_id.strip():
            raise ValueError("every draft construction feature needs a nonempty ID")
        if not isinstance(feature, Mapping) or set(feature) != {"owner", "role"}:
            raise ValueError(f"draft construction feature {feature_id!r} needs only owner and role")
        owner, role = feature["owner"], feature["role"]
        if not isinstance(owner, str) or owner not in parts:
            raise ValueError(f"draft construction feature {feature_id!r} owner must name a proposed part")
        if not isinstance(role, str) or role not in {"solid", "cutter", "separate"}:
            raise ValueError(f"draft construction feature {feature_id!r} role must be solid, cutter or separate")
        records[feature_id] = {"owner": owner, "role": role}
    return records


def export_draft(parts: Mapping[str, Any], *, references: Mapping[str, Any] | None = None,
                 construction_features: Mapping[str, Mapping[str, str]] | None = None) -> dict:
    """Call export_draft(parts) from a3d draft to preview named BRep parts.
    BuildSession.export() uses this same preview path under a3d draft. Neither
    form bypasses the final intent, source or manufacturing checks in a3d compile.

    No intent, features or manufacturing claims are required. All geometry is
    display-only; blue parts and orange references retain their source placement
    in millimetres, Z up. This call cannot run as a final compile export.
    construction_features optionally maps registered IDs to owner/role; it does
    not declare intent requirements or cover all finish operations/native edits.
    """
    if os.environ.get("AMAGINE3D_SOURCE_PHASE") != "draft":
        raise RuntimeError("export_draft requires a3d draft; use the complete intent and compile path for final artifacts")
    raw_directory = os.environ.get("AMAGINE3D_DRAFT_DIR")
    run_id = os.environ.get("AMAGINE3D_DRAFT_RUN_ID")
    if not raw_directory or not run_id:
        raise RuntimeError("export_draft requires the managed draft run directory")
    output = _workspace_path(Path.cwd().resolve(), Path(raw_directory), "draft directory", must_exist=False)
    manifest_path = output / "draft-geometry.json"
    if manifest_path.exists():
        raise ValueError("call export_draft once with all draft parts and references")
    if not isinstance(parts, Mapping) or not parts:
        raise ValueError("draft parts must be a nonempty mapping of names to BRep solids")
    references = {} if references is None else references
    if not isinstance(references, Mapping):
        raise ValueError("draft references must map names to BRep solids")
    if set(parts) & set(references):
        raise ValueError("draft part and reference names must be distinct")
    construction_records = _construction_features({} if construction_features is None else construction_features, parts)

    from build123d import Compound, Unit, export_step
    from cpu_z_buffer import DEFAULT_MATERIAL, render_contact_sheet
    from display_glb import appearance, export_display_glb
    from export_audit import geometry_record
    from geometry_binding import shape_to_mesh
    from render_preview import _render_inputs

    items, shapes, records = [], [], []
    for role, members, color in (("proposed-part", parts, "#6891B5"),
                                  ("component-reference", references, "#E8A54B")):
        for name, original in members.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("every draft object needs a nonempty name")
            shape = deepcopy(original)
            mesh = shape_to_mesh(shape, f"draft {name}")
            shape.label = f"DRAFT {name}"
            shapes.append(shape)
            items.append((name, mesh, appearance(color)))
            records.append({"name": name, "role": role, "geometry": geometry_record(shape)})

    output.mkdir(parents=True, exist_ok=True)
    step, glb, preview = (output / name for name in ("draft.step", "draft.glb", "draft-preview.png"))
    export_step(Compound(children=shapes), str(step), unit=Unit.MM)
    export_display_glb((), glb, display_items=items,
                       metadata={"status": "draft", "deliveryReady": False, "runId": run_id})
    contact = render_contact_sheet(
        _render_inputs(glb, DEFAULT_MATERIAL), 640,
        title="DRAFT | unvalidated geometry | blue: proposed parts | orange: component references",
    )
    contact.image.save(preview, format="PNG")
    result = {"schema": GEOMETRY_SCHEMA, "status": "draft", "deliveryReady": False,
              "runId": run_id, "units": "mm", "coordinateSystem": {"handedness": "right", "up": "Z"},
              "objects": records,
              "constructionFeatures": construction_records, "constructionFeatureScope": CONSTRUCTION_FEATURE_SCOPE,
              "artifacts": {"step": _binding(step), "glb": _binding(glb), "preview": _binding(preview)}}
    _write_json(manifest_path, result)
    return result


def run_draft(source: Path, *, workspace: Path, timeout_seconds: float = 120.0,
              intent: Path | None = None) -> dict:
    """Execute source once in a unique draft directory; never promote its files."""
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ConfigurationError("draft workspace must be an existing directory")
    timeout_seconds = _positive_timeout(str(timeout_seconds))
    source = _workspace_path(workspace, source, "draft source", must_exist=True)
    if source.suffix.lower() != ".py":
        raise ConfigurationError("draft source must be a Python file")
    if intent is not None:
        intent = _workspace_path(workspace, intent, "draft intent", must_exist=True)
    intent_binding = _binding(intent) if intent is not None else None
    drafts = _workspace_path(workspace, Path(".amagine3d-drafts"), "draft output", must_exist=False)
    run_id = str(uuid4())
    output = drafts / run_id
    output.mkdir(parents=True, exist_ok=False)
    source_binding = _binding(source)
    log = output / "draft.log"
    result = {"schema": DRAFT_SCHEMA, "status": "failed", "deliveryReady": False,
              "runId": run_id, "source": source_binding, "result": str(output / "draft-result.json"),
              "artifacts": {}, "issues": [],
              "limitations": ["Provisional geometry only; intent, feature acceptance, installation and print QA have not run."]}
    if intent_binding is not None:
        result["intent"] = intent_binding
    runner = CommandRunner(log)
    diagnostics_path = output / "source-diagnostics.json"
    command = runner.run(
        "draft-source", [sys.executable, str(source)], cwd=workspace, timeout_seconds=timeout_seconds,
        env_extra={"AMAGINE3D_SOURCE_PHASE": "draft", "AMAGINE3D_DRAFT_DIR": str(output),
                   "AMAGINE3D_DRAFT_RUN_ID": run_id, "AMAGINE3D_OUTPUT_DIR": str(output),
                   "AMAGINE3D_INTENT_PATH": str(intent) if intent is not None else "", "AMAGINE3D_SCENE_PATH": "",
                   "AMAGINE3D_COMPILE_RUN_ID": "", "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": str(diagnostics_path),
                   "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(Path(__file__).resolve().parent) + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    result["elapsedMs"] = command.elapsed_ms
    result["log"] = _binding(log)
    source_issues = []
    if diagnostics_path.is_file():
        try:
            if diagnostics_path.is_symlink() or diagnostics_path.resolve().parent != output:
                raise ValueError("source diagnostics must stay in this draft directory")
            diagnostics_binding = _binding(diagnostics_path)
            diagnostics = json.loads(diagnostics_path.read_text())
            if (diagnostics.get("schema") != SOURCE_DIAGNOSTICS_SCHEMA or diagnostics.get("runId") != run_id
                    or not isinstance(diagnostics.get("issues"), list) or _binding(diagnostics_path) != diagnostics_binding):
                raise ValueError("source diagnostics are not bound to this draft")
            if any(not isinstance(item, dict)
                   or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("code", "message"))
                   or not isinstance(item.get("severity", "error"), str)
                   or item.get("severity", "error") not in {"error", "warning"} for item in diagnostics["issues"]):
                raise ValueError("malformed source diagnostic issue")
            source_issues = [item for item in diagnostics["issues"] if item.get("severity", "error") == "error"]
            if type(diagnostics.get("pass")) is not bool or diagnostics["pass"] != (not source_issues):
                raise ValueError("source diagnostic pass/issue mismatch")
            result["sourceDiagnostics"] = diagnostics_binding
        except (OSError, ValueError, AttributeError) as error:
            result["diagnosticWarning"] = str(error)
    if command.timed_out or command.returncode != 0 or source_issues or "diagnosticWarning" in result:
        issue = {"code": "DRAFT.TIMEOUT" if command.timed_out else "DRAFT.SOURCE_FAILED",
                 "message": "draft exceeded its deadline" if command.timed_out else "draft source failed; inspect draft.log"}
        if not command.timed_out:
            issue.update(detail=command.output_tail[-1600:], repairHint=SOURCE_GUIDANCE)
        if source_issues:
            issue["sourceIssue"] = _agent_summary({"issues": source_issues[:1]})["issues"][0]
            issue["omittedSourceIssueCount"] = len(source_issues) - 1
            issue["repairHint"] = "Use the sourceIssue measurements to repair the geometry; full evidence is in sourceDiagnostics."
        result["issues"].append(issue)
    else:
        try:
            if _binding(source) != source_binding:
                raise ValueError("draft source changed during execution")
            if intent is not None and _binding(intent) != intent_binding:
                raise ValueError("draft intent changed during execution")
            manifest = json.loads((output / "draft-geometry.json").read_text())
            if (not isinstance(manifest, dict) or manifest.get("schema") != GEOMETRY_SCHEMA
                    or manifest.get("status") != "draft" or manifest.get("runId") != run_id
                    or manifest.get("deliveryReady") is not False
                    or not isinstance(manifest.get("artifacts"), dict) or not isinstance(manifest.get("objects"), list)):
                raise ValueError("source did not emit current draft geometry")
            for kind, filename in (("step", "draft.step"), ("glb", "draft.glb"), ("preview", "draft-preview.png")):
                path = _workspace_path(output, Path(filename), f"draft {kind}", must_exist=True)
                if manifest["artifacts"].get(kind) != _binding(path):
                    raise ValueError(f"draft {kind} does not match its recorded bytes")
            construction_records = _construction_features(
                manifest.get("constructionFeatures", {}),
                {item["name"] for item in manifest["objects"] if item["role"] == "proposed-part"},
            )
            if manifest.get("constructionFeatureScope", CONSTRUCTION_FEATURE_SCOPE) != CONSTRUCTION_FEATURE_SCOPE:
                raise ValueError("draft construction feature scope changed")
            result.update(status="draft", artifacts=manifest["artifacts"], objects=manifest["objects"])
            result.update(constructionFeatures=construction_records, constructionFeatureScope=CONSTRUCTION_FEATURE_SCOPE)
            result["geometry"] = _binding(output / "draft-geometry.json")
        except (OSError, ValueError, KeyError, TypeError) as error:
            issue = {"code": "DRAFT.INCOMPLETE", "message": str(error)}
            if not (output / "draft-geometry.json").exists():
                issue["repairHint"] = SOURCE_GUIDANCE
            result["issues"].append(issue)
    _write_json(Path(result["result"]), result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--intent", type=Path, help="Existing contract for an intent-bound BuildSession source")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--timeout-seconds", type=_positive_timeout, default=120.0)
    args = parser.parse_args(argv)
    try:
        result = run_draft(args.source, workspace=args.workspace, timeout_seconds=args.timeout_seconds,
                           intent=args.intent)
    except (ConfigurationError, OSError) as error:
        print(json.dumps({"schema": DRAFT_SCHEMA, "status": "failed", "deliveryReady": False, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "draft" else 1


if __name__ == "__main__":
    raise SystemExit(main())
