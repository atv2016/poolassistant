"""Helpers for converting Firestore REST API JSON into plain Python objects."""
from __future__ import annotations
from typing import Any


def parse_value(value: dict[str, Any]) -> Any:
    """Convert a single Firestore 'Value' object into a native Python value."""
    if not value:
        return None
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "mapValue" in value:
        return parse_fields(value["mapValue"].get("fields", {}))
    if "arrayValue" in value:
        return [parse_value(item) for item in value["arrayValue"].get("values", [])]
    # Unknown Firestore value type - surface it rather than silently dropping it.
    return value


def parse_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert a Firestore 'fields' mapping into a plain dict."""
    return {key: parse_value(value) for key, value in fields.items()}
