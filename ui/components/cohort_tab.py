"""
Cohort Tab — COHORT_READY human-in-the-loop gate (Gate 5 of 5).

The analyst reviews the final cohort SQL, validation SQL, and QC summary,
then gives final approval. This is the last gate before the session reaches
COMPLETE and the cohort is ready for downstream use.

Gate transition: COHORT_READY → COMPLETE.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.sql_artifact import FinalCohort
from domain.entities.audit import AuditAction, AuditTargetType

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

def format_cohort_summary(cohort: FinalCohort | None) -> str:
    if cohort is None:
        return "No final cohort built yet. Approve results first."
    approved_by = cohort.approved_by or "pending"
    approved_at = (
        cohort.approved_at.strftime("%Y-%m-%d %H:%M")
        if cohort.approved_at
        else "pending"
    )
    return (
        f"Cohort view: {cohort.cohort_view_name}\n"
        f"Steps included: {len(cohort.step_ids)}\n"
        f"Approved by: {approved_by}\n"
        f"Approved at: {approved_at}"
    )


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Final Cohort tab."""

    gr.Markdown("### Final Cohort Review  —  Gate 5 of 5")
    gr.Markdown(
        "This is the last approval gate. Review all four SQL statements below, "
        "then click **Approve Final Cohort** to mark the session COMPLETE."
    )

    with gr.Row():
        refresh_btn = gr.Button("Load Cohort", variant="secondary")
        cohort_summary = gr.Textbox(
            label="Cohort summary",
            value="Load a session with approved results.",
            interactive=False,
            scale=3,
        )

    with gr.Tabs():
        with gr.Tab("Final Cohort SQL"):
            final_sql_box = gr.Code(
                label="Final cohort SELECT (read-only)",
                language="sql",
                lines=14,
                interactive=False,
            )
        with gr.Tab("Attrition Summary SQL"):
            summary_sql_box = gr.Code(
                label="Waterfall summary SELECT",
                language="sql",
                lines=14,
                interactive=False,
            )
        with gr.Tab("Validation SQL"):
            validation_sql_box = gr.Code(
                label="Uniqueness and null-key checks",
                language="sql",
                lines=10,
                interactive=False,
            )
        with gr.Tab("QC Summary SQL"):
            qc_summary_sql_box = gr.Code(
                label="Overall retention and dedup checks",
                language="sql",
                lines=10,
                interactive=False,
            )

    approval_note = gr.Textbox(
        label="Approval note (optional)",
        placeholder="e.g. Row counts verified with clinical lead — cleared for downstream.",
        lines=2,
    )

    with gr.Row():
        approve_btn = gr.Button(
            "Approve Final Cohort",
            variant="primary",
            scale=2,
            elem_classes=["btn-approve"],
        )

    action_result = gr.Textbox(label="Result", interactive=False)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _load(sid: str) -> tuple:
        if not sid:
            return "No active session.", "", "", "", ""
        try:
            cohort = container.attrition_repo.get_final_cohort(sid)
            summary = format_cohort_summary(cohort)
            if cohort is None:
                return summary, "", "", "", ""
            return (
                summary,
                cohort.final_sql or "",
                cohort.attrition_summary_sql or "",
                cohort.validation_sql or "",
                cohort.qc_summary_sql or "",
            )
        except Exception as exc:
            return f"Error: {exc}", "", "", "", ""

    def _approve(sid: str, email: str) -> str:
        if not sid:
            gr.Warning("No active session.")
            return "No active session."
        try:
            container.execution_orchestrator.approve_final_cohort(
                session_id=sid,
                analyst_email=email or "analyst",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.COHORT_APPROVED,
                actor=email or "analyst",
                target_type=AuditTargetType.COHORT,
            )
            return (
                "Final cohort approved. Session is now COMPLETE. "
                "The cohort is ready for downstream analysis."
            )
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Error: {exc}"

    # ── Event wiring ───────────────────────────────────────────────────────────

    refresh_btn.click(
        fn=_load,
        inputs=[session_id],
        outputs=[cohort_summary, final_sql_box, summary_sql_box, validation_sql_box, qc_summary_sql_box],
    )
    approve_btn.click(
        fn=_approve,
        inputs=[session_id, analyst_email],
        outputs=[action_result],
    )
