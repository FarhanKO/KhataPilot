"""Tidy Pydantic JSON schemas into the plain form tool-calling models handle best."""

from __future__ import annotations

from typing import Any


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop titles, collapse `anyOf [X, null]` to X (optional fields are simply not required) and
    turn Decimal's `anyOf [number, string]` into number. The guard validates the real schema anyway."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        node = {k: v for k, v in node.items() if k != "title"}
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **options[0]}
                return walk(merged)
            types = {o.get("type") for o in options}
            if types == {"number", "string"}:
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                merged["type"] = "number"
                return walk(merged)
        return {key: walk(value) for key, value in node.items()}

    return walk(schema)
