"""Describe the installed CAD authoring surface without executing model source.

The manifest is intentionally independent from any requested object.  It gives
the Agent one compact, version-bound answer about the managed build123d API,
the repository authoring helpers, and the artifact families supported by the
single text-a3d surface.  It is advisory context for the existing Agent loop;
it is not a workflow engine and does not select a modeling strategy.
"""

from __future__ import annotations

import argparse
import ast
from difflib import get_close_matches
from hashlib import sha256
from importlib import metadata, util
import json
from pathlib import Path
import platform
import sys
from typing import Any, Iterable

from capability_registry import GEOMETRY_TOLERANCE_MM, proof_capabilities


CAPABILITY_SCHEMA = "evidence-cad-capabilities/v1"
MANAGED_MODULE = "build123d"


def literal_public_names(module_name: str = MANAGED_MODULE) -> set[str]:
    """Read a module's literal ``__all__`` without importing the CAD kernel."""

    spec = util.find_spec(module_name)
    raw_origin = spec.origin if spec is not None else None
    if not isinstance(raw_origin, str):
        raise ValueError(f"managed module {module_name!r} is not installed")
    origin = Path(raw_origin)
    if not origin.is_file() or origin.suffix.lower() != ".py":
        raise ValueError(
            f"managed module {module_name!r} has no inspectable Python source"
        )
    try:
        module_tree = ast.parse(origin.read_text(encoding="utf-8"), str(origin))
    except (OSError, SyntaxError) as error:
        raise ValueError(
            f"managed module {module_name!r} cannot be inspected: {error}"
        ) from error
    for statement in module_tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets: Iterable[ast.expr]
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        else:
            targets = (statement.target,)
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        try:
            value = ast.literal_eval(statement.value)
        except (ValueError, TypeError, SyntaxError) as error:
            raise ValueError(
                f"managed module {module_name!r} has a non-literal __all__"
            ) from error
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(
                f"managed module {module_name!r} has an invalid __all__"
            )
        return set(value)
    raise ValueError(f"managed module {module_name!r} does not declare __all__")


def _module_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _function_signatures(path: Path, public_names: set[str]) -> list[dict[str, Any]]:
    """Return compact signatures from a repository helper without importing it."""

    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    signatures: list[dict[str, Any]] = []
    for statement in tree.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if statement.name.startswith("_") or statement.name not in public_names:
            continue
        positional = [argument.arg for argument in statement.args.posonlyargs]
        positional.extend(argument.arg for argument in statement.args.args)
        keywords = [argument.arg for argument in statement.args.kwonlyargs]
        signatures.append(
            {
                "name": statement.name,
                "parameters": positional + keywords,
            }
        )
    return sorted(signatures, key=lambda item: item["name"])


MODE_CAPABILITIES = [
    {
        "id": "brep-part",
        "physicalAuthority": "build123d solid",
        "outputs": ["STEP", "STL", "display GLB"],
        "requirements": ["one valid solid", "named parameters", "unit scale"],
    },
    {
        "id": "brep-assembly",
        "physicalAuthority": "labeled build123d solids",
        "outputs": ["part STEP", "assembly STEP", "part STL", "plate STL", "display GLB"],
        "requirements": [
            "one valid solid per manufactured part",
            "source-authored placement",
            "declared interfaces",
        ],
    },
    {
        "id": "hybrid",
        "physicalAuthority": "one BRep or watertight mesh master per manufactured part",
        "outputs": ["eligible part STEP", "part STL", "3MF", "display GLB"],
        "requirements": [
            "one representation master per part",
            "fit-critical mechanical structure remains an independent BRep part",
            "organic mesh shell and BRep parts meet through declared interfaces",
            "a fused mesh-master part never claims editable STEP authority",
        ],
    },
    {
        "id": "manufactured-color-regions",
        "physicalAuthority": "exclusive volumetric regions of physical parts",
        "outputs": ["region or part STL", "colored 3MF", "display GLB"],
        "requirements": [
            "build the complete physical part before partitioning",
            "watertight regions",
            "no volumetric region overlap",
            "3MF readback",
        ],
    },
]


MODELING_RECIPES = [
    {
        "id": "checked-brep-features",
        "provider": "cad_helpers",
        "requires": ["checked_cut", "checked_union"],
        "useWhen": "additive or subtractive BRep features must prove material effect and connected topology",
    },
    {
        "id": "sdf-organic-shell",
        "provider": "organic_shell",
        "requires": ["build_organic_shell", "self_supporting_cavity"],
        "useWhen": "user landmarks require a watertight irregular shell with a constructive cavity and print plan",
    },
    {
        "id": "axisymmetric-profile",
        "provider": "build123d",
        "requires": ["BuildLine", "make_face", "revolve"],
        "useWhen": "a radial profile makes the controlling dimensions explicit",
    },
    {
        "id": "profile-extrusion",
        "provider": "build123d",
        "requires": ["BuildSketch", "extrude"],
        "useWhen": "a planar profile and thickness control the solid",
    },
    {
        "id": "path-sweep",
        "provider": "build123d",
        "requires": ["sweep"],
        "useWhen": "a section follows a path whose tangent controls orientation",
    },
    {
        "id": "source-positioned-assembly",
        "provider": "build123d",
        "requires": ["Location", "RigidJoint"],
        "useWhen": "part-local datums or joints should recompute assembly placement",
    },
]


def build_manifest(symbols: Iterable[str] = ()) -> dict[str, Any]:
    available = literal_public_names()
    requested = sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
    root = Path(__file__).resolve().parent
    interface_helpers = _function_signatures(
        root / "interface_recipes.py",
        {
            "inset_pocket",
            "collar_socket",
            "hinge_pin",
            "pin_socket",
            "retained_slider",
            "self_tapping_screw_pair",
        },
    )
    authoring_helpers = _function_signatures(
        root / "authoring.py",
        {"paired_dimensions", "paired_interface"},
    )
    organic_shell_helpers = _function_signatures(
        root / "organic_shell.py",
        {"build_organic_shell", "self_supporting_cavity"},
    )
    geometry_helpers = _function_signatures(
        root / "cad_helpers.py",
        {
            "checked_chamfer",
            "checked_cut",
            "checked_fillet",
            "checked_union",
            "observe",
        },
    )
    organic_shell_names = {item["name"] for item in organic_shell_helpers}
    geometry_helper_names = {item["name"] for item in geometry_helpers}
    query = {
        name: {
            "available": name in available,
            **(
                {}
                if name in available
                else {"suggestions": get_close_matches(name, sorted(available), n=5)}
            ),
        }
        for name in requested
    }
    recipes = [
        {
            **recipe,
            "available": set(recipe["requires"]).issubset(
                {
                    "build123d": available,
                    "cad_helpers": geometry_helper_names,
                    "organic_shell": organic_shell_names,
                }[recipe["provider"]]
            ),
        }
        for recipe in MODELING_RECIPES
    ]
    manifest = {
        "schema": CAPABILITY_SCHEMA,
        "runtime": {
            "build123d": _module_version("build123d"),
            "manifold3d": _module_version("manifold3d"),
            "python": platform.python_version(),
        },
        "build123d": {
            "explicitImportsRequired": True,
            "publicSymbols": sorted(available),
        },
        "authoring": {
            "agentLoop": "agent-directed",
            "artifactModes": MODE_CAPABILITIES,
            "contract": {
                "intent": "separate immutable contract source",
                "scene": "mutable semantic implementation generated by build source",
                "compile": "cad_compile is the single build and audit boundary",
            },
            "authoringHelpers": authoring_helpers,
            "geometryHelpers": geometry_helpers,
            "interfaceRecipes": interface_helpers,
            "interfaceProofs": proof_capabilities(),
            "modelingRecipes": recipes,
            "organicShellHelpers": organic_shell_helpers,
            "principles": [
                "choose construction from controlling dimensions and evidence",
                "use named parameters and source-authored coordinate frames",
                "derive mating geometry from one clearance recipe",
                "keep organic mesh shells and fit-critical BRep structure as independent assembled parts",
                "build the complete physical part before manufactured-color partitioning",
                "keep display-only decoration outside manufacturing geometry",
            ],
        },
        "policies": {"geometryToleranceMm": GEOMETRY_TOLERANCE_MM},
    }
    fingerprint_payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["fingerprint"] = sha256(fingerprint_payload).hexdigest()
    if query:
        manifest["query"] = query
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = build_manifest(args.symbol)
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
