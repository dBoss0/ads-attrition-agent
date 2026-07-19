"""
Steps Tab — STEPS_COMPLETE human-in-the-loop gate.

The analyst reviews the generated attrition steps, verifies ordering,
edits descriptions if needed, then approves to trigger SQL generation.

Gate transition: STEPS_COMPLETE → STEPS_APPROVED → SQL_GENERATING.

TOTAL_POPULATION (step 1) and DEDUPLICATION (last step) are injected
by the system and shown read-only in the step list.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from domain.entities.attrition import AttritionStep, StepType
from domain.entities.audit import AuditAction

if TYPE_CHECKING:
    from ui.services import ServiceContainer

# ── Formatting helpers ────────────────────────────────────────────────────────

_LOCKED_STEPS = {StepType.TOTAL_POPULATION, StepType.DEDUPLICATION}

_STATUS_SYMBOL = {
    "pending":      "⏳",
    "sql_generated": "🔵",
    "sql_approved":  "✅",
    "sql_rejected":  "❌",
    "executed":      "▶️",
    "results_approved": "🎉",
    "results_rejected": "↩️",
}


def format_steps_table(steps: list[AttritionStep]) -> list[list]:
    """Convert AttritionStep objects to display rows."""
    rows = []
    for s in steps:
        locked = "🔒" if s.step_type in _LOCKED_STEPS else ""
        status_sym = _STATUS_SYMBOL.get(s.status, "⏳")
        reduction = (
            f"{s.expected_reduction_pct:.0f}%" if s.expected_reduction_pct else "—"
        )
        rows.append([
            str(s.step_number),
            f"{locked} {s.step_type}".strip(),
            s.description[:120],
            reduction,
            f"{status_sym} {s.status}",
            s.output_view,
        ])
    return rows


def steps_gate_status(steps: list[AttritionStep]) -> str:
    if not steps:
        return "No steps generated yet."
    n = len(steps)
    approved = sum(1 for s in steps if s.status == "sql_approved")
    return f"{n} steps  •  {approved} SQL-approved  •  Approve all steps to generate SQL."


# ── Gradio render ──────────────────────────────────────────────────────────────

def render(
    container: "ServiceContainer",
    session_id: gr.State,
    analyst_email: gr.State,
) -> None:
    """Render the Steps Review tab."""

    gr.Markdown("### Attrition Steps Review  —  Gate 2 of 5")
    gate_status = gr.Textbox(
        label="Gate status",
        value="Generate steps by approving criteria first.",
        interactive=False,
    )

    with gr.Row():
        refresh_btn = gr.Button("Load / Refresh", variant="secondary")
        approve_btn = gr.Button(
            "Approve Steps & Generate SQL",
            variant="primary",
            elem_classes=["btn-approve"],
        )

    steps_table = gr.Dataframe(
        headers=["#", "Step Type", "Description", "Est. Reduction", "Status", "Output View"],
        datatype=["str", "str", "str", "str", "str", "str"],
        interactive=False,
        row_count=(5, "dynamic"),
        wrap=True,
    )

    gr.Markdown(
        "Steps 1 (Total Population) and last (Deduplication) are system-injected and cannot be removed."
    )

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _load(sid: str) -> tuple:
        if not sid:
            return "No active session.", []
        try:
            steps = container.attrition_repo.get_steps(sid)
            return steps_gate_status(steps), format_steps_table(steps)
        except Exception as exc:
            return f"Error: {exc}", []

    def _approve(sid: str, email: str) -> str:
        if not sid:
            gr.Warning("No active session.")
            return "No active session."
        try:
            container.attrition_engine.approve_plan(
                session_id=sid,
                analyst_email=email or "analyst",
                comment="Steps reviewed and approved",
            )
            container.audit_service.record(
                session_id=sid,
                action=AuditAction.STEPS_APPROVED,
                actor=email or "analyst",
            )
            # Trigger SQL generation immediately
            plan = container.attrition_repo.get_plan(sid)
            if plan:
                # Run SQL generation — returns when complete
                # For large plans this can be long; in production this would be async
                # and a progress indicator shown via polling
                from domain.entities.protocol import ParsedProtocol
                container.sql_orchestrator.generate_for_plan(
                    session_id=sid,
                    plan=plan,
                    protocol=ParsedProtocol(),  # protocol criteria already consumed
                    analyst_email=email or "analyst",
                )
            return "Steps approved. SQL generation complete — review SQL in the next tab."
        except Exception as exc:
            gr.Warning(f"Approval failed: {exc}")
            return f"Error: {exc}"

    refresh_btn.click(
        fn=_load,
        inputs=[session_id],
        outputs=[gate_status, steps_table],
    )
    approve_btn.click(
        fn=_approve,
        inputs=[session_id, analyst_email],
        outputs=[gate_status],
    )
