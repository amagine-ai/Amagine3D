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
from copy import deepcopy
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
        if isinstance(statement, ast.ClassDef) and statement.name in public_names:
            constructor = next((node for node in statement.body
                                if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None)
            dataclass_decorator = next((node for node in statement.decorator_list
                                       if ast.unparse(node).split("(")[0] == "dataclass"), None)
            if constructor is not None:
                args = deepcopy(constructor.args)
                receiver = args.posonlyargs if args.posonlyargs else args.args
                if receiver:
                    receiver.pop(0)
            elif dataclass_decorator is not None:
                # Dataclass fields define its generated constructor; never import
                # the geometry module merely to discover this lightweight API.
                fields = [node for node in statement.body
                          if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
                args = ast.arguments(
                    posonlyargs=[], args=[ast.arg(arg=node.target.id, annotation=node.annotation) for node in fields],
                    vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                    defaults=[node.value for node in fields if node.value is not None],
                )
            else:
                args = None
            if args is not None:
                signatures.append({
                    "name": statement.name,
                    "parameters": [argument.arg for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]],
                    "signature": f"{statement.name}({ast.unparse(args)})",
                    "description": (ast.get_docstring(statement) or "").split("\n\n")[0],
                })
            for method in statement.body:
                if isinstance(method, ast.FunctionDef) and not method.name.startswith("_"):
                    name = f"{statement.name}.{method.name}"
                    signatures.append({
                        "name": name,
                        "parameters": [argument.arg for argument in [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]],
                        "signature": f"{name}({ast.unparse(method.args)})",
                        "description": (ast.get_docstring(method) or "").split("\n\n")[0],
                    })
            continue
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
                "signature": f"{statement.name}({ast.unparse(statement.args)})",
                "description": (ast.get_docstring(statement) or "").split("\n\n")[0],
            }
        )
    return sorted(signatures, key=lambda item: item["name"])


def _intent_input_constraints() -> dict[str, Any]:
    """Describe compact writer inputs using the validator's live vocabulary."""
    import intent_contract as contract

    return {
        "task_mode": sorted(contract.MODES),
        "representation": sorted(contract.REPRESENTATIONS),
        "manufacturing_mode": sorted(contract.MANUFACTURING_MODES),
        "dimensions_mm": {
            "axes": ["x", "y", "z"],
            "perAxisRequired": ["value", "source", "confidence"],
            "value": "finite positive number; complete physical assembly envelope",
            "source": sorted(contract.SOURCES),
            "confidence": sorted(contract.CONFIDENCE),
            "constraint": "optional {kind: fixed} or {kind: range, min_mm, max_mm}; omitted means fixed",
            "measurement_precision_mm": "optional raw measurement tolerance from 0.0001 to 0.01 mm; default 0.01; only tighten when explicitly required",
        },
        "parts": {
            "shape": "{part_id: {features: [...], ...}}; at least one feature overall",
            "single-part": "exactly one key equal to part; its definition accepts only features",
            "multipart": {
                "minimumParts": 2,
                "minimumInterfaces": 1,
                "requiredPartFields": ["role", "acceptance"],
                "installation": sorted(contract.PART_INSTALLATIONS),
                "defaultInstallation": "interface",
                "installationExemptions": "loose/adhesive exempts a part from interface coverage; multipart still requires at least one interface",
            },
            "featureOwner": "derived from nesting; an explicit feature.part must match its owner",
        },
        "parts.*.features[]": {
            "required": ["id", "evidence", "acceptance"],
            "id": "unique across all parts; " + contract.FEATURE_ID_PATTERN.pattern,
            "optionalEnums": {
                "kind": sorted(contract.FEATURE_KINDS),
                "face": sorted(contract.FACES),
                "direction": sorted(contract.DIRECTIONS),
                "edge_crossing": sorted(contract.EDGE_CROSSING),
            },
            "openingFields": {
                "whenKind": sorted(contract.PLACED_OPENING_KINDS),
                "required": ["face", "direction", "edge_crossing"],
            },
            "faceDirections": {face: sorted(directions) for face, directions in sorted(contract.FACE_DIRECTIONS.items())},
            "directionRules": "direction or edge_crossing requires face; none and surface-normal are also valid for any face",
            "section_dimensions": {
                "scope": "optional list of outer-section dimensions on the owning final semantic BRep STEP; not hole, passage or wall dimensions",
                "plane": "{axis: x/y/z, coordinate_mm: finite number}; absolute semantic coordinates",
                "outer_envelope": "width_u_mm and/or depth_v_mm, each {value, constraint?, measurement_precision_mm?}; same fixed/range rules as dimensions_mm",
                "precision": "0.01 mm default raw measurement tolerance; measurement_precision_mm can tighten to 0.0001 mm; --tol cannot relax section targets",
                "details": "references/evidence-contract.md",
            },
        },
        "critical_features": "IDs from parts.*.features[].id; interface IDs do not qualify unless also declared as features",
        "interfaces[]": {
            "features": "existing feature IDs owned by exactly two distinct parts",
            "between": "derived from feature ownership; omit rather than repeat it",
            "connection": sorted(contract.INTERFACE_CONNECTIONS),
            "assembly_axis": sorted(contract.ASSEMBLY_AXES),
        },
        "details": ["references/evidence-contract.md", "references/multipart-connections.md"],
    }


class _ManagedSignatures:
    """Resolve installed Python exports without importing build123d or OCCT.

    Only source inside the managed package is inspected. Dynamic exports and
    external constructors are left unknown rather than assigned guessed APIs.
    """

    def __init__(self) -> None:
        spec = util.find_spec(MANAGED_MODULE)
        if spec is None or not isinstance(spec.origin, str):
            raise ValueError(f"managed module {MANAGED_MODULE!r} is not installed")
        self.root = Path(spec.origin).parent
        self.modules: dict[str, tuple[ast.Module, bool] | None] = {}

    def _module(self, name: str) -> tuple[ast.Module, bool] | None:
        if name not in self.modules:
            if name != MANAGED_MODULE and not name.startswith(MANAGED_MODULE + "."):
                return None
            relative = name.split(".")[1:]
            path = self.root.joinpath(*relative)
            is_package = path.is_dir()
            path = path / "__init__.py" if is_package else path.with_suffix(".py")
            try:
                self.modules[name] = (
                    ast.parse(path.read_text(encoding="utf-8"), str(path)),
                    is_package,
                )
            except (OSError, SyntaxError):
                self.modules[name] = None
        return self.modules[name]

    def _resolve(
        self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> tuple[str, ast.ClassDef | list[ast.FunctionDef | ast.AsyncFunctionDef]] | None:
        if (module, name) in seen:
            return None
        source = self._module(module)
        if source is None:
            return None
        tree, is_package = source
        seen = seen | {(module, name)}
        for statement in reversed(tree.body):
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == name:
                return module, [
                    node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
                ]
            if isinstance(statement, ast.ClassDef) and statement.name == name:
                return module, statement
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    if isinstance(statement.value, ast.Name):
                        return self._resolve(module, statement.value.id, seen)
                    return None
            if not isinstance(statement, ast.ImportFrom):
                continue
            package = module if is_package else module.rpartition(".")[0]
            imported = (
                util.resolve_name("." * statement.level + (statement.module or ""), package)
                if statement.level else statement.module or ""
            )
            for alias in statement.names:
                if alias.name == "*":
                    target = self._module(imported)
                    if target is None:
                        continue
                    # Respect literal export lists, so an imported implementation
                    # detail cannot shadow the actual public definition.
                    exports = None
                    for node in target[0].body:
                        if isinstance(node, ast.Assign) and any(
                            isinstance(item, ast.Name) and item.id == "__all__"
                            for item in node.targets
                        ):
                            try:
                                exports = ast.literal_eval(node.value)
                            except (ValueError, TypeError):
                                pass
                    if exports is not None and name not in exports:
                        continue
                    resolved = self._resolve(imported, name, seen)
                    if resolved is not None or exports is not None:
                        return resolved
                elif (alias.asname or alias.name) == name:
                    return self._resolve(imported, alias.name, seen)
        return None

    def _constructor(
        self, module: str, node: ast.ClassDef, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        if (module, node.name) in seen:
            return []
        methods = [
            item for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"
        ]
        if methods:
            return methods
        # A single Python base has an unambiguous inherited constructor. Do not
        # pretend to resolve metaclass or multiple-inheritance call semantics.
        if len(node.bases) == 1:
            base = node.bases[0]
            if isinstance(base, ast.Subscript):
                base = base.value
            if isinstance(base, ast.Name):
                resolved = self._resolve(module, base.id)
                if resolved is not None and isinstance(resolved[1], ast.ClassDef):
                    return self._constructor(*resolved, seen | {(module, node.name)})
        return []

    def query(self, name: str) -> dict[str, Any]:
        resolved = self._resolve(MANAGED_MODULE, name)
        if resolved is None:
            return {"signatureUnavailable": "No statically inspectable Python callable."}
        module, node = resolved
        is_class = isinstance(node, ast.ClassDef)
        methods = self._constructor(module, node) if is_class else node
        if not methods:
            return {"signatureUnavailable": "No source-defined constructor; this may be a constant, enum, or dynamic callable."}
        signatures = []
        parameters = []
        for method in methods:
            args = deepcopy(method.args)
            if is_class:
                receiver = args.posonlyargs if args.posonlyargs else args.args
                if receiver:
                    receiver.pop(0)
            signatures.append(f"{name}({ast.unparse(args)})")
            parameters.append([
                argument.arg for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]
            ])
        result = {
            "name": name,
            "signature": signatures[-1],
            "parameters": parameters[-1],
            "description": (ast.get_docstring(node if is_class else methods[-1]) or "").split("\n\n")[0],
            "definedIn": module,
            "signatureSource": "installed-python-source",
        }
        if len(signatures) > 1:
            result["overloadSignatures"] = signatures[:-1]
        return result


MODE_CAPABILITIES = [
    {
        "id": "brep-part",
        "physicalAuthority": "build123d solid",
        "outputs": ["STEP", "STL", "display GLB"],
        "requirements": [
            "one valid solid",
            "named parameters",
            "scene display-only nodes remain GLB-only",
            "unit scale",
        ],
    },
    {
        "id": "brep-assembly",
        "physicalAuthority": "labeled build123d solids",
        "outputs": ["part STEP", "assembly STEP", "part STL", "plate STL", "display GLB"],
        "requirements": [
            "one valid solid per manufactured part",
            "source-authored placement",
            "declared interfaces",
            "scene display-only nodes remain GLB-only",
        ],
    },
    {
        "id": "manufactured-color-regions",
        "physicalAuthority": "exclusive BRep volumetric regions of physical parts",
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
        "id": "owned-brep-features",
        "provider": "geometry_binding",
        "requires": ["BrepFeature", "BrepFeature.cut_from", "BrepFeature.bind"],
        "useWhen": "one owned feature should drive a checked cut, measured observation and scene binding without repeating its identity",
    },
    {
        "id": "checked-brep-features",
        "provider": "cad_helpers",
        "requires": ["checked_cut", "checked_union"],
        "useWhen": "additive or subtractive BRep features must prove material effect and connected topology",
    },
    {
        "id": "section-loft-shell",
        "provider": "build123d",
        "requires": ["loft", "Plane", "RectangleRounded"],
        "useWhen": "product envelopes need independently controlled sections; cut a BRep cavity and validate wall thickness",
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
        {"write_intent", "write_scene", "paired_dimensions", "paired_interface"},
    )
    for helper in authoring_helpers:
        if helper["name"] == "write_intent":
            helper["inputConstraints"] = _intent_input_constraints()
    session_helpers = _function_signatures(root / "build_session.py", {"BuildSession"})
    draft_helpers = _function_signatures(root / "cad_draft.py", {"export_draft"})
    measurement_helpers = _function_signatures(root / "brep_measurements.py", {"measure_section", "measure_step"})
    geometry_helpers = _function_signatures(
        root / "cad_helpers.py",
        {
            "checked_chamfer",
            "checked_cut",
            "checked_fillet",
            "checked_union",
            "observe",
            "export_part",
            "export_assembly",
        },
    )
    binding_helpers = _function_signatures(
        root / "geometry_binding.py",
        {"BrepFeature", "bind_brep_feature", "bind_display_component"},
    )
    planning_helpers = _function_signatures(root / "plate_layout.py", {"plan_plates"})
    installation_helpers = _function_signatures(
        root / "installation_check.py", {"check_installation", "bind_installation_check"},
    )
    geometry_helper_names = {item["name"] for item in geometry_helpers}
    binding_helper_names = {item["name"] for item in binding_helpers}
    helper_symbols = {
        item["name"]: {**item, "provider": provider}
        for provider, helpers in (
            ("authoring", authoring_helpers),
            ("build_session", session_helpers),
            ("cad_draft", draft_helpers),
            ("brep_measurements", measurement_helpers),
            ("cad_helpers", geometry_helpers),
            ("geometry_binding", binding_helpers),
            ("installation_check", installation_helpers),
            ("interface_recipes", interface_helpers),
            ("plate_layout", planning_helpers),
        )
        for item in helpers
    }
    all_names = available | helper_symbols.keys()
    managed_signatures = _ManagedSignatures() if set(requested) & available else None
    query = {
        name: {
            "available": name in all_names,
            **(
                helper_symbols[name]
                if name in helper_symbols
                else {"provider": "build123d", **managed_signatures.query(name)}
                if name in available
                else {"suggestions": get_close_matches(name, sorted(all_names), n=5)}
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
                    "geometry_binding": binding_helper_names,
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
                "draft": "a3d draft SOURCE.py produces unvalidated previews before intent and feature registration; never final artifacts",
            },
            "authoringHelpers": authoring_helpers,
            "buildSessionHelpers": session_helpers,
            "draftHelpers": draft_helpers,
            "measurementHelpers": measurement_helpers,
            "geometryHelpers": geometry_helpers,
            "geometryBindingHelpers": binding_helpers,
            "manufacturingPlanningHelpers": planning_helpers,
            "installationHelpers": installation_helpers,
            "interfaceRecipes": interface_helpers,
            "interfaceProofs": proof_capabilities(),
            "modelingRecipes": recipes,
            "principles": [
                "choose construction from controlling dimensions and evidence",
                "author every manufactured part as a valid BRep solid",
                "use key-section lofts for product envelopes; ruled transitions are acceptable when the form permits",
                "use named parameters and source-authored coordinate frames",
                "derive mating geometry from one clearance recipe",
                "derive bound feature meshes and final STEP, STL, 3MF and GLB from the authored BRep geometry",
                "build the complete physical part before manufactured-color partitioning",
                "keep display-only decoration outside manufacturing geometry",
            ],
        },
        "policies": {
            "geometryToleranceMm": GEOMETRY_TOLERANCE_MM,
            "representationMasters": ["brep"],
        },
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
    if args.symbol:
        result = {key: result[key] for key in ("schema", "runtime", "fingerprint", "query")}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
