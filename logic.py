"""Deterministic field extraction with exact source spans and bounded schema validation."""

import hashlib
import itertools
import json
from . import validation as v

DEFAULTS = {"input_format": "text",
            "fields": {"reference": {"mode": "line_prefix", "prefix": "Reference:", "type": "string"}},
            "schema": {"type": "object", "properties": {"reference": {"type": "string", "minLength": 1}},
                       "required": ["reference"], "additionalProperties": False},
            "max_bytes": 2097152, "max_field_characters": 20000, "max_issues": 100}


class ExtractError(ValueError):
    """Invalid extraction settings or content, separate from missing business fields."""


def configuration(raw):
    """Validate literal selectors; no executable expressions, regex or implicit model."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {"execution", "runtime_path", "runtime_path_label"}:
        raise ExtractError("Unknown extraction setting.")
    config = {**DEFAULTS, **{k: value for k, value in raw.items() if k in DEFAULTS}}
    if config["input_format"] not in {"text", "sections", "json"}:
        raise ExtractError("input_format must be text, sections or json.")
    for key, low, high in (("max_bytes", 1024, 4194304), ("max_field_characters", 1, 100000), ("max_issues", 1, 1000)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ExtractError(f"{key} must be an integer between {low} and {high}.")
    fields = v.json_value(config["fields"], 131072)
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 64:
        raise ExtractError("fields must contain 1 to 64 output field definitions.")
    for name, spec in fields.items():
        if not name or len(name) > 128 or not isinstance(spec, dict):
            raise ExtractError("Each output field needs a bounded name and selector object.")
        mode = spec.get("mode")
        keys = {"line_prefix": {"prefix"}, "between": {"start", "end"}, "pointer": {"path"}}
        if mode not in keys or set(spec) != {"mode", "type"} | keys[mode] or spec.get("type") not in v.TYPES:
            raise ExtractError("Selectors require mode, type and only their mode-specific keys.")
        if (mode == "pointer") != (config["input_format"] == "json"):
            raise ExtractError("JSON inputs use pointer selectors; text/sections use literal selectors.")
        if mode == "pointer":
            v.pointer_parts(spec["path"])
        else:
            for key in keys[mode]:
                if not isinstance(spec[key], str) or not 1 <= len(spec[key]) <= 256 or "\x00" in spec[key]:
                    raise ExtractError("Literal delimiters must contain 1 to 256 characters.")
            if mode == "line_prefix" and ("\n" in spec["prefix"] or "\r" in spec["prefix"]):
                raise ExtractError("A line prefix cannot span several lines.")
    schema = v.json_value(config["schema"], 131072)
    v.check_schema(schema)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ExtractError("The target schema must explicitly describe an object.")
    if set(schema.get("properties", {})) - set(fields) or set(schema.get("required", [])) - set(fields):
        raise ExtractError("Every schema property and required field must have an extraction selector.")
    config["fields"], config["schema"] = fields, schema
    return config


def sections(raw, config, sources):
    """Normalize text/section inputs without inventing page numbers or source identity."""
    if config["input_format"] == "text":
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > config["max_bytes"] or "\x00" in raw:
            raise ExtractError("Text input must be a bounded string without NUL characters.")
        return [{"id": "content", "text": raw, "source": sources}]
    value = v.json_value(raw, config["max_bytes"])
    if isinstance(value, dict) and set(value) == {"sections"}:
        value = value["sections"]
    if not isinstance(value, list) or not 1 <= len(value) <= 1000:
        raise ExtractError("Sections input must be a non-empty array, or an object containing only sections.")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) - {"id", "text", "source", "page", "paragraph", "section"} or not isinstance(item.get("text"), str):
            raise ExtractError("Each section accepts text, source and optional id/page/paragraph/section references.")
        if "\x00" in item["text"]:
            raise ExtractError("Sections cannot contain NUL characters.")
        refs = {key: item[key] for key in ("id", "page", "paragraph", "section") if key in item}
        if any(not isinstance(x, (str, int)) or isinstance(x, bool) for x in refs.values()):
            raise ExtractError("Section references must be string or integer identifiers.")
        result.append({"id": str(index), "text": item["text"], "source": item.get("source", sources), "reference": refs})
    return result


def candidates(section, selector):
    """Yield exact stripped spans; repeated candidates remain ambiguous even if equal."""
    text = section["text"]
    if selector["mode"] == "line_prefix":
        offset = 0
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            if body.startswith(selector["prefix"]):
                start, end = offset + len(selector["prefix"]), offset + len(body)
                while start < end and text[start].isspace():
                    start += 1
                while end > start and text[end - 1].isspace():
                    end -= 1
                yield start, end, text[start:end]
            offset += len(line)
    else:
        offset = 0
        while True:
            found = text.find(selector["start"], offset)
            if found < 0:
                return
            start = found + len(selector["start"])
            end = text.find(selector["end"], start)
            if end < 0:
                return
            offset = end + len(selector["end"])
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            yield start, end, text[start:end]


def typed(raw, type_name, *, pointer=False):
    """Convert non-string literal spans using strict finite JSON, not locale guessing."""
    value = raw if pointer or type_name == "string" else v.json_value(raw, 262144)
    if not v.type_matches(value, type_name):
        raise ExtractError("Located value does not match the declared type.")
    return value


def process(raw, source_value, raw_config, cancel=lambda: False):
    """Return evidence/issues always; emit data only when the resulting object validates."""
    config = configuration(raw_config)
    sources = {} if source_value in (None, "") else v.json_value(source_value, 65536)
    if not isinstance(sources, dict):
        raise ExtractError("sources must be an optional JSON object describing provenance.")
    if config["input_format"] == "json":
        document = v.json_value(raw, config["max_bytes"])
        units = None
        digest = hashlib.sha256(v.canonical(document).encode("utf-8")).hexdigest()
    else:
        document = None
        units = sections(raw, config, sources)
        digest = hashlib.sha256(v.canonical(units).encode("utf-8")).hexdigest()
    data, evidence, issues = {}, {}, []
    for name, selector in config["fields"].items():
        if cancel():
            raise ExtractError("Extraction cancelled.")
        pointer = selector["mode"] == "pointer"
        if pointer:
            selected = v.at_pointer(document, selector["path"])
            found = [] if selected is v.MISSING else [(None, None, None, selected)]
        else:
            found = []
            for unit in units:
                for start, end, selected in candidates(unit, selector):
                    found.append((unit, start, end, selected))
                    if len(found) == 2:
                        break
                if len(found) == 2:
                    break
        path = v.child_path("", name)
        if len(found) != 1:
            issues.append({"path": path, "code": "missing" if not found else "ambiguous",
                           "message": "No exact match." if not found else "Several source spans match; no value selected."})
            continue
        unit, start, end, selected = found[0]
        quote = v.canonical(selected) if pointer else selected
        if len(quote) > config["max_field_characters"]:
            issues.append({"path": path, "code": "field_limit", "message": "Located source span exceeds max_field_characters."})
            continue
        proof = {"method": "json_pointer" if pointer else "literal_span", "source": sources if pointer else unit["source"],
                 "quote": quote, "verified_against_input": True}
        if pointer:
            proof["pointer"] = selector["path"]
        else:
            proof.update(section_index=int(unit["id"]) if config["input_format"] == "sections" else 0,
                         start=start, end=end, offset_unit="unicode_code_points", reference=unit.get("reference", {}))
        evidence[name] = proof
        try:
            data[name] = typed(selected, selector["type"], pointer=pointer)
        except (ValueError, TypeError):
            issues.append({"path": path, "code": "type_conversion", "message": "Exact source value does not match the configured JSON type."})
    violations = list(itertools.islice(v.schema_issues(data, config["schema"]), config["max_issues"] + 1))
    accepted = not violations
    issues.extend(violations)
    truncated = len(issues) > config["max_issues"]
    result = {"evidence": {"accepted": accepted, "input_sha256": digest, "engine": "literal", "fields": evidence,
                           "semantic_truth_verified": False},
              "issues": {"accepted": accepted, "partial": len(data) < len(config["fields"]),
                         "truncated": truncated, "items": issues[:config["max_issues"]]}}
    if accepted:
        result["data"] = data
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 4194304:
        raise ExtractError("Combined output exceeds 4 MiB.")
    return result
