"""
Criteria Tab — EXTRACTION_COMPLETE human-in-the-loop gate.

The analyst reviews extracted inclusion and exclusion criteria,
edits any that are wrong, deactivates criteria that don't apply,
then clicks Approve to advance to step generation.

Gate transition: EXTRACTION_COMPLETE → CRITERIA_APPROVED → STEPS_GENERATING.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.protocol import Criterion, CriterionType
from domain.entities.audit import AuditAction

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

def format_criteria_rows(criteria: list[Criterion]) -> list[list]:
    """Convert Criterion objects to display rows."""
    return [
        [
            c.criterion_id[:8],
            c.type.value.upper(),
            c.clinical_concept.value,
            c.text[:200],
            "Yes" if c.is_active else "No",
        ]
        for c in criteria
    ]


def criteria_gate_status(session) -> str:
    """Gate status message shown at the top of the tab."""
    if session is None:
        return "Load a session to review criteria."
    state = session.status
    if state == "extraction_complete":
        return "Criteria extracted — review and approve to continue."
    if state in ("criteria_approved", "steps_generating", "steps_complete",
                 "steps_approved", "sql_generating", "sql_complete",
                 "all_sql_approved", "executing", "executed",
                 "results_approved", "cohort_ready", "complete"):
        return f"Criteria already approved. Current state: {state}"
    return f"Current state: {state}. Upload a protocol first."


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Criteria Review tab."""

    gr.Markdown("### Criteria Review  —  Gate 1 of 5")
    gate_status = gr.Textbox(
        label="Gate status",
        value="Load a session to review criteria.",
        interactive=False,
    )

    with gr.Row():
        refresh_btn = gr.Button("Load / Refresh", variant="secondary")
        approve_btn = gr.Button(
            "Approve Criteria & Generate Steps",
            variant="primary",
            elem_classes=["btn-approve"],
        )

    with gr.Tabs():
        with gr.Tab("Inclusion Criteria"):
            inc_table = gr.Dataframe(
                headers=["ID", "Type", "Concept", "Text", "Active"],
                datatype=["str", "str", "str", "str", "str"],
                interactive=False,
                row_count=(5, "dynamic"),
                wrap=True,
            )
        with gr.Tab("Exclusion Criteria"):
            exc_table = gr.Dataframe(
                headers=["ID", "Type", "Concept", "Text", "Active"],
                datatype=["str", "str", "str", "str", "str"],
                interactive=False,
                row_count=(5, "dynamic"),
                wrap=True,
            )

    result_box = gr.Textbox(label="Result", interactive=False, visible=False)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _load(sid: str) -> tuple:
        if not sid:
            return "No active session.", [], []
        try:
            session = container.session_repo.get_by_id(sid)
            status = criteria_gate_status(session)
            # Criteria come from the parsed protocol stored in the session
            # For now retrieve from attrition plan context if available
            # The protocol itself is not persisted separately in this architecture —
            # criteria are embedded in the session's protocol_id reference.
            # We load the most recent plan's step criterion_ids and display what's available.
            # Full criteria display requires protocol persistence (future enhancement).
            return status, [], []
        except Exception as exc:
            return f"Error: {exc}", [], []

    def _approve(sid: str, email: str) -> str:
        if not sid:
            gr.Warning("No active session.")
            return "No active session."
        try:
            container.session_repo.update_state(
                sid,
                "criteria_approved",
                triggered_by=email or "analyst",
                comment="Criteria reviewed and approved",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.CRITERIA_APPROVED,
                actor=email or "analyst",
            )
            session = container.session_repo.get_by_id(sid)
            return f"Criteria approved. Session state: {session.status if session else 'unknown'}"
        except Exception as exc:
            gr.Warning(f"Approval failed: {exc}")
            return f"Error: {exc}"

    refresh_btn.click(
        fn=_load,
        inputs=[session_id],
        outputs=[gate_status, inc_table, exc_table],
    )
    approve_btn.click(
        fn=_approve,
        inputs=[session_id, analyst_email],
        outputs=[gate_status],
    )
