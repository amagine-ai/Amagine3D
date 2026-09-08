"""Run a provisional BRep source and render it without a final CAD contract.

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

from cad_compile import CommandRunner, ConfigurationError, _positive_timeout, _workspace_path, _write_json


DRAFT_SCHEMA = "a3d-draft-result/v1"
GEOMETRY_SCHEMA = "a3d-draft-geometry/v1"


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}


def export_draft(parts: Mapping[str, Any], *, references: Mapping[str, Any] | None = None) -> dict:
    """Preview named BRep parts and optional component envelopes via a3d draft.

    No intent, features or manufacturing claims are required. All geometry is
    display-only; blue parts and orange references retain their source placement
    in millimetres, Z up. This call cannot run as a final compile export.
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
              "artifacts": {"step": _binding(step), "glb": _binding(glb), "preview": _binding(preview)}}
    _write_json(manifest_path, result)
    return result


def run_draft(source: Path, *, workspace: Path, timeout_seconds: float = 120.0) -> dict:
    """Execute source once in a unique draft directory; never promote its files."""
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ConfigurationError("draft workspace must be an existing directory")
    timeout_seconds = _positive_timeout(str(timeout_seconds))
    source = _workspace_path(workspace, source, "draft source", must_exist=True)
    if source.suffix.lower() != ".py":
        raise ConfigurationError("draft source must be a Python file")
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
    runner = CommandRunner(log)
    command = runner.run(
        "draft-source", [sys.executable, str(source)], cwd=workspace, timeout_seconds=timeout_seconds,
        env_extra={"AMAGINE3D_SOURCE_PHASE": "draft", "AMAGINE3D_DRAFT_DIR": str(output),
                   "AMAGINE3D_DRAFT_RUN_ID": run_id, "AMAGINE3D_OUTPUT_DIR": str(output),
                   "AMAGINE3D_INTENT_PATH": "", "AMAGINE3D_SCENE_PATH": "",
                   "AMAGINE3D_COMPILE_RUN_ID": "", "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": "",
                   "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(Path(__file__).resolve().parent) + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    result["elapsedMs"] = command.elapsed_ms
    result["log"] = _binding(log)
    if command.timed_out or command.returncode != 0:
        result["issues"].append({"code": "DRAFT.TIMEOUT" if command.timed_out else "DRAFT.SOURCE_FAILED",
                                 "message": "draft exceeded its deadline" if command.timed_out else "draft source failed; inspect draft.log"})
    else:
        try:
            if _binding(source) != source_binding:
                raise ValueError("draft source changed during execution")
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
            result.update(status="draft", artifacts=manifest["artifacts"], objects=manifest["objects"])
            result["geometry"] = _binding(output / "draft-geometry.json")
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["issues"].append({"code": "DRAFT.INCOMPLETE", "message": str(error)})
    _write_json(Path(result["result"]), result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--timeout-seconds", type=_positive_timeout, default=120.0)
    args = parser.parse_args(argv)
    try:
        result = run_draft(args.source, workspace=args.workspace, timeout_seconds=args.timeout_seconds)
    except (ConfigurationError, OSError) as error:
        print(json.dumps({"schema": DRAFT_SCHEMA, "status": "failed", "deliveryReady": False, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "draft" else 1


if __name__ == "__main__":
    raise SystemExit(main())
