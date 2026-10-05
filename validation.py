"""Bounded declarative validation without evaluation, remote references or regular expressions."""

from __future__ import annotations

import json
import math


class RuleError(ValueError):
    """Invalid settings or input envelope, distinct from a normal rejected record."""


TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}
KEYWORDS = {"type", "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
            "minLength", "maxLength", "minimum", "maximum", "enum", "const", "title", "description"}
MISSING = object()


def json_value(raw, max_bytes):
    """Decode finite JSON with explicit byte, depth and node bounds."""
    try:
        if isinstance(raw, str):
            if len(raw.encode("utf-8")) > max_bytes:
                raise RuleError("Input exceeds its byte limit.")
            raw = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > max_bytes:
            raise RuleError("Input exceeds its byte limit.")
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, RuleError):
            raise
        raise RuleError("Expected finite JSON data.") from exc
    pending, count = [(raw, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 100000 or depth > 32:
            raise RuleError("JSON exceeds the depth or node limit.")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise RuleError("JSON object keys must be strings.")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return raw


def canonical(value):
    """Compare JSON scalars/collections without Python's bool-equals-number ambiguity."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def check_schema(schema, depth=0):
    """Reject unsupported schema keywords instead of silently accepting weaker checks."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict) or depth > 16:
        raise RuleError("schema must be an object or boolean with depth at most 16.")
    if set(schema) - KEYWORDS:
        raise RuleError("Unsupported schema keyword; see the supported subset in the README.")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or any(not isinstance(t, str) or t not in TYPES for t in types):
        raise RuleError("Unsupported schema type.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(k, str) or not k for k in required):
        raise RuleError("required must list nonempty property names.")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > 256:
        raise RuleError("properties must be an object with at most 256 entries.")
    for child in props.values():
        check_schema(child, depth + 1)
    if "items" in schema:
        check_schema(schema["items"], depth + 1)
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise RuleError("additionalProperties only supports true or false.")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
            raise RuleError(f"{key} must be a nonnegative integer.")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            raise RuleError(f"{key} must be a number.")
    for low, high in (("minimum", "maximum"), ("minItems", "maxItems"), ("minLength", "maxLength")):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise RuleError("Schema minimum exceeds maximum.")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not 1 <= len(schema["enum"]) <= 1000):
        raise RuleError("enum must contain 1 to 1000 values.")


def pointer_parts(path):
    """Decode a JSON Pointer; invalid escape sequences never address another field."""
    if not isinstance(path, str) or len(path) > 2048 or (path and not path.startswith("/")):
        raise RuleError("Rule paths must be JSON Pointers, starting with / (or empty for the root).")
    parts = []
    for part in path.split("/")[1:]:
        for index, char in enumerate(part):
            if char == "~" and (index + 1 == len(part) or part[index + 1] not in "01"):
                raise RuleError("Invalid JSON Pointer escape.")
        parts.append(part.replace("~1", "/").replace("~0", "~"))
    if len(parts) > 32:
        raise RuleError("Rule path is too deep.")
    return parts


def type_matches(value, type_name):
    """Use JSON types and finite numbers; booleans are not integers."""
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {"null": value is None, "boolean": isinstance(value, bool),
            "number": numeric, "integer": numeric and (isinstance(value, int) or value.is_integer()),
            "string": isinstance(value, str), "object": isinstance(value, dict), "array": isinstance(value, list)}[type_name]


def child_path(path, key):
    """Produce an unambiguous JSON Pointer for machine-consumable diagnostics."""
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def schema_issues(value, schema, path=""):
    """Yield bounded-by-input schema violations without embedding sensitive field values."""
    def issue(code, message, at=path):
        return {"path": at, "code": code, "message": message}
    if schema is True:
        return
    if schema is False:
        yield issue("schema_false", "This value is not permitted.")
        return
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if types and not any(type_matches(value, t) for t in types):
        yield issue("type", "Value has an unexpected JSON type.")
        return
    if "enum" in schema and canonical(value) not in {canonical(v) for v in schema["enum"]}:
        yield issue("enum", "Value is outside the allowed set.")
    if "const" in schema and canonical(value) != canonical(schema["const"]):
        yield issue("const", "Value differs from the required constant.")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                yield issue("required", "Required property is missing.", child_path(path, key))
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                yield from schema_issues(child, properties[key], child_path(path, key))
            elif schema.get("additionalProperties", True) is False:
                yield issue("additional_property", "Additional property is not permitted.", child_path(path, key))
    if isinstance(value, (list, str)):
        low, high = ("minItems", "maxItems") if isinstance(value, list) else ("minLength", "maxLength")
        if len(value) < schema.get(low, 0):
            yield issue(low, "Value is shorter than the configured minimum.")
        if high in schema and len(value) > schema[high]:
            yield issue(high, "Value exceeds the configured length.")
        if isinstance(value, list) and "items" in schema:
            for index, child in enumerate(value):
                yield from schema_issues(child, schema["items"], child_path(path, index))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            yield issue("minimum", "Value is below the configured minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            yield issue("maximum", "Value exceeds the configured maximum.")


def at_pointer(value, path):
    """Return a field without conflating missing and explicit null values."""
    for part in pointer_parts(path):
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif isinstance(value, list) and part.isascii() and part.isdigit() and str(int(part)) == part and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value
