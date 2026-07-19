"""
Audit Tab — read-only compliance view.

Displays the immutable audit log for the active session.
No analyst actions are taken from this tab — it is entirely read-only.

Filters:
  - Session events only (default) or all recent events across sessions
  - Action type filter (multi-select via text prefix)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.audit import AuditEvent

if TYPE_CHECKING:
    from ui.services import ServiceContainer


# ── Formatting helpers ────────────────────────────────────────────────────────

_ACTION_CATEGORY = {
    # Analyst actions → show in green-ish column value
    "criteria_approved":  "Analyst",
    "steps_approved":     "Analyst",
    "sql_step_approved":  "Analyst",
    "sql_step_rejected":  "Analyst",
    "sql_step_edited":    "Analyst",
    "results_approved":   "Analyst",
    "results_rejected":   "Analyst",
    "cohort_approved":    "Analyst",
    "protocol_uploaded":  "Analyst",
    "session_created":    "Analyst",
    # System actions
    "extraction_started":    "System",
    "extraction_complete":   "System",
    "steps_generated":       "System",
    "sql_generated":         "System",
    "sql_regenerated":       "System",
    "execution_started":     "System",
    "execution_complete":    "System",
    "step_execution_failed": "System",
    "cohort_built":          "System",
}


def format_audit_rows(events: list[AuditEvent]) -> list[list]:
    rows = []
    for e in events:
        ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S") if e.timestamp else "—"
        action_str = str(e.action).replace("_", " ").title()
        category = _ACTION_CATEGORY.get(str(e.action), "System")
        detail_dict = e.detail_as_dict()
        # Render compact detail: show key=value pairs up to 120 chars
        detail_str = "  |  ".join(f"{k}: {v}" for k, v in detail_dict.items())[:120]
        rows.append([
            ts,
            category,
            action_str,
            e.actor,
            e.target_type if e.target_id else "—",
            e.target_id[:16] if e.target_id else "—",
            detail_str or "—",
        ])
    return rows


def audit_summary(events: list[AuditEvent]) -> str:
    if not events:
        return "No audit events found."
    analyst_count = sum(1 for e in events if _ACTION_CATEGORY.get(str(e.action)) == "Analyst")
    system_count = len(events) - analyst_count
    return f"{len(events)} events  •  {analyst_count} analyst actions  •  {system_count} system events"


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Audit Trail tab (read-only)."""

    gr.Markdown("### Audit Trail")
    gr.Markdown(
        "Immutable compliance log — every analyst action and system event for the active session. "
        "Records cannot be modified or deleted."
    )

    with gr.Row():
        load_session_btn = gr.Button("Load Session Audit", variant="primary")
        load_recent_btn = gr.Button("Load All Recent (50)", variant="secondary")
        load_analyst_btn = gr.Button("My Actions", variant="secondary")

    summary_txt = gr.Textbox(
        label="Summary",
        value="—",
        interactive=False,
    )

    audit_table = gr.Dataframe(
        headers=["Timestamp (UTC)", "Category", "Action", "Actor", "Target Type", "Target ID", "Detail"],
        datatype=["str", "str", "str", "str", "str", "str", "str"],
        interactive=False,
        row_count=(10, "dynamic"),
        wrap=True,
    )

    gr.Markdown(
        "_This log is stored in Delta with `delta.appendOnly = true` "
        "— records cannot be updated or deleted by any user or service._"
    )

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _load_session(sid: str) -> tuple:
        if not sid:
            return "No active session.", []
        events = container.audit_service.get_session_history(sid, limit=200)
        return audit_summary(events), format_audit_rows(events)

    def _load_recent(_) -> tuple:
        events = container.audit_service.get_recent(limit=50)
        return audit_summary(events), format_audit_rows(events)

    def _load_analyst(email: str) -> tuple:
        if not email:
            return "Enter your email in the Sessions tab first.", []
        events = container.audit_service.get_by_actor(email, limit=100)
        return audit_summary(events), format_audit_rows(events)

    # ── Event wiring ───────────────────────────────────────────────────────────

    load_session_btn.click(
        fn=_load_session,
        inputs=[session_id],
        outputs=[summary_txt, audit_table],
    )
    load_recent_btn.click(
        fn=_load_recent,
        inputs=[session_id],  # ignored, kept for consistent signature
        outputs=[summary_txt, audit_table],
    )
    load_analyst_btn.click(
        fn=_load_analyst,
        inputs=[analyst_email],
        outputs=[summary_txt, audit_table],
    )
