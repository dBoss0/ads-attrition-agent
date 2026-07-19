"""
Results Tab — EXECUTED human-in-the-loop gate.

The analyst reviews the row-count waterfall after Spark execution.
Each step shows rows-in, rows-out, and reduction percentage.
Approve advances to COHORT_READY; reject sends back to ALL_SQL_APPROVED.

Gate transition: EXECUTED → RESULTS_APPROVED (approve) or back to ALL_SQL_APPROVED (reject).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.attrition import AttritionStep, StepStatus
from domain.entities.audit import AuditAction

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

def format_waterfall_table(steps: list[AttritionStep]) -> list[list]:
    """Build the attrition waterfall display rows."""
    rows = []
    for s in steps:
        row_in = s.row_count_in if s.row_count_in is not None else "—"
        row_out = s.row_count_out if s.row_count_out is not None else "—"
        if s.row_count_in and s.row_count_out is not None and s.row_count_in > 0:
            reduction = s.row_count_in - s.row_count_out
            pct = reduction / s.row_count_in * 100
            reduction_str = f"{reduction:,} ({pct:.1f}%)"
        else:
            reduction_str = "—"
        status_sym = "✅" if s.status == StepStatus.EXECUTED else "⏳"
        rows.append([
            str(s.step_number),
            s.step_type,
            s.description[:100],
            f"{row_in:,}" if isinstance(row_in, int) else row_in,
            f"{row_out:,}" if isinstance(row_out, int) else row_out,
            reduction_str,
            f"{status_sym} {s.status}",
        ])
    return rows


def results_gate_status(steps: list[AttritionStep]) -> str:
    if not steps:
        return "No execution results yet."
    executed = sum(1 for s in steps if s.row_count_out is not None)
    total = len(steps)
    final = next((s for s in reversed(steps) if s.row_count_out is not None), None)
    final_n = f"{final.row_count_out:,}" if final and final.row_count_out is not None else "?"
    return (
        f"{executed}/{total} steps executed  •  "
        f"Final cohort: {final_n} encounters"
    )


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Results Review tab."""

    gr.Markdown("### Execution Results — Gate 4 of 5")
    gr.Markdown(
        "Review the attrition waterfall below. "
        "Approve if the row counts are clinically plausible; "
        "reject to go back and revise SQL."
    )

    with gr.Row():
        run_btn = gr.Button("Execute Plan", variant="primary", elem_classes=["btn-execute"])
        refresh_btn = gr.Button("Refresh Results", variant="secondary")

    gate_status = gr.Textbox(
        label="Execution status",
        value="Execute the plan to see row counts.",
        interactive=False,
    )

    waterfall_table = gr.Dataframe(
        headers=["#", "Step Type", "Description", "Rows In", "Rows Out", "Reduction", "Status"],
        datatype=["str", "str", "str", "str", "str", "str", "str"],
        interactive=False,
        row_count=(8, "dynamic"),
        wrap=True,
    )

    with gr.Row():
        approve_btn = gr.Button(
            "Approve Results & Build Final Cohort",
            variant="primary",
            elem_classes=["btn-approve"],
        )
        reject_btn = gr.Button(
            "Reject — Revise SQL",
            variant="secondary",
            elem_classes=["btn-reject"],
        )

    rejection_note = gr.Textbox(
        label="Rejection note",
        placeholder="Describe what looks wrong with the row counts",
        lines=2,
    )

    action_result = gr.Textbox(label="Action result", interactive=False)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _execute(sid: str, email: str) -> tuple:
        if not sid:
            gr.Warning("No active session.")
            return "No active session.", []
        try:
            plan = container.attrition_repo.get_plan(sid)
            if plan is None:
                return "No attrition plan found. Approve steps first.", []
            results = container.execution_orchestrator.execute_plan(
                session_id=sid,
                plan=plan,
                analyst_email=email or "analyst",
            )
            steps = container.attrition_repo.get_steps(sid)
            return results_gate_status(steps), format_waterfall_table(steps)
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Execution error: {exc}", []

    def _refresh(sid: str) -> tuple:
        if not sid:
            return "No active session.", []
        try:
            steps = container.attrition_repo.get_steps(sid)
            return results_gate_status(steps), format_waterfall_table(steps)
        except Exception as exc:
            return f"Error: {exc}", []

    def _approve(sid: str, email: str) -> str:
        if not sid:
            gr.Warning("No active session.")
            return "No active session."
        try:
            cohort = container.execution_orchestrator.approve_results(
                session_id=sid,
                analyst_email=email or "analyst",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.RESULTS_APPROVED,
                actor=email or "analyst",
            )
            return (
                f"Results approved. Final cohort built — "
                f"view in the Final Cohort tab."
            )
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Error: {exc}"

    def _reject(sid: str, email: str, note: str) -> str:
        if not sid:
            gr.Warning("No active session.")
            return "No active session."
        try:
            container.execution_orchestrator.reject_results(
                session_id=sid,
                analyst_email=email or "analyst",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.RESULTS_REJECTED,
                actor=email or "analyst",
                detail={"note": note or ""},
            )
            return "Results rejected. Return to SQL Review tab to revise."
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Error: {exc}"

    # ── Event wiring ───────────────────────────────────────────────────────────

    run_btn.click(
        fn=_execute,
        inputs=[session_id, analyst_email],
        outputs=[gate_status, waterfall_table],
    )
    refresh_btn.click(
        fn=_refresh,
        inputs=[session_id],
        outputs=[gate_status, waterfall_table],
    )
    approve_btn.click(
        fn=_approve,
        inputs=[session_id, analyst_email],
        outputs=[action_result],
    )
    reject_btn.click(
        fn=_reject,
        inputs=[session_id, analyst_email, rejection_note],
        outputs=[action_result],
    )
