"""FB1/FB2/FB4/FB5: actual graph execution and owned surfaces across runtimes/origins."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.structured_extract.block import StructuredExtractBlock
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import create_run_api, data_edge, display_node, graph_payload, isolated_server, text_node, wait_for_run_terminal


def main():
    """Extract a fixture reference without making a provider call."""
    block = StructuredExtractBlock()
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            with isolated_server() as server:
                node = block.build_node_payload(node_id="extract", position={"x": 360, "y": 140})
                if origin:
                    model = install_test_package(server, "structured_extract", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        html = surface_payload(server, model, node, surface=surface)["html"]
                        assert "{{" not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="fields"' in html and 'data-owned-apply' in html
                document = graph_payload("Explicit extraction", [text_node("source", "Content", "Reference: INV-0042", 80, 140),
                    node, display_node("display", "Data", 700, 140)], [
                    data_edge("source-extract", "source", 1, "extract", 1), data_edge("extract-display", "extract", 1, "display", 1)])
                run = create_run_api(server, document, runtime_mode=mode)
                result = wait_for_run_terminal(server, run["run_id"], timeout_sec=30)
                assert result["status"] == "success", (mode, origin, result.get("logs"))
                assert json.loads(result["output_values"]["extract:1"]["value"]) == {"reference": "INV-0042"}
                assert json.loads(result["output_values"]["extract:2"]["value"])["accepted"] is True
    print("[ok] Structured Extract real runtime and surfaces")


if __name__ == "__main__":
    main()
