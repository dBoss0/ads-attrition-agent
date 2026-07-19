"""
SQL Tab — SQL_COMPLETE human-in-the-loop gate.

The analyst reviews the LLM-generated SQL for each attrition step.
Each step can be independently approved, rejected (triggers regeneration),
or edited manually before saving.

Gate transition: SQL_COMPLETE → ALL_SQL_APPROVED (when every step is approved).
The session auto-advances to ALL_SQL_APPROVED when the last step is approved.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.attrition import AttritionStep, StepStatus
from domain.entities.audit import AuditAction, AuditTargetType

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

def format_step_choices(steps: list[AttritionStep]) -> list[str]:
    """Dropdown labels for step selection."""
    return [
        f"Step {s.step_number}: {s.step_type} — {s.description[:60]}"
        for s in steps
    ]


def sql_progress_text(steps: list[AttritionStep]) -> str:
    if not steps:
        return "No steps loaded."
    approved = sum(1 for s in steps if s.status == StepStatus.SQL_APPROVED)
    total = len(steps)
    pct = int(approved / total * 100) if total else 0
    return f"{approved}/{total} steps approved ({pct}%)"


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the SQL Review tab."""

    gr.Markdown("### SQL Review  —  Gate 3 of 5")
    gr.Markdown(
        "Review the LLM-generated Spark SQL for each attrition step. "
        "Approve, reject, or edit each step individually."
    )

    with gr.Row():
        refresh_btn = gr.Button("Load Steps", variant="secondary")
        progress_txt = gr.Textbox(
            label="SQL approval progress",
            value="—",
            interactive=False,
            scale=3,
        )

    step_dropdown = gr.Dropdown(
        label="Select step",
        choices=[],
        interactive=True,
    )

    # Internal state: map of step label → step_id
    step_id_state = gr.State(value={})

    with gr.Tabs():
        with gr.Tab("Main SQL"):
            sql_editor = gr.Code(
                label="Step SQL (editable)",
                language="sql",
                lines=20,
                interactive=True,
            )
        with gr.Tab("QC SQL"):
            qc_sql_box = gr.Code(
                label="QC SQL (read-only)",
                language="sql",
                lines=12,
                interactive=False,
            )

    step_status_txt = gr.Textbox(
        label="Step status",
        value="—",
        interactive=False,
    )

    with gr.Row():
        approve_btn = gr.Button(
            "Approve Step SQL", variant="primary", elem_classes=["btn-approve"]
        )
        reject_btn = gr.Button(
            "Reject & Regenerate", variant="secondary", elem_classes=["btn-reject"]
        )
        save_edit_btn = gr.Button("Save Edited SQL", variant="secondary")

    reject_comment = gr.Textbox(
        label="Rejection reason (shown to SQL generator on retry)",
        placeholder="e.g. Wrong table — should use PATBILL not PATDEMO",
        lines=2,
    )

    action_result = gr.Textbox(label="Action result", interactive=False)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _load_steps(sid: str) -> tuple:
        if not sid:
            return [], {}, "No active session.", "—"
        try:
            steps = container.attrition_repo.get_steps(sid)
            choices = format_step_choices(steps)
            id_map = {choices[i]: steps[i].step_id for i in range(len(steps))}
            progress = sql_progress_text(steps)
            return choices, id_map, gr.update(choices=choices), progress
        except Exception as exc:
            return [], {}, f"Error: {exc}", "—"

    def _select_step(choice: str, id_map: dict, sid: str) -> tuple:
        if not choice or choice not in id_map:
            return "", "", "—"
        step_id = id_map[choice]
        try:
            step = container.attrition_repo.get_step(step_id)
            if step is None:
                return "", "", "Step not found."
            return step.sql_text, step.qc_sql_text, f"{step.status}"
        except Exception as exc:
            return "", "", f"Error: {exc}"

    def _approve(choice: str, id_map: dict, sid: str, email: str) -> tuple:
        if not choice or choice not in id_map:
            gr.Warning("Select a step first.")
            return "No step selected.", "—"
        step_id = id_map[choice]
        try:
            container.sql_orchestrator.approve_step_sql(sid, step_id, email or "analyst")
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.SQL_STEP_APPROVED,
                actor=email or "analyst",
                target_id=step_id,
                target_type=AuditTargetType.STEP,
            )
            steps = container.attrition_repo.get_steps(sid)
            progress = sql_progress_text(steps)
            return f"Step approved.", progress
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Error: {exc}", "—"

    def _reject(choice: str, id_map: dict, sid: str, email: str, comment: str) -> tuple:
        if not choice or choice not in id_map:
            gr.Warning("Select a step first.")
            return "", "", "No step selected.", "—"
        step_id = id_map[choice]
        try:
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.SQL_STEP_REJECTED,
                actor=email or "analyst",
                target_id=step_id,
                target_type=AuditTargetType.STEP,
                detail={"reason": comment or "No reason given"},
            )
            new_version = container.sql_orchestrator.reject_step_sql(
                sid, step_id, email or "analyst", comment or "No reason given"
            )
            step = container.attrition_repo.get_step(step_id)
            steps = container.attrition_repo.get_steps(sid)
            sql = step.sql_text if step else ""
            qc = step.qc_sql_text if step else ""
            return sql, qc, f"Regenerated (version {new_version.version_number}).", sql_progress_text(steps)
        except Exception as exc:
            gr.Warning(str(exc))
            return "", "", f"Error: {exc}", "—"

    def _save_edit(choice: str, id_map: dict, sid: str, edited_sql: str, email: str) -> str:
        if not choice or choice not in id_map:
            gr.Warning("Select a step first.")
            return "No step selected."
        step_id = id_map[choice]
        try:
            container.sql_orchestrator.save_analyst_edit(
                step_id=step_id,
                edited_sql=edited_sql,
                analyst_email=email or "analyst",
                comment="Analyst manual edit",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.SQL_STEP_EDITED,
                actor=email or "analyst",
                target_id=step_id,
                target_type=AuditTargetType.STEP,
            )
            return "Edited SQL saved and validated successfully."
        except ValueError as exc:
            gr.Warning(str(exc))
            return f"Validation error: {exc}"
        except Exception as exc:
            gr.Warning(str(exc))
            return f"Error: {exc}"

    # ── Event wiring ───────────────────────────────────────────────────────────

    refresh_btn.click(
        fn=_load_steps,
        inputs=[session_id],
        outputs=[gr.State(), step_id_state, step_dropdown, progress_txt],
    )
    step_dropdown.change(
        fn=_select_step,
        inputs=[step_dropdown, step_id_state, session_id],
        outputs=[sql_editor, qc_sql_box, step_status_txt],
    )
    approve_btn.click(
        fn=_approve,
        inputs=[step_dropdown, step_id_state, session_id, analyst_email],
        outputs=[action_result, progress_txt],
    )
    reject_btn.click(
        fn=_reject,
        inputs=[step_dropdown, step_id_state, session_id, analyst_email, reject_comment],
        outputs=[sql_editor, qc_sql_box, action_result, progress_txt],
    )
    save_edit_btn.click(
        fn=_save_edit,
        inputs=[step_dropdown, step_id_state, session_id, sql_editor, analyst_email],
        outputs=[action_result],
    )
