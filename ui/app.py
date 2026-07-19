"""
ADS Automation — Gradio UI (Phase 9 + Phase 11 startup banner).

Tab layout
----------
1. Sessions      — create / load analyst sessions
2. Upload        — protocol file upload + Document AI extraction
3. Criteria      — Gate 1: review extracted criteria → CRITERIA_APPROVED
4. Steps         — Gate 2: review attrition steps → STEPS_APPROVED → SQL generated
5. SQL Review    — Gate 3: per-step SQL approve / reject / edit → ALL_SQL_APPROVED
6. Results       — Gate 4: execute plan, review row-count waterfall → RESULTS_APPROVED
7. Final Cohort  — Gate 5: review final cohort SQL → COMPLETE
8. Audit Trail   — immutable compliance log (read-only)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from config.settings import get_settings
from ui.auth import extract_analyst_email
from ui.services import ServiceContainer
from ui.theme import build_gradio_theme, CUSTOM_CSS, ACCENT_BLUE
from ui.components import (
    session_tab,
    upload_tab,
    criteria_tab,
    steps_tab,
    sql_tab,
    results_tab,
    cohort_tab,
    audit_tab,
)

if TYPE_CHECKING:
    from application.startup.validator import StartupReport


# ── Startup banner HTML ────────────────────────────────────────────────────────

_STATUS_COLOR = {
    "pass": "#3fb950",   # green
    "warn": "#d29922",   # amber
    "fail": "#f85149",   # red
    "skip": "#8b949e",   # grey
}
_STATUS_ICON = {
    "pass": "✓",
    "warn": "⚠",
    "fail": "✗",
    "skip": "–",
}


def _build_startup_banner(report: "StartupReport | None") -> str:
    if report is None:
        return ""

    if report.passed and not report.warnings:
        # All green — show a compact single-line banner
        return f"""
        <div style="
            background:#0d1117; border:1px solid #3fb950;
            border-radius:6px; padding:8px 16px; margin:8px 0;
            font-size:12px; color:#3fb950;
        ">
            ✓ All startup checks passed — system ready.
        </div>
        """

    rows = "".join(
        f"""<tr>
            <td style="padding:3px 8px; color:{_STATUS_COLOR[str(c.status)]}; font-family:monospace;">
                {_STATUS_ICON[str(c.status)]}
            </td>
            <td style="padding:3px 8px; color:#e6edf3;">{c.name}</td>
            <td style="padding:3px 8px; color:#8b949e;">{c.message}
                {"<br><span style='font-size:11px;color:#6e7681;'>" + c.detail + "</span>" if c.detail else ""}
            </td>
        </tr>"""
        for c in report.checks
    )

    border_color = "#f85149" if not report.passed else "#d29922"
    summary = report.summary_line()

    return f"""
    <div style="
        background:#161b22; border:1px solid {border_color};
        border-radius:6px; padding:12px 16px; margin:8px 0;
    ">
        <div style="font-size:13px; font-weight:600; color:{border_color}; margin-bottom:8px;">
            Startup Status: {summary}
        </div>
        <table style="border-collapse:collapse; font-size:12px; width:100%;">
            {rows}
        </table>
    </div>
    """


# ── App factory ────────────────────────────────────────────────────────────────

def create_app(
    spark=None,
    startup_report: "StartupReport | None" = None,
) -> gr.Blocks:
    """
    Build and return the Gradio Blocks app.

    Parameters
    ----------
    spark:
        Active SparkSession, injected by app.py at startup on Databricks.
        Pass None for local development / testing.
    startup_report:
        Result from StartupInitializer.run(), shown as a banner below the header.
        Pass None to suppress the banner (e.g., in tests).
    """
    settings = get_settings()
    theme = build_gradio_theme()
    container = ServiceContainer(settings=settings, spark=spark)

    with gr.Blocks(
        title=settings.app_name,
        theme=theme,
        css=CUSTOM_CSS,
    ) as demo:

        # ── Header ─────────────────────────────────────────────────────────────
        gr.HTML(f"""
        <div class="ads-header">
            <div>
                <span class="ads-header-title">ADS Automation</span>
                &nbsp;&nbsp;
                <span class="ads-header-badge">Attrition Module</span>
            </div>
            <div style="display:flex; gap:12px; align-items:center;">
                <span class="ads-header-badge">Mu Sigma × J&amp;J MedTech</span>
                <span class="ads-header-badge" style="color:{ACCENT_BLUE};">
                    v{settings.app_version}
                </span>
            </div>
        </div>
        """)

        # ── Startup banner (only shown when report has content) ─────────────────
        banner_html = _build_startup_banner(startup_report)
        if banner_html:
            gr.HTML(banner_html)

        # ── Shared session state ────────────────────────────────────────────────
        session_id_state = gr.State(value="")
        analyst_email_state = gr.State(value="")

        # ── Tabs ───────────────────────────────────────────────────────────────
        with gr.Tabs():

            with gr.Tab("Sessions"):
                session_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Upload Protocol"):
                upload_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Criteria  [Gate 1]"):
                criteria_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Steps  [Gate 2]"):
                steps_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("SQL Review  [Gate 3]"):
                sql_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Results  [Gate 4]"):
                results_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Final Cohort  [Gate 5]"):
                cohort_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

            with gr.Tab("Audit Trail"):
                audit_tab.render(
                    container=container,
                    session_id=session_id_state,
                    analyst_email=analyst_email_state,
                )

        # ── SSO: auto-populate analyst email on page load ─────────────────────────
        # On Databricks Apps the proxy sets X-Forwarded-User on every request.
        # This populates analyst_email_state before the user does anything, so
        # each user sees only their own sessions from the moment the page opens.
        # In local dev the email is empty; the analyst types it in Session tab.
        def _on_load(request: gr.Request):
            return extract_analyst_email(request)

        demo.load(
            fn=_on_load,
            inputs=[],
            outputs=[analyst_email_state],
        )

        # ── Footer ─────────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="
            border-top: 1px solid #30363d;
            padding: 10px 24px;
            font-size: 11px;
            color: #8b949e;
            display: flex;
            justify-content: space-between;
        ">
            <span>ADS Automation — Attrition Module</span>
            <span>Mu Sigma × Johnson &amp; Johnson MedTech  |  Premier PHD v2.2</span>
        </div>
        """)

    return demo
