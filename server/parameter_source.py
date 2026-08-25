"""Inspect and rewrite explicit parameter() declarations without executing source."""

from __future__ import annotations

import ast
import json
import math
import re
import sys


ALLOWED_KEYWORDS = {
    "affects",
    "group",
    "group_zh",
    "label",
    "label_zh",
    "max_value",
    "min_value",
    "step",
    "unit",
}


def numeric_literal(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        value = node.value
    elif (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
    ):
        operand = node.operand.value
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            raise ValueError("parameter defaults and bounds must be numeric literals")
        value = operand if isinstance(node.op, ast.UAdd) else -operand
    else:
        raise ValueError("parameter defaults and bounds must be numeric literals")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("parameter defaults and bounds must be numeric literals")
    if not math.isfinite(value):
        raise ValueError("parameter defaults and bounds must be finite")
    return value


def string_literal(node: ast.AST, field: str) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise ValueError(f"parameter {field} must be a string literal")
    return node.value


def affects_literal(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise ValueError("parameter affects must be a list or tuple of strings")
    values = [string_literal(item, "affects") for item in node.elts]
    if len(values) != len(set(values)):
        raise ValueError("parameter affects cannot contain duplicate feature IDs")
    return values


def optional_localized_string(node: ast.AST | None) -> str | None:
    """Return usable localized metadata without making it build-critical."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    value = node.value.strip()
    return value or None


def source_offset(source: str, lineno: int, byte_column: int) -> int:
    lines = source.splitlines(keepends=True)
    line = lines[lineno - 1]
    prefix = line.encode("utf-8")[:byte_column].decode("utf-8")
    return sum(len(item) for item in lines[: lineno - 1]) + len(prefix)


def declarations(source: str) -> list[dict]:
    tree = ast.parse(source, filename="model.py")
    result: list[dict] = []
    ids: set[str] = set()
    names: set[str] = set()
    for statement in tree.body:
        name = None
        value_node = None
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            name, value_node = statement.targets[0].id, statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            name, value_node = statement.target.id, statement.value
        if (
            name is None
            or not isinstance(value_node, ast.Call)
            or not isinstance(value_node.func, ast.Name)
            or value_node.func.id != "parameter"
        ):
            continue
        if len(value_node.args) != 2:
            raise ValueError(f"{name}: parameter() requires ID and default positional arguments")
        if any(keyword.arg is None for keyword in value_node.keywords):
            raise ValueError(f"{name}: parameter() does not accept expanded keywords")
        keywords = {keyword.arg: keyword.value for keyword in value_node.keywords}
        unknown = set(keywords) - ALLOWED_KEYWORDS
        if unknown:
            raise ValueError(f"{name}: unknown parameter metadata {sorted(unknown)}")
        required = {"min_value", "max_value", "step"}
        missing = required - set(keywords)
        if missing:
            raise ValueError(f"{name}: missing parameter metadata {sorted(missing)}")

        parameter_id = string_literal(value_node.args[0], "ID")
        default_node = value_node.args[1]
        default = numeric_literal(default_node)
        minimum = numeric_literal(keywords["min_value"])
        maximum = numeric_literal(keywords["max_value"])
        step = numeric_literal(keywords["step"])
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", parameter_id) or parameter_id in ids:
            raise ValueError(f"{name}: parameter IDs must be non-empty and unique")
        if name in names:
            raise ValueError(f"{name}: parameter variable names must be unique")
        if minimum > maximum:
            raise ValueError(f"{name}: min_value cannot exceed max_value")
        if default < minimum or default > maximum:
            raise ValueError(f"{name}: default must be inside its declared bounds")
        if step <= 0:
            raise ValueError(f"{name}: step must be positive")
        kind = "integer" if isinstance(default, int) else "number"
        descriptor = {
            "affects": affects_literal(keywords["affects"])
            if "affects" in keywords
            else [],
            "defaultValue": default,
            "id": parameter_id,
            "kind": kind,
            "label": string_literal(keywords["label"], "label")
            if "label" in keywords
            else name,
            "maximum": maximum,
            "minimum": minimum,
            "name": name,
            "step": step,
            "value": default,
            "_start": source_offset(source, default_node.lineno, default_node.col_offset),
            "_end": source_offset(
                source, default_node.end_lineno, default_node.end_col_offset
            ),
        }
        if "group" in keywords:
            descriptor["group"] = string_literal(keywords["group"], "group")
        group_zh = optional_localized_string(keywords.get("group_zh"))
        if group_zh:
            descriptor["groupZh"] = group_zh
        label_zh = optional_localized_string(keywords.get("label_zh"))
        if label_zh:
            descriptor["labelZh"] = label_zh
        if "unit" in keywords:
            descriptor["unit"] = string_literal(keywords["unit"], "unit")
        result.append(descriptor)
        ids.add(parameter_id)
        names.add(name)
    return result


def public_descriptor(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def validate_value(item: dict, value) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{item['id']}: override must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"{item['id']}: override must be finite")
    if item["kind"] == "integer" and not isinstance(value, int):
        raise ValueError(f"{item['id']}: override must be an integer")
    if value < item["minimum"] or value > item["maximum"]:
        raise ValueError(f"{item['id']}: override is outside its declared bounds")
    quotient = (value - item["minimum"]) / item["step"]
    if not math.isclose(quotient, round(quotient), abs_tol=1e-8):
        raise ValueError(f"{item['id']}: override does not align with its step")
    return value


def rewrite(source: str, values: dict) -> str:
    items = declarations(source)
    by_id = {item["id"]: item for item in items}
    unknown = set(values) - set(by_id)
    if unknown:
        raise ValueError(f"unknown parameter IDs: {sorted(unknown)}")
    replacements = []
    for parameter_id, raw_value in values.items():
        item = by_id[parameter_id]
        value = validate_value(item, raw_value)
        literal = repr(int(value)) if item["kind"] == "integer" else repr(float(value))
        replacements.append((item["_start"], item["_end"], literal))
    rewritten = source
    for start, end, literal in sorted(replacements, reverse=True):
        rewritten = rewritten[:start] + literal + rewritten[end:]
    declarations(rewritten)
    return rewritten


def main() -> int:
    try:
        request = json.load(sys.stdin)
        source = request["source"]
        operation = request.get("operation", "inspect")
        if not isinstance(source, str):
            raise ValueError("source must be a string")
        if operation == "inspect":
            response = {
                "parameters": [public_descriptor(item) for item in declarations(source)]
            }
        elif operation == "rewrite":
            values = request.get("values")
            if not isinstance(values, dict):
                raise ValueError("values must be an object")
            response = {"source": rewrite(source, values)}
        else:
            raise ValueError(f"unsupported operation: {operation}")
        print(json.dumps({"ok": True, **response}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
