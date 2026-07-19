"""
Delta implementation of MetadataRepository.

TODAY  — search_columns() uses LIKE keyword matching on Delta tables.
PHASE 12 — swapped for VectorSearchMetadataRepository; interface identical.

The CONCEPT_TO_TABLES mapping encodes clinical domain knowledge:
which Premier tables are relevant for each clinical concept type.
This is stable business logic — update only when the protocol templates change.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from config.databricks import get_databricks_config
from domain.ports.metadata_port import (
    BusinessRule,
    ColumnMetadata,
    MetadataContext,
    MetadataRepository,
    RelationshipMetadata,
    TableMetadata,
)

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


# Clinical concept → Premier table names relevant for that concept
CONCEPT_TO_TABLES: dict[str, list[str]] = {
    "diagnosis_filter":    ["patdemo", "paticd_diag", "icdcode"],
    "procedure_filter":    ["patdemo", "paticd_proc", "patcpt", "icdcode", "cptcode"],
    "date_range":          ["patdemo"],
    "age_filter":          ["patdemo"],
    "gender_filter":       ["patdemo"],
    "encounter_type":      ["patdemo", "pattype"],
    "payer_filter":        ["patdemo", "payor"],
    "hospital_filter":     ["patdemo", "providers", "prov_enrollment"],
    "device_filter":       ["patdemo", "proc_supply", "patcpt", "cptcode"],
    "drug_filter":         ["patdemo", "patbill", "chgmstr"],
    "lab_filter":          ["patdemo", "genlab"],
    "continuous_enrollment": ["patdemo", "prov_enrollment"],
    "lookback_period":     ["patdemo", "paticd_diag"],
    "index_event":         ["patdemo", "paticd_diag", "paticd_proc", "patcpt"],
    "washout_period":      ["patdemo", "paticd_diag", "paticd_proc"],
    "other":               ["patdemo"],
}


class DeltaMetadataRepository(MetadataRepository):
    """
    Reads metadata from ads_automation.metadata.* Delta tables.
    Populated by MetadataIngestor (Phase 4) from the PHD data dictionary Excel.
    """

    def __init__(self, spark: "SparkSession") -> None:
        self._spark = spark
        self._db = get_databricks_config()

    # ── Core lookups ───────────────────────────────────────────────────────────

    def get_table(self, table_name: str) -> TableMetadata | None:
        rows = (
            self._spark.sql(f"""
                SELECT table_id, table_name, description,
                       use_cases, is_addon, grain, primary_join_key
                FROM   {self._db.metadata_tables}
                WHERE  lower(table_name) = lower('{table_name}')
                LIMIT  1
            """)
            .collect()
        )
        if not rows:
            return None
        r = rows[0]
        return TableMetadata(
            table_id=r["table_id"],
            table_name=r["table_name"],
            description=r["description"] or "",
            use_cases=list(r["use_cases"] or []),
            is_addon=bool(r["is_addon"]),
            grain=r["grain"] or "",
            primary_join_key=r["primary_join_key"] or "pat_key",
        )

    def get_columns(self, table_name: str) -> list[ColumnMetadata]:
        rows = (
            self._spark.sql(f"""
                SELECT column_id, table_name, column_name, data_type,
                       description, is_primary_key, is_foreign_key,
                       code_set_type, valid_values, is_nullable
                FROM   {self._db.metadata_columns}
                WHERE  lower(table_name) = lower('{table_name}')
                ORDER BY column_name
            """)
            .collect()
        )
        return [self._row_to_column(r) for r in rows]

    def get_all_tables(self) -> list[TableMetadata]:
        rows = (
            self._spark.sql(f"""
                SELECT table_id, table_name, description,
                       use_cases, is_addon, grain, primary_join_key
                FROM   {self._db.metadata_tables}
                ORDER BY table_name
            """)
            .collect()
        )
        return [
            TableMetadata(
                table_id=r["table_id"],
                table_name=r["table_name"],
                description=r["description"] or "",
                use_cases=list(r["use_cases"] or []),
                is_addon=bool(r["is_addon"]),
                grain=r["grain"] or "",
                primary_join_key=r["primary_join_key"] or "pat_key",
            )
            for r in rows
        ]

    def get_relationships(self, table_name: str) -> list[RelationshipMetadata]:
        rows = (
            self._spark.sql(f"""
                SELECT relationship_id, from_table, from_column,
                       to_table, to_column, join_condition, join_type, notes
                FROM   {self._db.metadata_relationships}
                WHERE  lower(from_table) = lower('{table_name}')
                    OR lower(to_table)   = lower('{table_name}')
            """)
            .collect()
        )
        return [
            RelationshipMetadata(
                relationship_id=r["relationship_id"],
                from_table=r["from_table"],
                from_column=r["from_column"],
                to_table=r["to_table"],
                to_column=r["to_column"],
                join_condition=r["join_condition"],
                join_type=r["join_type"] or "INNER",
                notes=r["notes"] or "",
            )
            for r in rows
        ]

    def get_business_rules(self, category: str | None = None) -> list[BusinessRule]:
        where = f"WHERE rule_category = '{category}'" if category else ""
        rows = (
            self._spark.sql(f"""
                SELECT rule_id, rule_name, rule_category,
                       description, sql_pattern, applicable_tables
                FROM   {self._db.metadata_business_rules}
                {where}
                ORDER BY rule_category, rule_name
            """)
            .collect()
        )
        return [
            BusinessRule(
                rule_id=r["rule_id"],
                rule_name=r["rule_name"],
                rule_category=r["rule_category"] or "",
                description=r["description"] or "",
                sql_pattern=r["sql_pattern"] or "",
                applicable_tables=list(r["applicable_tables"] or []),
            )
            for r in rows
        ]

    # ── Keyword search (swapped by Vector Search in Phase 12) ─────────────────

    def search_columns(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """
        Keyword search on embedding_text (column_name + description + valid_values).
        Phase 12: this method is overridden in VectorSearchMetadataRepository
        to call the Databricks Vector Search index instead.
        """
        safe_query = query.replace("'", "''")
        rows = (
            self._spark.sql(f"""
                SELECT column_id, table_name, column_name, data_type,
                       description, is_primary_key, is_foreign_key,
                       code_set_type, valid_values, is_nullable
                FROM   {self._db.metadata_columns}
                WHERE  lower(embedding_text) LIKE lower('%{safe_query}%')
                LIMIT  {top_k}
            """)
            .collect()
        )
        return [self._row_to_column(r) for r in rows]

    def validate_table_exists(self, table_name: str) -> bool:
        return self.get_table(table_name) is not None

    def validate_column_exists(self, table_name: str, column_name: str) -> bool:
        rows = (
            self._spark.sql(f"""
                SELECT 1 FROM {self._db.metadata_columns}
                WHERE  lower(table_name)  = lower('{table_name}')
                  AND  lower(column_name) = lower('{column_name}')
                LIMIT  1
            """)
            .collect()
        )
        return bool(rows)

    # ── Main context builder ──────────────────────────────────────────────────

    def build_context_for_criterion(
        self,
        criterion_text: str,
        clinical_concept: str,
        top_k_tables: int = 3,
    ) -> MetadataContext:
        """
        Return a MetadataContext ready for injection into a SQL prompt.

        Strategy:
        1. Map clinical_concept → canonical Premier table list (domain knowledge).
        2. Keyword-search `criterion_text` against embedding_text to surface
           additional relevant columns not in the primary table list.
        3. Fetch all relationships between the identified tables.
        4. Fetch applicable business rules for the concept category.
        """
        table_names: list[str] = CONCEPT_TO_TABLES.get(
            clinical_concept, CONCEPT_TO_TABLES["other"]
        )

        # Step 1 — tables by concept
        tables = [t for name in table_names if (t := self.get_table(name)) is not None]

        # Step 2 — columns for those tables
        all_columns: list[ColumnMetadata] = []
        for name in table_names:
            all_columns.extend(self.get_columns(name))

        # Keyword search to augment with any extra columns
        extra = self.search_columns(criterion_text, top_k=top_k_tables * 3)
        seen = {(c.table_name, c.column_name) for c in all_columns}
        for col in extra:
            if (col.table_name, col.column_name) not in seen:
                all_columns.append(col)
                seen.add((col.table_name, col.column_name))

        # Step 3 — relationships
        all_rels: list[RelationshipMetadata] = []
        seen_rels: set[str] = set()
        for name in table_names:
            for rel in self.get_relationships(name):
                if rel.relationship_id not in seen_rels:
                    all_rels.append(rel)
                    seen_rels.add(rel.relationship_id)

        # Step 4 — business rules
        rules = self.get_business_rules(category=clinical_concept)

        db = get_databricks_config()
        return MetadataContext(
            relevant_tables=tables,
            relevant_columns=all_columns,
            join_conditions=all_rels,
            business_rules=rules,
            premier_fqn_prefix=f"{db.premier_catalog}.{db.premier_schema}",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_column(r: object) -> ColumnMetadata:
        return ColumnMetadata(
            column_id=r["column_id"],
            table_name=r["table_name"],
            column_name=r["column_name"],
            data_type=r["data_type"] or "",
            description=r["description"] or "",
            is_primary_key=bool(r["is_primary_key"]),
            is_foreign_key=bool(r["is_foreign_key"]),
            code_set_type=r["code_set_type"],
            valid_values=r["valid_values"],
            is_nullable=bool(r["is_nullable"]) if r["is_nullable"] is not None else True,
        )
