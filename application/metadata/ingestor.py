"""
Metadata Ingestor — PHD V2.2 Excel → Delta tables.

Pipeline:
  1. Parse Excel with PhDExcelParser → ParsedWorkbook
  2. Validate parsed data (row counts, required fields)
  3. Deactivate previous active version in metadata.versions
  4. Write new version record
  5. TRUNCATE + INSERT tables, columns, relationships, business rules
     (full refresh — metadata doesn't grow incrementally)
  6. Return IngestionSummary

This is an application-layer service: depends on ports, not Spark directly.
Called by the Gradio UI admin panel and the CLI seeder script.

Thread safety: holds a single ingestion lock per Spark session.
Concurrent ingestions are rejected (return early with an error).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

from application.metadata.business_rules_seed import get_premier_business_rules
from application.metadata.excel_parser import PhDExcelParser, ParsedWorkbook, safe_open_excel
from application.metadata.relationship_seed import get_premier_relationships
from config.databricks import get_databricks_config

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

_INGEST_LOCK = Lock()


@dataclass
class IngestionSummary:
    version_id: str
    source_file_name: str
    tables_written: int
    columns_written: int
    relationships_written: int
    business_rules_written: int
    warnings: list[str]
    errors: list[str]
    succeeded: bool
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None


class MetadataIngestor:
    """
    Orchestrates the full PHD data dictionary → Delta pipeline.
    Accepts a file path (Volume) or raw bytes (UI upload).
    """

    def __init__(self, spark: "SparkSession") -> None:
        self._spark = spark
        self._db = get_databricks_config()
        self._parser = PhDExcelParser()

    # ── Public entry point ─────────────────────────────────────────────────────

    def ingest_from_path(
        self,
        file_path: str | Path,
        uploaded_by: str = "system",
    ) -> IngestionSummary:
        """
        Ingest a PHD Excel file from a filesystem path (Volume or local).
        Copies to temp before opening to avoid Windows file-lock errors.
        """
        tmp_path = safe_open_excel(file_path)
        try:
            return self._run_ingestion(
                excel_path=tmp_path,
                source_file_name=Path(file_path).name,
                uploaded_by=uploaded_by,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def ingest_from_bytes(
        self,
        content: bytes,
        filename: str,
        uploaded_by: str = "system",
    ) -> IngestionSummary:
        """
        Ingest a PHD Excel file from bytes (Gradio file upload).
        Writes to a temp file, then parses.
        """
        suffix = Path(filename).suffix or ".xlsx"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, prefix="phd_upload_")
        try:
            tmp.write(content)
            tmp.close()
            return self._run_ingestion(
                excel_path=Path(tmp.name),
                source_file_name=filename,
                uploaded_by=uploaded_by,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # ── Core pipeline ──────────────────────────────────────────────────────────

    def _run_ingestion(
        self,
        excel_path: Path,
        source_file_name: str,
        uploaded_by: str,
    ) -> IngestionSummary:
        if not _INGEST_LOCK.acquire(blocking=False):
            return IngestionSummary(
                version_id="",
                source_file_name=source_file_name,
                tables_written=0,
                columns_written=0,
                relationships_written=0,
                business_rules_written=0,
                warnings=[],
                errors=["Another ingestion is already in progress. Please wait and retry."],
                succeeded=False,
                started_at=datetime.now(UTC),
            )

        started_at = datetime.now(UTC)
        version_id = str(uuid4())
        errors: list[str] = []

        try:
            # Step 1: Parse
            logger.info("Parsing PHD Excel: %s", source_file_name)
            parsed = self._parser.parse(excel_path, source_file_name=source_file_name)

            # Step 2: Validate
            validation_errors = self._validate(parsed)
            if validation_errors:
                return IngestionSummary(
                    version_id=version_id,
                    source_file_name=source_file_name,
                    tables_written=0,
                    columns_written=0,
                    relationships_written=0,
                    business_rules_written=0,
                    warnings=parsed.warnings,
                    errors=validation_errors,
                    succeeded=False,
                    started_at=started_at,
                )

            # Step 3: Deactivate previous versions
            self._deactivate_previous_versions()

            # Step 4: Write version record
            self._write_version(
                version_id=version_id,
                source_file_name=source_file_name,
                uploaded_by=uploaded_by,
                tables_count=len(parsed.tables),
                columns_count=len(parsed.columns),
            )

            # Step 5: Write tables
            tables_written = self._write_tables(parsed, version_id)

            # Step 6: Write columns
            columns_written = self._write_columns(parsed, version_id)

            # Step 7: Write relationships — seed + discovered from Excel hyperlinks
            rels = get_premier_relationships()
            rels += _convert_discovered_relationships(parsed.discovered_relationships)
            rels_written = self._write_relationships(rels, version_id)
            if parsed.discovered_relationships:
                logger.info(
                    "Merged %d Excel-discovered relationships into seed",
                    len(parsed.discovered_relationships),
                )

            # Step 8: Write business rules (hardcoded seed)
            rules = get_premier_business_rules()
            rules_written = self._write_business_rules(rules)

            completed_at = datetime.now(UTC)
            duration = (completed_at - started_at).total_seconds()

            logger.info(
                "Ingestion complete — tables=%d cols=%d rels=%d rules=%d (%.1fs)",
                tables_written, columns_written, rels_written, rules_written, duration,
            )
            return IngestionSummary(
                version_id=version_id,
                source_file_name=source_file_name,
                tables_written=tables_written,
                columns_written=columns_written,
                relationships_written=rels_written,
                business_rules_written=rules_written,
                warnings=parsed.warnings,
                errors=[],
                succeeded=True,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as exc:
            logger.exception("Ingestion failed: %s", exc)
            return IngestionSummary(
                version_id=version_id,
                source_file_name=source_file_name,
                tables_written=0,
                columns_written=0,
                relationships_written=0,
                business_rules_written=0,
                warnings=[],
                errors=[str(exc)],
                succeeded=False,
                started_at=started_at,
            )
        finally:
            _INGEST_LOCK.release()

    # ── Validation ─────────────────────────────────────────────────────────────

    @staticmethod
    def _validate(parsed: ParsedWorkbook) -> list[str]:
        errors: list[str] = []
        if not parsed.columns:
            errors.append("No columns parsed — the Excel file may have an unrecognised layout.")
        if not parsed.tables:
            errors.append("No tables parsed.")
        if len(parsed.columns) < 10:
            errors.append(
                f"Only {len(parsed.columns)} columns parsed — expected hundreds. "
                "Check that the correct data dictionary file was uploaded."
            )
        return errors

    # ── Delta write helpers ────────────────────────────────────────────────────

    def _deactivate_previous_versions(self) -> None:
        self._spark.sql(f"""
            UPDATE {self._db.metadata_versions}
            SET    is_active = FALSE
            WHERE  is_active = TRUE
        """)

    def _write_version(
        self,
        version_id: str,
        source_file_name: str,
        uploaded_by: str,
        tables_count: int,
        columns_count: int,
    ) -> None:
        ts = datetime.now(UTC).isoformat()
        src = source_file_name.replace("'", "''")
        by = uploaded_by.replace("'", "''")
        self._spark.sql(f"""
            INSERT INTO {self._db.metadata_versions}
            (version_id, source_file_name, uploaded_by, upload_timestamp,
             tables_count, columns_count, is_active)
            VALUES (
                '{version_id}', '{src}', '{by}',
                TIMESTAMP '{ts}', {tables_count}, {columns_count}, TRUE
            )
        """)

    def _write_tables(self, parsed: ParsedWorkbook, version_id: str) -> int:
        if not parsed.tables:
            return 0

        ts = datetime.now(UTC).isoformat()
        rows: list[str] = []
        for t in parsed.tables:
            tid = str(uuid4())
            name = t.table_name.replace("'", "''")
            desc = t.description.replace("'", "''")
            grain = t.grain.replace("'", "''")
            pjk = t.primary_join_key.replace("'", "''")
            addon = "TRUE" if t.is_addon else "FALSE"
            rows.append(
                f"('{tid}', '{name}', '{desc}', ARRAY(), {addon}, "
                f"'{grain}', '{pjk}', '{version_id}', TIMESTAMP '{ts}')"
            )

        # Full refresh: delete existing rows for this schema then insert
        self._spark.sql(f"DELETE FROM {self._db.metadata_tables}")
        values = ",\n".join(rows)
        self._spark.sql(f"""
            INSERT INTO {self._db.metadata_tables}
            (table_id, table_name, description, use_cases, is_addon,
             grain, primary_join_key, source_version_id, ingested_at)
            VALUES {values}
        """)
        return len(rows)

    def _write_columns(self, parsed: ParsedWorkbook, version_id: str) -> int:
        if not parsed.columns:
            return 0

        # Build a table_name → table_id map
        rows_df = self._spark.sql(
            f"SELECT table_id, table_name FROM {self._db.metadata_tables}"
        ).collect()
        table_id_map = {r["table_name"]: r["table_id"] for r in rows_df}

        ts = datetime.now(UTC).isoformat()

        # Batch insert in chunks to avoid hitting Spark SQL string-length limits
        self._spark.sql(f"DELETE FROM {self._db.metadata_columns}")

        chunk_size = 500
        total = 0
        for i in range(0, len(parsed.columns), chunk_size):
            chunk = parsed.columns[i : i + chunk_size]
            rows: list[str] = []
            for c in chunk:
                cid = str(uuid4())
                tid = table_id_map.get(c.table_name, "")
                tname = c.table_name.replace("'", "''")
                cname = c.column_name.replace("'", "''")
                dtype = c.data_type.replace("'", "''")
                desc = c.description.replace("'", "''")
                valid = c.valid_values.replace("'", "''")
                is_pk = "TRUE" if c.is_primary_key else "FALSE"
                is_fk = "TRUE" if c.is_foreign_key else "FALSE"
                is_null = "TRUE" if c.is_nullable else "FALSE"
                cst = c.code_set_type.replace("'", "''")
                emb = c.embedding_text.replace("'", "''")
                rows.append(
                    f"('{cid}', '{tid}', '{tname}', '{cname}', '{dtype}', "
                    f"'{desc}', {is_pk}, {is_fk}, '{cst}', '{valid}', {is_null}, "
                    f"'{emb}', '{version_id}', TIMESTAMP '{ts}')"
                )
            values = ",\n".join(rows)
            self._spark.sql(f"""
                INSERT INTO {self._db.metadata_columns}
                (column_id, table_id, table_name, column_name, data_type,
                 description, is_primary_key, is_foreign_key, code_set_type,
                 valid_values, is_nullable, embedding_text, source_version_id, ingested_at)
                VALUES {values}
            """)
            total += len(rows)
            logger.debug("Columns chunk %d/%d written", i + chunk_size, len(parsed.columns))

        return total

    def _write_relationships(self, rels: list, version_id: str) -> int:
        self._spark.sql(f"DELETE FROM {self._db.metadata_relationships}")
        if not rels:
            return 0

        rows: list[str] = []
        for r in rels:
            rid = r.relationship_id.replace("'", "''")
            ft = r.from_table.replace("'", "''")
            fc = r.from_column.replace("'", "''")
            tt = r.to_table.replace("'", "''")
            tc = r.to_column.replace("'", "''")
            jc = r.join_condition.replace("'", "''")
            jt = r.join_type.replace("'", "''")
            notes = r.notes.replace("'", "''")
            rows.append(
                f"('{rid}', '{ft}', '{fc}', '{tt}', '{tc}', "
                f"'{jc}', '{jt}', '{notes}', '{version_id}')"
            )

        values = ",\n".join(rows)
        self._spark.sql(f"""
            INSERT INTO {self._db.metadata_relationships}
            (relationship_id, from_table, from_column, to_table, to_column,
             join_condition, join_type, notes, source_version_id)
            VALUES {values}
        """)
        return len(rows)

    def _write_business_rules(self, rules: list) -> int:
        self._spark.sql(f"DELETE FROM {self._db.metadata_business_rules}")
        if not rules:
            return 0

        ts = datetime.now(UTC).isoformat()
        rows: list[str] = []
        for r in rules:
            rid = r.rule_id.replace("'", "''")
            rname = r.rule_name.replace("'", "''")
            cat = r.rule_category.replace("'", "''")
            desc = r.description.replace("'", "''")
            pat = r.sql_pattern.replace("'", "''")
            tables_arr = ", ".join(f"'{t}'" for t in r.applicable_tables)
            rows.append(
                f"('{rid}', '{rname}', '{cat}', '{desc}', '{pat}', "
                f"ARRAY({tables_arr}), TIMESTAMP '{ts}')"
            )

        values = ",\n".join(rows)
        self._spark.sql(f"""
            INSERT INTO {self._db.metadata_business_rules}
            (rule_id, rule_name, rule_category, description, sql_pattern,
             applicable_tables, created_at)
            VALUES {values}
        """)
        return len(rows)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _convert_discovered_relationships(
    discovered: list,
) -> list:
    """
    Convert DiscoveredRelationship objects (from Excel hyperlinks/text) into
    RelationshipMetadata objects that can be merged with the hardcoded seed.
    Skips duplicates that already exist in the seed by relationship_id convention.
    """
    from application.metadata.relationship_seed import get_premier_relationships
    from domain.ports.metadata_port import RelationshipMetadata

    existing_ids = {r.relationship_id for r in get_premier_relationships()}
    result: list[RelationshipMetadata] = []

    for d in discovered:
        rid = f"disc_{d.from_table}_{d.from_column}_{d.to_table}"
        if rid in existing_ids:
            continue
        result.append(RelationshipMetadata(
            relationship_id=rid,
            from_table=d.from_table,
            from_column=d.from_column,
            to_table=d.to_table,
            to_column="",
            join_condition=(
                f"{d.from_table}.{d.from_column} = {d.to_table}.{d.from_column}"
            ),
            join_type="LEFT",
            notes=f"Discovered from Excel {d.evidence}.",
        ))
        existing_ids.add(rid)

    return result
