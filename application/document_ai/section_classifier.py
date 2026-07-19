"""
Section Classifier — uses Databricks ai_classify() to identify section types.

Requires: Databricks Runtime 13.3 LTS or later, Foundation Model API enabled.
Reference: https://docs.databricks.com/en/sql/language-manual/functions/ai_classify.html

ai_classify(content STRING, labels ARRAY<STRING>) → STRING

Classifies each text chunk into one of the section labels. The result drives
which chunks are processed as inclusion vs exclusion criteria.

Parser.py business rule preserved:
  Rule 1 — "Last occurrence wins": when the same section header appears multiple
  times (ToC + body), only the LAST occurrence is used. Applied by deduplicating
  classified sections: if we see INCLUSION_CRITERIA twice, we keep the later one.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.entities.protocol import ExtractedSection, SectionType

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from infrastructure.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_LABELS = [
    "inclusion_criteria",
    "exclusion_criteria",
    "study_design",
    "study_population",
    "background",
    "other",
]

_SECTION_TYPE_MAP = {
    "inclusion_criteria": SectionType.INCLUSION_CRITERIA,
    "exclusion_criteria": SectionType.EXCLUSION_CRITERIA,
    "study_design":       SectionType.STUDY_DESIGN,
    "study_population":   SectionType.STUDY_POPULATION,
}


@dataclass
class ClassifiedChunk:
    chunk_id: int
    text: str
    section_type: str  # raw label from ai_classify
    confidence: float = 1.0


class SectionClassifier:
    """
    Classifies text chunks as clinical protocol sections using ai_classify().

    On Databricks: Spark SQL temp view → ai_classify() call per row.
    Fallback: keyword-based heuristic classifier (local dev / testing).

    Business rule — last-occurrence-wins:
      Applied after classification. If INCLUSION_CRITERIA appears at chunk 2
      (ToC reference) and chunk 15 (actual body), chunk 15 wins.
    """

    def __init__(
        self,
        spark: "SparkSession | None" = None,
        router: "LLMRouter | None" = None,
    ) -> None:
        self._spark = spark
        self._router = router

    def classify(self, chunks: list[str], session_id: str = "tmp") -> list[ExtractedSection]:
        """
        Classify a list of text chunks.
        Returns ExtractedSection objects; only inclusion/exclusion/design/population
        sections are returned (background and other are filtered out).

        Priority:
          1. Databricks ai_classify() — when Spark + DBR available
          2. LLM via Databricks serving — when router available (local dev)
          3. Keyword heuristic — last resort fallback
        """
        if not chunks:
            return []

        if self._spark and self._is_databricks():
            classified = self._classify_with_ai(chunks, session_id)
        elif self._router:
            classified = self._classify_with_llm(chunks)
        else:
            classified = self._classify_heuristic(chunks)

        return self._apply_last_occurrence_rule(classified)

    # ── Databricks ai_classify ─────────────────────────────────────────────────

    def _classify_with_ai(
        self, chunks: list[str], session_id: str
    ) -> list[ClassifiedChunk]:
        """
        Creates a Spark temp view of chunks, then runs ai_classify() over it.

        ai_classify(content, labels) returns the single most-likely label.
        DBR 13.3+, Foundation Model API enabled.
        """
        logger.info("Using ai_classify() for %d chunks", len(chunks))

        # Build temp view
        view_name = f"_doc_chunks_{session_id}"
        rows_sql = ",\n".join(
            f"({idx}, '{_esc(chunk)}')"
            for idx, chunk in enumerate(chunks)
        )
        self._spark.sql(f"""
            CREATE OR REPLACE TEMP VIEW {view_name} AS
            SELECT chunk_id, chunk_text
            FROM (VALUES {rows_sql}) AS t(chunk_id, chunk_text)
        """)

        labels_sql = ", ".join(f"'{label}'" for label in _LABELS)
        rows = self._spark.sql(f"""
            SELECT
                chunk_id,
                chunk_text,
                ai_classify(
                    chunk_text,
                    array({labels_sql})
                ) AS section_type
            FROM {view_name}
        """).collect()

        return [
            ClassifiedChunk(
                chunk_id=r["chunk_id"],
                text=r["chunk_text"],
                section_type=r["section_type"] or "other",
            )
            for r in rows
        ]

    # ── LLM classification (local dev — no Spark, but router available) ──────────

    def _classify_with_llm(self, chunks: list[str]) -> list[ClassifiedChunk]:
        """
        Use Claude via Databricks serving to classify chunks when Spark is absent.
        Sends all chunks in one request to minimise token cost.
        """
        from config.llm_models import LLMTask
        from domain.ports.llm_port import LLMRequest

        logger.info("Using LLM (Databricks serving) for section classification of %d chunks", len(chunks))

        numbered = "\n\n".join(
            f"[CHUNK_{i}]\n{chunk[:600]}"
            for i, chunk in enumerate(chunks)
        )

        system = (
            "You are classifying sections of a clinical study protocol document.\n"
            "For EACH chunk labelled [CHUNK_N], assign exactly ONE label from:\n"
            "  inclusion_criteria, exclusion_criteria, study_design, "
            "study_population, background, other\n\n"
            'Return ONLY a JSON array: [{"chunk_id": 0, "label": "..."}, ...]\n'
            "One entry per chunk, in the same order. No prose."
        )

        request = LLMRequest.with_system(
            system=system,
            user=f"Classify these protocol chunks:\n\n{numbered}",
            model="",
            temperature=0.0,
            max_tokens=1024,
            json_mode=True,
        )

        try:
            result = self._router.route_json(LLMTask.DOCUMENT_CLASSIFICATION, request)
            entries = result if isinstance(result, list) else result.get("classifications", [])
            classified: list[ClassifiedChunk] = []
            for entry in entries:
                idx = int(entry.get("chunk_id", 0))
                label = entry.get("label", "other")
                if idx < len(chunks):
                    classified.append(ClassifiedChunk(
                        chunk_id=idx,
                        text=chunks[idx],
                        section_type=label if label in _LABELS else "other",
                    ))
            # Fill any missing chunk_ids with "other"
            classified_ids = {c.chunk_id for c in classified}
            for i, chunk in enumerate(chunks):
                if i not in classified_ids:
                    classified.append(ClassifiedChunk(chunk_id=i, text=chunk, section_type="other"))
            classified.sort(key=lambda c: c.chunk_id)
            return classified
        except Exception as exc:
            logger.warning("LLM classification failed (%s) — falling back to heuristic", exc)
            return self._classify_heuristic(chunks)

    # ── Heuristic fallback ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_heuristic(chunks: list[str]) -> list[ClassifiedChunk]:
        """
        Keyword-based classifier for local dev / testing without Databricks.
        Mirrors what ai_classify is expected to return.
        """
        result: list[ClassifiedChunk] = []
        for idx, chunk in enumerate(chunks):
            lower = chunk.lower()
            if any(kw in lower for kw in (
                "inclusion criteria", "patients will be included",
                "must meet", "eligible patients", "inclusion:",
            )):
                label = "inclusion_criteria"
            elif any(kw in lower for kw in (
                "exclusion criteria", "patients will be excluded",
                "excluded if", "exclusion:", "not eligible",
            )):
                label = "exclusion_criteria"
            elif any(kw in lower for kw in (
                "study design", "study type", "retrospective", "prospective",
                "cohort", "cross-sectional", "case-control",
            )):
                label = "study_design"
            elif any(kw in lower for kw in (
                "study population", "target population", "patient population",
            )):
                label = "study_population"
            elif any(kw in lower for kw in (
                "background", "introduction", "rationale", "objectives",
            )):
                label = "background"
            else:
                label = "other"
            result.append(ClassifiedChunk(chunk_id=idx, text=chunk, section_type=label))
        return result

    # ── Last-occurrence-wins rule ──────────────────────────────────────────────

    @staticmethod
    def _apply_last_occurrence_rule(
        classified: list[ClassifiedChunk],
    ) -> list[ExtractedSection]:
        """
        Parser.py Rule 1: last occurrence of a section type wins.
        Groups chunks by section_type and keeps only the LAST contiguous run.
        """
        # Build ordered list of (section_type, [chunks]) runs
        runs: list[tuple[str, list[ClassifiedChunk]]] = []
        for chunk in classified:
            if chunk.section_type in ("background", "other"):
                continue
            if runs and runs[-1][0] == chunk.section_type:
                runs[-1][1].append(chunk)
            else:
                runs.append((chunk.section_type, [chunk]))

        # For each section type, keep only the last run (skip ToC references)
        last_runs: dict[str, list[ClassifiedChunk]] = {}
        for stype, chunks in runs:
            last_runs[stype] = chunks  # overwrite → last one wins

        sections: list[ExtractedSection] = []
        for stype, chunks in last_runs.items():
            if stype not in _SECTION_TYPE_MAP:
                continue
            combined = "\n\n".join(c.text for c in chunks)
            sections.append(ExtractedSection(
                section_type=_SECTION_TYPE_MAP[stype],
                text=combined,
                confidence=sum(c.confidence for c in chunks) / len(chunks),
                start_char=0,
                end_char=len(combined),
            ))
        return sections

    @staticmethod
    def _is_databricks() -> bool:
        try:
            import subprocess
            result = subprocess.run(
                ["bash", "-c", "echo $DATABRICKS_RUNTIME_VERSION"],
                capture_output=True, text=True, timeout=2,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False


def _esc(s: str) -> str:
    return s.replace("'", "''").replace("\n", " ").replace("\r", "")
