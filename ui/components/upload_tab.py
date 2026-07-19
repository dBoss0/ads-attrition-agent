"""
Upload Tab — protocol file upload and Document AI extraction.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import gradio as gr

if TYPE_CHECKING:
    from ui.services import ServiceContainer


def _render_results_html(protocol) -> str:
    if protocol is None:
        return ""

    inc = protocol.active_inclusion or []
    exc = protocol.active_exclusion or []
    sources = protocol.data_sources or []

    # Data source badges
    if sources:
        badges = "".join(
            f'<span style="display:inline-block;background:#1f6feb22;color:#388bfd;'
            f'border:1px solid #1f6feb55;border-radius:20px;padding:3px 12px;'
            f'font-size:12px;font-weight:600;margin:2px;">{s}</span>'
            for s in sources
        )
        ds_html = f'<div style="margin-bottom:6px;">{badges}</div>'
    else:
        ds_html = '<span style="color:#8b949e;font-size:13px;">Not detected — check document for a Data Sources section</span>'

    # Inclusion criteria list
    inc_items = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid #21262d;color:#e6edf3;font-size:13px;">'
        f'<span style="color:#3fb950;font-weight:600;margin-right:8px;">{i}.</span>{c.text}</li>'
        for i, c in enumerate(inc, 1)
    ) or '<li style="color:#8b949e;font-size:13px;">None detected</li>'

    # Exclusion criteria list
    exc_items = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid #21262d;color:#e6edf3;font-size:13px;">'
        f'<span style="color:#f85149;font-weight:600;margin-right:8px;">{i}.</span>{c.text}</li>'
        for i, c in enumerate(exc, 1)
    ) or '<li style="color:#8b949e;font-size:13px;">None detected</li>'

    return f"""
    <div style="font-family:'Inter',system-ui,sans-serif;padding:4px 0;">

      <!-- Summary bar -->
      <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap;">
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                    padding:12px 20px;min-width:120px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#3fb950;">{len(inc)}</div>
          <div style="font-size:11px;color:#8b949e;margin-top:2px;">INCLUSION</div>
        </div>
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                    padding:12px 20px;min-width:120px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#f85149;">{len(exc)}</div>
          <div style="font-size:11px;color:#8b949e;margin-top:2px;">EXCLUSION</div>
        </div>
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                    padding:12px 20px;min-width:120px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#1f6feb;">{len(inc)+len(exc)}</div>
          <div style="font-size:11px;color:#8b949e;margin-top:2px;">TOTAL STEPS</div>
        </div>
      </div>

      <!-- Data source -->
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:14px 16px;margin-bottom:16px;">
        <div style="font-size:11px;font-weight:600;color:#8b949e;
                    letter-spacing:0.08em;margin-bottom:8px;">DATA SOURCE</div>
        {ds_html}
      </div>

      <!-- Two-column criteria -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

        <!-- Inclusion -->
        <div style="background:#161b22;border:1px solid #30363d;border-left:3px solid #3fb950;
                    border-radius:8px;padding:14px 16px;">
          <div style="font-size:11px;font-weight:600;color:#3fb950;
                      letter-spacing:0.08em;margin-bottom:10px;">
            ✓ INCLUSION CRITERIA
          </div>
          <ol style="margin:0;padding-left:0;list-style:none;">
            {inc_items}
          </ol>
        </div>

        <!-- Exclusion -->
        <div style="background:#161b22;border:1px solid #30363d;border-left:3px solid #f85149;
                    border-radius:8px;padding:14px 16px;">
          <div style="font-size:11px;font-weight:600;color:#f85149;
                      letter-spacing:0.08em;margin-bottom:10px;">
            ✗ EXCLUSION CRITERIA
          </div>
          <ol style="margin:0;padding-left:0;list-style:none;">
            {exc_items}
          </ol>
        </div>

      </div>

      <!-- Footer -->
      <div style="margin-top:12px;font-size:11px;color:#484f58;text-align:right;">
        Extracted by: {protocol.extraction_model or 'document-ai'}
        &nbsp;·&nbsp; {protocol.source_filename}
      </div>

    </div>
    """


def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:

    gr.Markdown("### Upload Study Protocol")
    gr.Markdown(
        "Upload the clinical study protocol (DOCX or PDF). "
        "Inclusion and exclusion criteria will be extracted automatically."
    )

    with gr.Row():
        with gr.Column(scale=3):
            file_input = gr.File(
                label="Protocol file (.docx or .pdf)",
                file_types=[".docx", ".pdf"],
                type="binary",
            )
        with gr.Column(scale=1):
            upload_btn = gr.Button("Extract Criteria", variant="primary", size="lg")
            status_label = gr.Textbox(
                label="Status",
                value="Ready",
                interactive=False,
                lines=1,
            )

    results_panel = gr.HTML(value="", label=None)

    # ── Handler ───────────────────────────────────────────────────────────────

    def _extract(file_data, sid: str, email: str):
        if file_data is None:
            gr.Warning("Select a file first.")
            return "No file selected.", ""
        if not sid:
            gr.Warning("Create or load a session first (Sessions tab).")
            return "No active session.", ""

        content = file_data if isinstance(file_data, bytes) else file_data.read()

        # Attempt to recover real filename from Gradio internals
        filename = "protocol.docx"
        if hasattr(file_data, "name"):
            from pathlib import Path
            filename = Path(file_data.name).name or filename

        try:
            protocol = container.document_pipeline.process_upload(
                content=content,
                filename=filename,
                session_id=sid,
                analyst_email=email or "",
            )

            inc = len(protocol.active_inclusion or [])
            exc = len(protocol.active_exclusion or [])
            sources = ", ".join(protocol.data_sources) if protocol.data_sources else "not detected"

            status = f"Done — {inc} inclusion, {exc} exclusion | Source: {sources}"

            try:
                container.session_repo.update_state(
                    sid, "extraction_complete",
                    triggered_by=email or "system",
                    comment="Protocol extracted",
                )
            except Exception:
                pass

            return status, _render_results_html(protocol)

        except Exception as exc:
            gr.Warning(f"Extraction failed: {exc}")
            return f"Error: {exc}", ""

    upload_btn.click(
        fn=_extract,
        inputs=[file_input, session_id, analyst_email],
        outputs=[status_label, results_panel],
    )
