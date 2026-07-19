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
        Classify a list of text chunks into protocol sections.

        Priority:
          1. Databricks ai_classify() — when Spark + DBR runtime available
          2. Marker-based regex (original parser.py logic) — reliable, no AI needed
        """
        if not chunks:
            return []

        if self._spark and self._is_databricks():
            classified = self._classify_with_ai(chunks, session_id)
        else:
            classified = self._classify_by_markers(chunks)

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

    # ── Original parser.py marker logic ───────────────────────────────────────────

    def _classify_by_markers(self, chunks: list[str]) -> list[ClassifiedChunk]:
        """
        Exact replication of parser.py split_criteria_sections() +
        extract_study_selection().

        Joins all chunks into full text, finds 'inclusion criteria' and
        'exclusion criteria' text markers, takes the LAST occurrence of each
        (Rule 1: last-occurrence wins, skips TOC references), then slices the
        text into inc/exc sections.
        """
        full_text = "\n".join(chunks)
        full_text_lower = full_text.lower()

        # Narrow to study section (parser.py: extract_study_selection)
        start_idx = None
        for kw in ["study design", "study population", "inclusion criteria"]:
            match = re.search(kw, full_text_lower)
            if match:
                start_idx = match.start()
                break

        if start_idx is not None:
            end_idx = len(full_text)
            for kw in [
                "exposure variable", "primary independent variable",
                "covariates", "study outcomes", "product codes",
            ]:
                match = re.search(kw, full_text_lower[start_idx:])
                if match:
                    end_idx = start_idx + match.start()
                    break
            study_text = full_text[start_idx:end_idx]
        else:
            study_text = full_text

        study_lower = study_text.lower()

        # Remove table of contents (parser.py: split on "table of contents", keep last)
        if "table of contents" in study_lower:
            study_text = study_text.split("table of contents")[-1]
            study_lower = study_text.lower()

        # Find ALL occurrences → take LAST (Rule 1)
        inc_matches = list(re.finditer(r"inclusion criteria", study_lower))
        exc_matches = list(re.finditer(r"exclusion criteria", study_lower))

        inc_start = inc_matches[-1].start() if inc_matches else -1
        exc_start = exc_matches[-1].start() if exc_matches else -1

        result: list[ClassifiedChunk] = []
        chunk_id = 0

        if inc_start != -1:
            inc_text = study_text[
                inc_start: exc_start if exc_start != -1 else len(study_text)
            ]
            result.append(ClassifiedChunk(
                chunk_id=chunk_id,
                text=inc_text,
                section_type="inclusion_criteria",
            ))
            chunk_id += 1

        if exc_start != -1:
            exc_text = study_text[exc_start:]
            result.append(ClassifiedChunk(
                chunk_id=chunk_id,
                text=exc_text,
                section_type="exclusion_criteria",
            ))

        if not result:
            logger.warning("No inclusion/exclusion markers found — using keyword fallback")
            return self._classify_heuristic(chunks)

        return result

    # ── Keyword fallback (last resort only) ────────────────────────────────────

    @staticmethod
    def _classify_heuristic(chunks: list[str]) -> list[ClassifiedChunk]:
        result: list[ClassifiedChunk] = []
        for idx, chunk in enumerate(chunks):
            lower = chunk.lower()
            if any(kw in lower for kw in ("inclusion", "eligible patients", "must meet")):
                label = "inclusion_criteria"
            elif any(kw in lower for kw in ("exclusion", "excluded if", "not eligible")):
                label = "exclusion_criteria"
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
