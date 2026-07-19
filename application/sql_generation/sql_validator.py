"""
SqlValidator — validates LLM-generated Spark SQL before surfacing it to the analyst.

If validation fails, the SQL generator retries (up to MAX_RETRIES) with the
validation errors injected into the next prompt so the LLM can self-correct.
Bad SQL is NEVER surfaced to the analyst.

Checks (in order, fail-fast):
  1. Starts with CREATE OR REPLACE TEMP VIEW ads_attrition_...
  2. Output view name matches the expected step view name
  3. No SELECT * (production SQL must enumerate columns)
  4. No DML against Premier tables (INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER)
  5. All Premier FQN table references exist in metadata
  6. ICD filter detected → icd_version must also be present
  7. RANK() not used — ROW_NUMBER() only
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.ports.metadata_port import MetadataRepository

# ── Constants ──────────────────────────────────────────────────────────────────

_PREMIER_FQN_PREFIX = "rhealth_premier_phg.bronze_native_premier_phd"
_TEMP_VIEW_PREFIX = "ads_attrition_"

# Matches the FQN prefix followed by a table identifier
_FQN_TABLE_RE = re.compile(
    r"rhealth_premier_phg\.bronze_native_premier_phd\.([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)

# Matches "CREATE OR REPLACE TEMP VIEW <name>"
_CREATE_VIEW_RE = re.compile(
    r"^\s*CREATE\s+OR\s+REPLACE\s+TEMP(?:ORARY)?\s+VIEW\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)

# DML patterns that must not appear targeting Premier tables
_DML_RE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE)\b",
    re.IGNORECASE,
)

# SELECT * check — catches both bare and aliased
_SELECT_STAR_RE = re.compile(r"SELECT\s+\*", re.IGNORECASE)

# ICD detection — column names commonly used for ICD codes
_ICD_COLUMN_RE = re.compile(
    r"\b(icd_code|icd10_code|icd9_code|std_chg_code|proc_cd|diag_cd)\b",
    re.IGNORECASE,
)
_ICD_VERSION_RE = re.compile(r"\bicd_version\b", re.IGNORECASE)

# RANK() — forbidden; only ROW_NUMBER() allowed
_RANK_RE = re.compile(r"\bRANK\s*\(", re.IGNORECASE)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)

    def error_text(self) -> str:
        return "\n".join(f"- {e}" for e in self.errors)


class SqlValidator:
    """
    Validates generated Spark SQL against metadata and style rules.

    Accepts a MetadataRepository for table-existence checks.  If the repo
    is unavailable (e.g. unit tests without Delta), validation skips only
    the metadata-existence check and passes all others.
    """

    def __init__(self, metadata_repo: "MetadataRepository | None" = None) -> None:
        self._repo = metadata_repo

    def validate(self, sql_text: str, expected_output_view: str) -> ValidationResult:
        """
        Run all checks and return a ValidationResult.

        sql_text              — the raw SQL string returned by the LLM
        expected_output_view  — the canonical view name for this step
                                (e.g. ads_attrition_a1b2c3d4_03_age)
        """
        errors: list[str] = []
        sql_stripped = sql_text.strip()

        # 1. Must start with CREATE OR REPLACE TEMP VIEW ads_attrition_...
        match = _CREATE_VIEW_RE.search(sql_stripped)
        if not match:
            errors.append(
                "SQL must start with CREATE OR REPLACE TEMP VIEW ads_attrition_..."
            )
        else:
            actual_view = match.group(1)
            # 2. View name must match expected
            if actual_view.lower() != expected_output_view.lower():
                errors.append(
                    f"Output view mismatch: expected '{expected_output_view}', "
                    f"got '{actual_view}'"
                )
            # 3. View name must use the correct prefix
            if not actual_view.lower().startswith(_TEMP_VIEW_PREFIX):
                errors.append(
                    f"Temp view name must start with '{_TEMP_VIEW_PREFIX}', "
                    f"got '{actual_view}'"
                )

        # 4. No SELECT *
        if _SELECT_STAR_RE.search(sql_stripped):
            errors.append(
                "SELECT * is not allowed in production SQL — enumerate columns explicitly"
            )

        # 5. No DML
        dml_match = _DML_RE.search(sql_stripped)
        if dml_match:
            errors.append(
                f"DML statement '{dml_match.group(0).strip()}' is not permitted — "
                "Premier Healthcare Database is READ ONLY"
            )

        # 6. Premier table existence check (skip if repo unavailable)
        referenced_tables = _FQN_TABLE_RE.findall(sql_stripped)
        if referenced_tables and self._repo is not None:
            for tbl in set(t.lower() for t in referenced_tables):
                if not self._repo.validate_table_exists(tbl):
                    errors.append(
                        f"Table '{_PREMIER_FQN_PREFIX}.{tbl}' not found in metadata — "
                        "check the Premier Data Dictionary"
                    )

        # 7. ICD columns detected → icd_version must be present
        if _ICD_COLUMN_RE.search(sql_stripped) and not _ICD_VERSION_RE.search(sql_stripped):
            errors.append(
                "ICD code column detected but icd_version is missing — "
                "ICD-9 and ICD-10 codes overlap; always include icd_version in the filter"
            )

        # 8. No RANK() — only ROW_NUMBER()
        if _RANK_RE.search(sql_stripped):
            errors.append(
                "RANK() is not allowed — use ROW_NUMBER() for deterministic deduplication"
            )

        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def strip_markdown(self, text: str) -> str:
        """
        Remove markdown code fences the LLM sometimes wraps SQL in.
        Strips ```sql ... ``` and ``` ... ```.
        """
        text = text.strip()
        # Remove opening fence (with optional language tag)
        text = re.sub(r"^```(?:sql)?\s*\n?", "", text, flags=re.IGNORECASE)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)
        return text.strip()
