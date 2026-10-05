"""FB1/FB2/FB3: exact spans, typed JSON pointers, ambiguity and bounded validation."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.structured_extract import logic


def rejected(call):
    """Malformed configuration or content must fail without fabricated values."""
    try:
        call()
    except (ValueError, TypeError):
        return
    raise AssertionError("Invalid extraction accepted")


def main():
    """Use in-memory documents only, without model or network access."""
    text = "Title\nReference:  INV-0042  \nCustomer: Example\n"
    result = logic.process(text, {"document_id": "fixture"}, {})
    assert result["data"] == {"reference": "INV-0042"}
    proof = result["evidence"]["fields"]["reference"]
    assert text[proof["start"]:proof["end"]] == proof["quote"] == "INV-0042"
    assert proof["source"]["document_id"] == "fixture" and not result["evidence"]["semantic_truth_verified"]
    result = logic.process("Reference: A\nReference: A", None, {})
    assert "data" not in result and result["issues"]["items"][0]["code"] == "ambiguous"
    assert "data" not in logic.process("No reference", None, {})
    sections = [{"text": "Intro", "page": 1, "source": {"path": "fixture.pdf"}},
                {"text": "Reference: B", "page": 2, "source": {"path": "fixture.pdf"}}]
    result = logic.process(sections, None, {"input_format": "sections"})
    proof = result["evidence"]["fields"]["reference"]
    assert result["data"] == {"reference": "B"} and proof["reference"]["page"] == 2 and proof["section_index"] == 1
    cfg = {"fields": {"total": {"mode": "between", "start": "Total [", "end": "]", "type": "number"}},
           "schema": {"type": "object", "properties": {"total": {"type": "number", "minimum": 0}}, "required": ["total"]}}
    assert logic.process("Total [12.50]", None, cfg)["data"] == {"total": 12.5}
    assert "data" not in logic.process("Total [12,50]", None, cfg)
    assert "data" not in logic.process("Total [-1]", None, cfg)
    assert "data" not in logic.process("Total [12.5] Total [13]", None, cfg)
    rejected(lambda: logic.process("Total [12]", None, cfg, cancel=lambda: True))
    ptr = {"input_format": "json", "fields": {"reference": {"mode": "pointer", "path": "/a~1b/~0ref", "type": "string"}},
           "schema": {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}}
    result = logic.process('{"a/b":{"~ref":"0042"}}', None, ptr)
    assert result["data"]["reference"] == "0042"
    assert result["evidence"]["fields"]["reference"]["pointer"] == "/a~1b/~0ref"
    assert "data" not in logic.process('{"a/b":{"~ref":42}}', None, ptr)
    null_cfg = {"input_format": "json", "fields": {"value": {"mode": "pointer", "path": "/0", "type": "null"}},
                "schema": {"type": "object", "properties": {"value": {"type": "null"}}, "required": ["value"]}}
    assert logic.process("[null]", None, null_cfg)["data"] == {"value": None}
    assert "data" not in logic.process("[]", None, null_cfg)
    optional = {"schema": {"type": "object", "properties": {"reference": {"type": "string"}}}}
    result = logic.process("Nothing", None, optional)
    assert result["data"] == {} and result["issues"]["partial"]
    limited = logic.process("Nothing", None, {"max_issues": 1})
    assert limited["issues"]["truncated"] and "data" not in limited
    result = logic.process("Reference: long", None, {"max_field_characters": 2})
    assert "data" not in result and result["issues"]["items"][0]["code"] == "field_limit"
    for invalid in ({"input_format": "llm"}, {"schema": {"type": "object", "$ref": "remote"}},
                    {"fields": {"bad": {"mode": "regex", "pattern": ".*", "type": "string"}}},
                    {"max_issues": True}, {"fields": {}}, {"unknown": 1}):
        rejected(lambda: logic.configuration(invalid))
    rejected(lambda: logic.process("x" * 2000, None, {"max_bytes": 1024}))
    rejected(lambda: logic.process("Reference: x", "not JSON", {}))
    rejected(lambda: logic.process([{"text": "x", "page": []}], None, {"input_format": "sections"}))
    assert logic.configuration({"execution": {}, "runtime_path": "x", "runtime_path_label": "X"})["max_issues"] == 100
    print("[ok] Structured Extract literal evidence, ambiguity and schema gate")


if __name__ == "__main__":
    main()
