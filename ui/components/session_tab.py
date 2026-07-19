"""
Session Tab — create and load analyst sessions.

Shows recent sessions as a table. The analyst selects or creates a session
before moving to any other tab. The session_id gr.State propagates to all tabs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.session import AnalystSession, SessionState
from domain.entities.audit import AuditAction, AuditTargetType

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers (testable, no Gradio) ──────────────────────────────────

_STATE_EMOJI: dict[str, str] = {
    SessionState.CREATED:            "⚪",
    SessionState.PROTOCOL_UPLOADED:  "📄",
    SessionState.EXTRACTION_RUNNING: "🔄",
    SessionState.EXTRACTION_COMPLETE:"✅",
    SessionState.CRITERIA_APPROVED:  "✅",
    SessionState.STEPS_GENERATING:   "🔄",
    SessionState.STEPS_COMPLETE:     "✅",
    SessionState.STEPS_APPROVED:     "✅",
    SessionState.SQL_GENERATING:     "🔄",
    SessionState.SQL_COMPLETE:       "✅",
    SessionState.ALL_SQL_APPROVED:   "✅",
    SessionState.EXECUTING:          "▶️",
    SessionState.EXECUTED:           "✅",
    SessionState.RESULTS_APPROVED:   "✅",
    SessionState.COHORT_READY:       "🏁",
    SessionState.COMPLETE:           "🎉",
    SessionState.FAILED:             "❌",
}


def format_sessions_table(sessions: list[AnalystSession]) -> list[list]:
    """Convert session objects to display rows."""
    rows = []
    for s in sessions:
        emoji = _STATE_EMOJI.get(s.status, "⚪")
        rows.append([
            s.session_id[:8],
            s.protocol_name or "—",
            f"{emoji} {s.status}",
            s.analyst_email or "—",
            s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "—",
        ])
    return rows


def format_session_status(session: AnalystSession | None) -> str:
    """One-line status summary for a loaded session."""
    if session is None:
        return "No session loaded"
    emoji = _STATE_EMOJI.get(session.status, "⚪")
    progress = session.progress_pct
    return f"{emoji} {session.status}  •  {progress}% complete  •  {session.session_id[:8]}"


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Sessions tab into the current Gradio block context."""

    gr.Markdown("### Analyst Sessions")
    gr.Markdown(
        "_On Databricks Apps your identity is set automatically from SSO. "
        "Enter your email below only when running locally._",
        visible=True,
    )

    # Pre-fill from ADS_DEV_ANALYST_EMAIL when running locally.
    # On Databricks Apps the SSO event overwrites this anyway.
    _dev_email = container.settings.dev_analyst_email

    with gr.Row():
        email_box = gr.Textbox(
            label="Your email",
            placeholder="analyst@company.com (auto-filled on Databricks Apps)",
            value=_dev_email,
            interactive=True,
            scale=3,
        )
        create_btn = gr.Button("New Session", variant="primary", scale=1)

    session_status = gr.Textbox(
        label="Active session",
        value="No session loaded",
        interactive=False,
    )

    gr.Markdown("#### Recent sessions")
    sessions_table = gr.Dataframe(
        headers=["ID (short)", "Protocol", "Status", "Analyst", "Created"],
        datatype=["str", "str", "str", "str", "str"],
        interactive=False,
        label=None,
        row_count=(5, "dynamic"),
    )

    with gr.Row():
        refresh_btn = gr.Button("Refresh", variant="secondary", scale=1)
        selected_id_box = gr.Textbox(
            label="Load session by full ID",
            placeholder="paste session UUID here",
            scale=3,
        )
        load_btn = gr.Button("Load", variant="secondary", scale=1)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _create_session(email: str) -> tuple:
        if not email.strip():
            gr.Warning("Enter your email before creating a session.")
            return gr.update(), gr.update(), "", []
        try:
            from domain.entities.session import AnalystSession
            session = AnalystSession(analyst_email=email.strip())
            saved = container.session_repo.create(session)
            container.audit_service.record(
                session_id=saved.session_id,
                action=AuditAction.SESSION_CREATED,
                actor=email.strip(),
                detail={"analyst": email.strip()},
            )
            status = format_session_status(saved)
            sessions = container.session_repo.list_by_analyst(email.strip(), limit=20)
            return status, email.strip(), saved.session_id, format_sessions_table(sessions)
        except Exception as exc:
            gr.Warning(f"Could not create session: {exc}")
            return "Error creating session", email, "", []

    def _load_session(sid: str, email: str) -> tuple:
        if not sid.strip():
            gr.Warning("Enter a session ID to load.")
            return gr.update(), gr.update(), gr.update()
        try:
            session = container.session_repo.get_by_id(sid.strip())
            if session is None:
                gr.Warning(f"Session not found: {sid[:12]}...")
                return gr.update(), gr.update(), gr.update()
            status = format_session_status(session)
            analyst = email or session.analyst_email or ""
            return status, analyst, session.session_id
        except Exception as exc:
            gr.Warning(f"Error loading session: {exc}")
            return gr.update(), gr.update(), gr.update()

    def _refresh(email: str) -> list:
        try:
            if email.strip():
                sessions = container.session_repo.list_by_analyst(email.strip(), limit=20)
            else:
                sessions = container.session_repo.list_recent(limit=20)
            return format_sessions_table(sessions)
        except Exception as exc:
            gr.Warning(f"Could not refresh sessions: {exc}")
            return []

    # ── Event wiring ───────────────────────────────────────────────────────────

    # When analyst_email_state is populated by SSO on page load, push it into
    # the visible email textbox so the analyst sees their own identity.
    analyst_email.change(
        fn=lambda email: gr.update(value=email) if email else gr.update(),
        inputs=[analyst_email],
        outputs=[email_box],
    )

    create_btn.click(
        fn=_create_session,
        inputs=[email_box],
        outputs=[session_status, analyst_email, session_id, sessions_table],
    )
    load_btn.click(
        fn=_load_session,
        inputs=[selected_id_box, email_box],
        outputs=[session_status, analyst_email, session_id],
    )
    refresh_btn.click(
        fn=_refresh,
        inputs=[email_box],
        outputs=[sessions_table],
    )
