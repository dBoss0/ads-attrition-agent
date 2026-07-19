"""
Upload Tab — protocol file upload and Document AI extraction.

Accepts DOCX and PDF files. Calls DocumentAIPipeline which runs:
  ai_parse_document → ai_classify → ai_extract → criteria list.

On completion the session advances to EXTRACTION_COMPLETE and the
Criteria tab becomes active for analyst review.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

def format_extraction_summary(protocol) -> str:
    """Human-readable summary of extraction result."""
    if protocol is None:
        return "No protocol loaded."
    inc = len(protocol.active_inclusion)
    exc = len(protocol.active_exclusion)
    src = ", ".join(protocol.data_sources) if protocol.data_sources else "unknown"
    return (
        f"File: {protocol.source_filename}\n"
        f"Inclusion criteria: {inc}\n"
        f"Exclusion criteria: {exc}\n"
        f"Data sources: {src}\n"
        f"Extracted by: {protocol.extraction_model}"
    )


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Upload tab."""

    gr.Markdown("### Upload Study Protocol")
    gr.Markdown(
        "Upload the study protocol (DOCX or PDF). "
        "The system will extract inclusion and exclusion criteria automatically."
    )

    file_input = gr.File(
        label="Protocol file",
        file_types=[".docx", ".pdf"],
        type="binary",
    )
    upload_btn = gr.Button("Extract Criteria", variant="primary")

    with gr.Row():
        status_box = gr.Textbox(
            label="Extraction status",
            value="Upload a file to begin.",
            interactive=False,
            lines=6,
        )

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _extract(file_data, sid: str, email: str) -> str:
        if file_data is None:
            gr.Warning("Select a file first.")
            return "No file selected."
        if not sid:
            gr.Warning("Create or load a session first (Sessions tab).")
            return "No active session."

        # Gradio passes bytes when type="binary"
        content = file_data if isinstance(file_data, bytes) else file_data.read()
        filename = "protocol.docx"  # Gradio file component name not always accessible

        try:
            protocol = container.document_pipeline.process_upload(
                content=content,
                filename=filename,
                session_id=sid,
                analyst_email=email or "",
            )
            # Advance session to EXTRACTION_COMPLETE
            try:
                container.session_repo.update_state(
                    sid,
                    "extraction_complete",
                    triggered_by=email or "system",
                    comment="Protocol extracted via Document AI",
                )
            except Exception:
                pass  # state may already have advanced

            return format_extraction_summary(protocol)
        except Exception as exc:
            gr.Warning(f"Extraction failed: {exc}")
            return f"Error: {exc}"

    upload_btn.click(
        fn=_extract,
        inputs=[file_input, session_id, analyst_email],
        outputs=[status_box],
    )
