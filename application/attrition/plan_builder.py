"""
PlanBuilder — converts ordered StepSpec list into a wired AttritionPlan.

Responsibilities:
  1. Inject TOTAL_POPULATION as step 1 (always — no criterion maps to it)
  2. Map each StepSpec to an AttritionStep with correct input/output views
  3. Wire linear dependency chain: each step depends on its predecessor
  4. Inject DEDUPLICATION as the final step (always last)
  5. Reassign step_number 1..N across all steps

PlanBuilder is pure Python — no LLM, no Spark, no I/O.
"""
from __future__ import annotations

from application.attrition.step_sequencer import StepSpec
from application.attrition.view_namer import make_view_name
from domain.entities.attrition import AttritionPlan, AttritionStep, StepType
from domain.entities.protocol import ParsedProtocol


class PlanBuilder:
    """
    Assembles an AttritionPlan from sequenced StepSpec objects.

    Usage:
        specs = sequencer.sequence(protocol.all_active_criteria)
        plan = PlanBuilder().build(
            session_id=session.session_id,
            protocol=protocol,
            specs=specs,
            generated_by_model="gpt-5.6",
        )
    """

    def build(
        self,
        session_id: str,
        protocol: ParsedProtocol,
        specs: list[StepSpec],
        generated_by_model: str = "",
    ) -> AttritionPlan:
        """
        Build and return a fully wired AttritionPlan.

        Step numbering is 1-based.  View names follow:
            ads_attrition_{session_id[:8]}_{step_num:02d}_{slug}
        """
        steps: list[AttritionStep] = []

        # ── Step 1: TOTAL_POPULATION — always injected, no criterion ──────────
        total_pop = self._make_step(
            session_id=session_id,
            step_number=1,
            step_type=StepType.TOTAL_POPULATION,
            description="Total population — all encounters in Premier Healthcare Database",
            criterion_id=None,
            input_view="",       # no input; this is the base view
            output_view=make_view_name(session_id, 1, StepType.TOTAL_POPULATION),
            dependencies=[],
        )
        steps.append(total_pop)
        prev_output_view = total_pop.output_view
        prev_step_id = total_pop.step_id

        # ── Criterion-derived steps ────────────────────────────────────────────
        for idx, spec in enumerate(specs, start=2):
            output_view = make_view_name(session_id, idx, spec.step_type)
            step = self._make_step(
                session_id=session_id,
                step_number=idx,
                step_type=spec.step_type,
                description=spec.description,
                criterion_id=spec.criterion_id,
                input_view=prev_output_view,
                output_view=output_view,
                dependencies=[prev_step_id],
                expected_reduction_pct=spec.expected_reduction_pct,
            )
            steps.append(step)
            prev_output_view = output_view
            prev_step_id = step.step_id

        # ── Final step: DEDUPLICATION — always injected, no criterion ─────────
        dedup_num = len(specs) + 2
        dedup = self._make_step(
            session_id=session_id,
            step_number=dedup_num,
            step_type=StepType.DEDUPLICATION,
            description="Deduplication — retain one record per patient (medrec_key) at index date",
            criterion_id=None,
            input_view=prev_output_view,
            output_view=make_view_name(session_id, dedup_num, StepType.DEDUPLICATION),
            dependencies=[prev_step_id],
        )
        steps.append(dedup)

        return AttritionPlan(
            session_id=session_id,
            steps=steps,
            generated_by_model=generated_by_model,
        )

    # ── Factory helper ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_step(
        session_id: str,
        step_number: int,
        step_type: StepType,
        description: str,
        criterion_id: str | None,
        input_view: str,
        output_view: str,
        dependencies: list[str],
        expected_reduction_pct: float | None = None,
    ) -> AttritionStep:
        return AttritionStep(
            session_id=session_id,
            step_number=step_number,
            step_type=step_type,
            description=description,
            criterion_id=criterion_id,
            input_view=input_view,
            output_view=output_view,
            dependencies=dependencies,
            expected_reduction_pct=expected_reduction_pct,
        )
