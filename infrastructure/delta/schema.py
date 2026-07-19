"""
Delta table DDL — idempotent (IF NOT EXISTS everywhere).

Run `SchemaManager(spark).initialize()` once at app startup.
All schemas and tables are created in the `ads_automation` catalog.
Premier tables (rhealth_premier_phg) are READ ONLY — never touched here.

AI Search readiness:
  metadata.columns includes `embedding_text` column — a concatenation of
  column_name + description + valid_values. Phase 12 embeds this field and
  builds the Databricks AI Search index from it. No schema migration needed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class SchemaManager:
    def __init__(self, spark: "SparkSession", catalog: str = "ads_automation") -> None:
        self.spark = spark
        self.catalog = catalog

    # ── Public entry point ────────────────────────────────────────────────────

    def initialize(self) -> None:
        logger.info("Initialising ADS schemas in catalog: %s", self.catalog)
        self._create_schemas()
        self._create_metadata_tables()
        self._create_session_tables()
        self._create_attrition_tables()
        self._create_sql_history_tables()
        self._create_audit_tables()
        logger.info("Schema initialisation complete.")

    # ── Schema creation ────────────────────────────────────────────────────────

    def _create_schemas(self) -> None:
        for schema in ("metadata", "sessions", "attrition", "sql_history", "audit"):
            self.spark.sql(
                f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{schema}"
            )
            logger.debug("Schema ensured: %s.%s", self.catalog, schema)

    # ── Metadata tables ────────────────────────────────────────────────────────

    def _create_metadata_tables(self) -> None:
        c = self.catalog

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.metadata.versions (
                version_id       STRING  NOT NULL,
                source_file_name STRING,
                uploaded_by      STRING,
                upload_timestamp TIMESTAMP,
                tables_count     INT,
                columns_count    INT,
                is_active        BOOLEAN
            )
            USING DELTA
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.metadata.tables (
                table_id           STRING  NOT NULL,
                table_name         STRING  NOT NULL,
                description        STRING,
                use_cases          ARRAY<STRING>,
                is_addon           BOOLEAN,
                grain              STRING,
                primary_join_key   STRING,
                source_version_id  STRING,
                ingested_at        TIMESTAMP
            )
            USING DELTA
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """)

        # embedding_text: concatenation of column_name + description + valid_values
        # Phase 12: embed this field → Vector Search index. No migration needed.
        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.metadata.columns (
                column_id          STRING  NOT NULL,
                table_id           STRING  NOT NULL,
                table_name         STRING  NOT NULL,
                column_name        STRING  NOT NULL,
                data_type          STRING,
                description        STRING,
                is_primary_key     BOOLEAN,
                is_foreign_key     BOOLEAN,
                code_set_type      STRING,
                valid_values       STRING,
                is_nullable        BOOLEAN,
                embedding_text     STRING,
                source_version_id  STRING,
                ingested_at        TIMESTAMP
            )
            USING DELTA
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.metadata.relationships (
                relationship_id   STRING  NOT NULL,
                from_table        STRING  NOT NULL,
                from_column       STRING  NOT NULL,
                to_table          STRING  NOT NULL,
                to_column         STRING  NOT NULL,
                join_condition    STRING  NOT NULL,
                join_type         STRING,
                notes             STRING,
                source_version_id STRING
            )
            USING DELTA
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.metadata.business_rules (
                rule_id            STRING  NOT NULL,
                rule_name          STRING  NOT NULL,
                rule_category      STRING,
                description        STRING,
                sql_pattern        STRING,
                applicable_tables  ARRAY<STRING>,
                created_at         TIMESTAMP
            )
            USING DELTA
        """)

    # ── Session tables ─────────────────────────────────────────────────────────

    def _create_session_tables(self) -> None:
        c = self.catalog

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.sessions.runs (
                session_id     STRING  NOT NULL,
                protocol_name  STRING,
                protocol_id    STRING,
                study_design   STRING,
                data_sources   ARRAY<STRING>,
                status         STRING  NOT NULL,
                analyst_email  STRING,
                created_at     TIMESTAMP,
                updated_at     TIMESTAMP
            )
            USING DELTA
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.sessions.transitions (
                transition_id  STRING  NOT NULL,
                session_id     STRING  NOT NULL,
                from_state     STRING,
                to_state       STRING,
                triggered_by   STRING,
                comment        STRING,
                timestamp      TIMESTAMP
            )
            USING DELTA
        """)

    # ── Attrition tables ───────────────────────────────────────────────────────

    def _create_attrition_tables(self) -> None:
        c = self.catalog

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.attrition.plans (
                plan_id              STRING  NOT NULL,
                session_id           STRING  NOT NULL,
                version              INT,
                generated_by_model   STRING,
                created_at           TIMESTAMP
            )
            USING DELTA
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.attrition.final_cohorts (
                cohort_id                STRING  NOT NULL,
                session_id               STRING  NOT NULL,
                final_sql                STRING,
                attrition_summary_sql    STRING,
                validation_sql           STRING,
                qc_summary_sql           STRING,
                total_initial_count      LONG,
                total_final_count        LONG,
                overall_retention_pct    DOUBLE,
                generated_at             TIMESTAMP,
                approved_by              STRING,
                approved_at              TIMESTAMP
            )
            USING DELTA
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.attrition.steps (
                step_id                  STRING  NOT NULL,
                session_id               STRING  NOT NULL,
                step_number              INT,
                step_type                STRING,
                description              STRING,
                criterion_id             STRING,
                input_view               STRING,
                output_view              STRING,
                business_explanation     STRING,
                sql_text                 STRING,
                qc_sql_text              STRING,
                expected_reduction_pct   DOUBLE,
                dependencies             ARRAY<STRING>,
                status                   STRING,
                sql_version              INT,
                row_count_in             LONG,
                row_count_out            LONG,
                analyst_comment          STRING,
                approved_by              STRING,
                approved_at              TIMESTAMP,
                created_at               TIMESTAMP,
                updated_at               TIMESTAMP
            )
            USING DELTA
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """)

    # ── SQL history tables ─────────────────────────────────────────────────────

    def _create_sql_history_tables(self) -> None:
        c = self.catalog

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.sql_history.versions (
                version_id        STRING  NOT NULL,
                step_id           STRING  NOT NULL,
                version_number    INT,
                sql_text          STRING,
                qc_sql_text       STRING,
                changed_by        STRING,
                change_source     STRING,
                change_reason     STRING,
                generation_model  STRING,
                created_at        TIMESTAMP
            )
            USING DELTA
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.sql_history.results (
                result_id          STRING  NOT NULL,
                step_id            STRING  NOT NULL,
                sql_version_id     STRING,
                row_count          LONG,
                execution_time_ms  INT,
                status             STRING,
                error_message      STRING,
                executed_by        STRING,
                executed_at        TIMESTAMP
            )
            USING DELTA
        """)

        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.sql_history.qc_results (
                qc_result_id             STRING  NOT NULL,
                step_id                  STRING  NOT NULL,
                qc_sql_text              STRING,
                result_summary           STRING,
                passed                   BOOLEAN,
                failure_details          STRING,
                null_check_passed        BOOLEAN,
                duplicate_check_passed   BOOLEAN,
                row_count_reasonable     BOOLEAN,
                executed_at              TIMESTAMP
            )
            USING DELTA
        """)

    # ── Audit tables ───────────────────────────────────────────────────────────

    def _create_audit_tables(self) -> None:
        c = self.catalog

        # Columns match DeltaAuditRepository exactly.
        # TBLPROPERTIES delta.appendOnly = true enforced at storage layer.
        self.spark.sql(f"""
            CREATE TABLE IF NOT EXISTS {c}.audit.log (
                event_id     STRING     NOT NULL,
                session_id   STRING     NOT NULL,
                action       STRING     NOT NULL,
                actor        STRING     NOT NULL,
                target_id    STRING,
                target_type  STRING,
                detail       STRING,
                timestamp    TIMESTAMP  NOT NULL,
                app_version  STRING
            )
            USING DELTA
            TBLPROPERTIES (
                'delta.appendOnly'       = 'true',
                'delta.minReaderVersion' = '1',
                'delta.minWriterVersion' = '2'
            )
            COMMENT 'Immutable audit log. Never UPDATE or DELETE rows.'
        """)
