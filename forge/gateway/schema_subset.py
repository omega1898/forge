"""Structured-output schemas a small local runtime can actually enforce.

llama.cpp converts only a subset of JSON Schema into a decoding grammar, and
its documentation states that unsupported features are skipped *silently*.
Known gaps (llama.cpp grammars/README.md, checked 2026-11-04): nested $ref,
uniqueItems, not, if/then/else, contains, patternProperties; numeric bounds
apply to integers only; patterns must be anchored with ^ and $.

So FORGE slot schemas are flat and simple, this module refuses schemas that
use features the runtime would drop, and the gateway re-validates every
response with the full Pydantic contract regardless: the constraint layer
is a help, never the guarantee.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

UNSUPPORTED_KEYS = {
    "$ref", "$defs", "definitions", "uniqueItems", "not", "if", "then", "else", "contains",
    "minContains", "maxContains", "patternProperties", "dependentSchemas", "dependentRequired",
    "prefixItems", "$anchor",
}


def unsupported_features(schema: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in UNSUPPORTED_KEYS:
                problems.append(f"{path}: '{key}' is not enforced by llama.cpp grammars")
            if key == "pattern" and isinstance(value, str) and not (value.startswith("^") and value.endswith("$")):
                problems.append(f"{path}: pattern must be anchored with ^ and $")
            if key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum") and schema.get("type") == "number":
                problems.append(f"{path}: numeric bounds on non-integers are not enforced")
            if key in ("anyOf", "oneOf") and "properties" in schema:
                problems.append(f"{path}: properties mixed with {key} is not supported")
            problems.extend(unsupported_features(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            problems.extend(unsupported_features(item, f"{path}[{index}]"))
    return problems


def inline_refs(schema: dict[str, Any], *, max_depth: int = 8) -> dict[str, Any]:
    """Replace local "$ref": "#/$defs/X" with the definition itself.

    Nested objects are fine for grammar conversion when written inline; it is
    the $ref indirection that llama.cpp mishandles. Recursive models cannot
    be inlined and are refused.
    """
    definitions = schema.get("$defs", {})

    def resolve(node: Any, depth: int) -> Any:
        if depth > max_depth:
            raise ValueError("schema nests too deeply or is recursive; slot outputs must be shallow")
        if isinstance(node, dict):
            if "$ref" in node:
                name = str(node["$ref"]).removeprefix("#/$defs/")
                if name not in definitions:
                    raise ValueError(f"unresolvable reference {node['$ref']!r}")
                merged = {**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            return {key: resolve(value, depth) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item, depth) for item in node]
        return node

    return resolve(schema, 0)


def flat_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a slot output model, refusing features the runtime would drop."""
    schema = inline_refs(model.model_json_schema(mode="validation"))
    schema.pop("title", None)
    problems = unsupported_features(schema)
    if problems:
        raise ValueError(f"slot schema for {model.__name__} is not grammar-safe: {problems[0]}")
    return schema
