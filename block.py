"""Explicit field extraction with bounded source evidence."""

import json

from bloxsmith_app.block_api import APPLICATION_JSON, BlockDefinition, BlockRuntimeOutput, BlockRuntimeResult
from . import logic, ui


# FB1 - Extract unique literal text spans and explicit JSON pointers with typed outputs.
# FB2 - Keep exact evidence and suppress data when the target schema fails.
# FB3 - Bound selectors, content, diagnostics and provenance without inference or code evaluation.
# FB4 - Execute with stable port names in both runtimes and supported package origins.
# FB5 - Provide translated responsive modal/inspector drafts and exact JSON settings replacement.
class StructuredExtractBlock(BlockDefinition):
    """Keep selectors and validation policy in configuration, never in incoming documents."""

    kind = "structured_extract"

    def ui_assets(self, surface="modal"):
        """Use the manifest's asset list for bundled surfaces as well as hosted releases."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def execute_runtime(self, context):
        """Emit evidence/issues for rejected extraction, never invalid data."""
        try:
            values = logic.process(context.input_value("content", "1"), context.input_value("sources", "2", default=None), context.config,
                                   context.services.get("cancel_requested", lambda: False))
            outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
                       value=json.dumps(values[port.name], ensure_ascii=False, allow_nan=False),
                       content_type=APPLICATION_JSON) for port in context.output_ports
                       if port.name in values]
            report = {"accepted": values["issues"]["accepted"], "partial": values["issues"]["partial"]}
            return BlockRuntimeResult(status="success", outputs=outputs, content_type=APPLICATION_JSON,
                                      metadata={self.kind: report},
                                      logs=[f"[structured-extract] Extraction checked; accepted={report['accepted']}."])
        except (ValueError, TypeError, RecursionError) as exc:
            detail = str(exc) if isinstance(exc, logic.ExtractError) else "Unable to check the input."
            message = self.translate("block.structured_extract.error", {"detail": detail}, fallback="Extraction failed: {detail}")
            return BlockRuntimeResult(status="failed", outputs=[], error=message, exit_code=1,
                                      logs=[f"[structured-extract-error] {detail}"])

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Refuse unsupported schemas or settings before persisting a form patch."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    return {"error": self.translate("block.structured_extract.error", {"detail": str(exc)}, fallback="Extraction failed: {detail}")}
        result = super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action, values=values, payload=payload)
        return ui.replace_config(result, node)

    def render_modal(self, *, node, payload=None):
        """Render selectors and schema inside an opaque scrollable modal."""
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        """Keep the same fields and controls usable in the narrow inspector."""
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Display the selected mode, preserving the shared port and status shell."""
        config = {**self.default_config(), **(node.get("config") or {})}
        return ui.card(self, node, f"{config['input_format']} · {len(config['fields'])}")
