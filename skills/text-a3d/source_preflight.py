"""Statically validate generated Python against the managed CAD API.

This check deliberately does not import build123d and never executes the
Agent-authored source.  It reads the installed package's literal ``__all__``
declaration so API diagnostics match the pinned runtime without paying the
OCCT import cost.
"""

from __future__ import annotations

import argparse
import ast
import builtins
from hashlib import sha256
import json
from pathlib import Path
import symtable
from typing import Any

from capability_manifest import literal_public_names


PREFLIGHT_SCHEMA = "evidence-python-source-preflight/v1"
MANAGED_MODULE = "build123d"
FORBIDDEN_MESH_REPAIRS = {
    "fill_holes",
    "fix_inversion",
    "fix_normals",
    "fix_winding",
    "stitch",
}


def _issue(
    check: str,
    message: str,
    *,
    line: int | None = None,
    module: str | None = MANAGED_MODULE,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        **({"line": line} if line is not None else {}),
        "message": message,
        **({"module": module} if module is not None else {}),
        **({"name": name} if name is not None else {}),
    }


def _module_bindings(table: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }


def _unbound_global_references(
    table: symtable.SymbolTable,
    module_bindings: set[str],
) -> set[str]:
    references = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_referenced()
        and symbol.is_global()
        and symbol.get_name() not in module_bindings
    }
    for child in table.get_children():
        references.update(_unbound_global_references(child, module_bindings))
    return references


def _first_load_lines(tree: ast.AST) -> dict[str, int]:
    lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            lines[node.id] = min(lines.get(node.id, node.lineno), node.lineno)
    return lines


def _module_aliases(tree: ast.Module, module_name: str) -> set[str]:
    aliases: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Import):
            continue
        for alias in statement.names:
            if alias.name == module_name:
                aliases.add(alias.asname or alias.name)
    return aliases


def _silent_finish_fallbacks(tree: ast.Module) -> list[dict[str, Any]]:
    """Catch discarded shape operations, not legitimate alternate constructions.

    A handler returning the original receiver (possibly moved) silently erases
    a requested finish. A replacement construction or a re-raised failure is
    deliberately left to the geometric checks and visual review.
    """
    def unchanged(value: ast.AST | None, receiver: str) -> bool:
        if isinstance(value, ast.Name):
            return value.id == receiver
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult):
            transform = value.left
            return (isinstance(transform, ast.Call) and isinstance(transform.func, ast.Name)
                    and transform.func.id in {"Pos", "Rot", "Location"}
                    and unchanged(value.right, receiver))
        # A call such as rebuild(shape) or shape.cut(tool) may construct a
        # genuine alternative. Static name occurrence does not prove identity.
        return False

    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    issues = []
    for attempt in ast.walk(tree):
        if not isinstance(attempt, ast.Try):
            continue
        operations = [
            call for statement in attempt.body for call in ast.walk(statement)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"fillet", "chamfer", "shell", "offset_3d"}
            and isinstance(call.func.value, ast.Name)
        ]
        for handler in attempt.handlers:
            if any(isinstance(node, ast.Raise) for node in ast.walk(handler)):
                continue
            # A real replacement operation in the handler may preserve the
            # target by another construction; do not ban exception recovery.
            if any(
                isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"fillet", "chamfer", "shell", "offset_3d", "extrude", "loft"}
                    or isinstance(node.func, ast.Name)
                    and node.func.id in {"checked_fillet", "checked_chamfer", "extrude", "loft"}
                ) for node in ast.walk(handler)
            ):
                continue
            rebound = {
                node.id for node in ast.walk(handler)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            }
            for operation in operations:
                receiver = operation.func.value.id
                if receiver in rebound:
                    continue
                returns_original = any(isinstance(node, ast.Return) and unchanged(node.value, receiver)
                                       for node in ast.walk(handler))
                # Also recognize the common swallow-and-return spelling, but
                # leave more complex recovery/control flow to runtime checks.
                if all(isinstance(node, ast.Pass) for node in handler.body):
                    parent = parents.get(attempt)
                    for _, siblings in ast.iter_fields(parent) if parent is not None else []:
                        if isinstance(siblings, list) and attempt in siblings:
                            following = siblings[siblings.index(attempt) + 1:]
                            if following and isinstance(following[0], ast.Return):
                                returns_original |= unchanged(following[0].value, receiver)
                if returns_original:
                    issues.append(_issue(
                        "construction-fallback",
                        f"failed {operation.func.attr} returns unchanged input {receiver!r}; "
                        "preserve the requested form with an explicit alternative construction "
                        "or report the operation failure (checked_fillet/checked_chamfer retain diagnostics)",
                        line=handler.lineno, module=None, name=receiver,
                    ))
    return issues


def _uncaught_module_loads(tree: ast.Module) -> dict[str, int]:
    """Find ordinary module reads, excluding intentionally caught NameErrors."""
    class Loads(ast.NodeVisitor):
        def __init__(self):
            self.lines = {}

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self.lines.setdefault(node.id, node.lineno)

        def visit_FunctionDef(self, node):
            # Function globals can be injected by a caller; defaults and
            # decorators are evaluated at module execution time.
            for expression in [*node.decorator_list, *node.args.defaults,
                               *[value for value in node.args.kw_defaults if value is not None]]:
                self.visit(expression)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            for expression in [*node.args.defaults, *[value for value in node.args.kw_defaults if value is not None]]:
                self.visit(expression)

        def visit_Try(self, node):
            catches_missing = any(handler.type is None or any(
                isinstance(kind, ast.Name) and kind.id in {"NameError", "Exception", "BaseException"}
                for kind in ast.walk(handler.type)) for handler in node.handlers)
            if not catches_missing:
                for statement in node.body:
                    self.visit(statement)
            for statement in [*node.handlers, *node.orelse, *node.finalbody]:
                self.visit(statement)

    visitor = Loads()
    visitor.visit(tree)
    return visitor.lines


def validate_source_text(source: str, filename: str = "<source>") -> list[dict[str, Any]]:
    """Return focused syntax and managed-API issues for one Python source."""

    try:
        tree = ast.parse(source, filename)
        symbols = symtable.symtable(source, filename, "exec")
    except SyntaxError as error:
        return [
            _issue(
                "syntax",
                f"invalid Python syntax: {error.msg}",
                line=error.lineno,
                module=None,
            )
        ]

    try:
        public_names = literal_public_names(MANAGED_MODULE)
    except ValueError as error:
        return [_issue("api-catalog", str(error))]

    issues: list[dict[str, Any]] = []
    issues.extend(_silent_finish_fallbacks(tree))
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.ImportFrom)
            or node.level != 0
            or node.module != "authoring"
        ):
            continue
        for alias in node.names:
            if alias.name not in {"write_intent", "*"}:
                continue
            issues.append(
                _issue(
                    "contract-authoring",
                    "CAD build source may not import write_intent; create and validate intent in a separate contract-only authoring step",
                    line=node.lineno,
                    module="authoring",
                    name=alias.name,
                )
            )

    authoring_aliases = _module_aliases(tree, "authoring")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in authoring_aliases
            and node.attr == "write_intent"
        ):
            issues.append(
                _issue(
                    "contract-authoring",
                    "CAD build source may not call authoring.write_intent; create and validate intent in a separate contract-only authoring step",
                    line=node.lineno,
                    module="authoring",
                    name=node.attr,
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "trimesh.repair":
            for alias in node.names:
                if alias.name in FORBIDDEN_MESH_REPAIRS or alias.name == "*":
                    issues.append(
                        _issue(
                            "post-mesh-repair",
                            "CAD build source must construct a watertight volume; post-mesh repair is forbidden",
                            line=node.lineno,
                            module="trimesh.repair",
                            name=alias.name,
                        )
                    )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in FORBIDDEN_MESH_REPAIRS
        ):
            issues.append(
                _issue(
                    "post-mesh-repair",
                    "CAD build source must construct a watertight volume; post-mesh repair is forbidden",
                    line=node.lineno,
                    module="trimesh.repair",
                    name=node.func.attr,
                )
            )

    wildcard_import = False
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.ImportFrom)
            or node.level != 0
            or node.module != MANAGED_MODULE
        ):
            continue
        for alias in node.names:
            if alias.name == "*":
                wildcard_import = True
                issues.append(
                    _issue(
                        "api-symbol",
                        "build123d wildcard imports are unsupported; import every used symbol explicitly",
                        line=node.lineno,
                        name=alias.name,
                    )
                )
            elif alias.name not in public_names:
                issues.append(
                    _issue(
                        "api-symbol",
                        f"build123d does not export {alias.name!r} in the managed runtime",
                        line=node.lineno,
                        name=alias.name,
                    )
                )

    module_aliases = _module_aliases(tree, MANAGED_MODULE)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
            and node.attr not in public_names
        ):
            issues.append(
                _issue(
                    "api-symbol",
                    f"build123d does not export {node.attr!r} in the managed runtime",
                    line=node.lineno,
                    name=node.attr,
                )
            )

    bindings = _module_bindings(symbols)
    if wildcard_import:
        bindings.update(public_names)
    unbound = _unbound_global_references(symbols, bindings)
    load_lines = _first_load_lines(tree)
    for name in sorted(unbound & public_names):
        issues.append(
            _issue(
                "api-binding",
                f"build123d symbol {name!r} is used but is not imported or defined",
                line=load_lines.get(name),
                name=name,
            )
        )

    # Report definite undefined names in the executable module body. Function
    # globals may intentionally be supplied by a caller, and wildcard/dynamic
    # namespaces cannot be resolved safely without executing code.
    opaque_namespace = any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        or isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"exec", "globals", "locals"}
        for node in ast.walk(tree)
    )
    if not opaque_namespace and (
        _module_aliases(tree, MANAGED_MODULE)
        or any(isinstance(node, ast.ImportFrom) and node.module == MANAGED_MODULE for node in tree.body)
    ):
        implicit = set(dir(builtins)) | {"__file__", "__name__", "__package__", "__spec__", "__loader__", "__doc__", "__builtins__", "__cached__", "__annotations__"}
        module_loads = _uncaught_module_loads(tree)
        for symbol in symbols.get_symbols():
            name = symbol.get_name()
            if symbol.is_referenced() and name in module_loads and name not in bindings | implicit | public_names:
                issues.append(_issue(
                    "source-binding", f"name {name!r} is used in the build but is never imported or defined; verify the complete source edit before compiling",
                    line=module_loads[name], module=None, name=name,
                ))

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in issues:
        identity = (item.get("check"), item.get("line"), item.get("name"))
        if identity not in seen:
            unique.append(item)
            seen.add(identity)
    return unique


def audit(source_path: Path) -> dict[str, Any]:
    """Return a serializable source preflight artifact."""

    resolved = source_path.resolve()
    try:
        payload = resolved.read_bytes()
        source = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors = [
            _issue(
                "source-read",
                f"source cannot be read as UTF-8: {error}",
                module=None,
            )
        ]
        digest = None
    else:
        errors = validate_source_text(source, str(resolved))
        digest = sha256(payload).hexdigest()
    return {
        "errors": errors,
        "pass": not errors,
        "schema": PREFLIGHT_SCHEMA,
        "source": {
            "path": str(resolved),
            **({"sha256": digest} if digest is not None else {}),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.source)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
