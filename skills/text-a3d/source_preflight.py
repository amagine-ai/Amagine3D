"""Statically validate generated Python against the managed CAD API.

This check deliberately does not import build123d and never executes the
Agent-authored source.  It reads the installed package's literal ``__all__``
declaration so API diagnostics match the pinned runtime without paying the
OCCT import cost.
"""

from __future__ import annotations

import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import symtable
from typing import Any

from capability_manifest import literal_public_names


PREFLIGHT_SCHEMA = "evidence-python-source-preflight/v1"
MANAGED_MODULE = "build123d"


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
