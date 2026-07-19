# Databricks notebook source
# MAGIC %md
# MAGIC # ADS Automation — Schema & Table Setup
# MAGIC
# MAGIC Run this notebook once in your Databricks workspace to create all the
# MAGIC schemas and Delta tables the application needs.
# MAGIC
# MAGIC **Run this before deploying the app for the first time.**

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Set your catalog name

# COMMAND ----------

# CHANGE THIS to match your ADS_CATALOG env var.
# Your current internal catalog:
ADS_CATALOG = "__databricks_internal_catalog_tiles_arclight_1258026991313256"

# When client provides their catalog, change only this line:
# ADS_CATALOG = "jnj_medtech_ads"

print(f"Using catalog: {ADS_CATALOG}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Create Schemas

# COMMAND ----------

schemas = ["metadata", "sessions", "attrition", "sql_history", "audit"]

for schema in schemas:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ADS_CATALOG}.{schema}")
    print(f"  ✓ {ADS_CATALOG}.{schema}")

print("\nAll schemas ready.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — Create Metadata Tables

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.metadata.tables (
    table_name        STRING NOT NULL,
    schema_name       STRING,
    description       STRING,
    row_count_approx  LONG,
    join_key          STRING,
    is_addon          BOOLEAN,
    addon_note        STRING,
    data_category     STRING,
    ingested_at       TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.metadata.columns (
    table_name    STRING NOT NULL,
    column_name   STRING NOT NULL,
    data_type     STRING,
    description   STRING,
    example_values STRING,
    is_key        BOOLEAN,
    is_nullable   BOOLEAN,
    value_set     STRING,
    ingested_at   TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.metadata.relationships (
    from_table  STRING,
    to_table    STRING,
    join_type   STRING,
    join_keys   STRING,
    notes       STRING,
    ingested_at TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.metadata.business_rules (
    rule_id     STRING NOT NULL,
    rule_name   STRING,
    description STRING,
    sql_snippet STRING,
    applies_to  STRING,
    ingested_at TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.metadata.versions (
    version_id    STRING NOT NULL,
    version_label STRING,
    source_file   STRING,
    row_count     LONG,
    ingested_at   TIMESTAMP
) USING DELTA
""")

print(f"✓ Metadata tables created under {ADS_CATALOG}.metadata")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — Create Session Tables

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.sessions.runs (
    session_id     STRING NOT NULL,
    analyst_email  STRING,
    protocol_name  STRING,
    protocol_path  STRING,
    status         STRING,
    data_source    STRING,
    created_at     TIMESTAMP,
    updated_at     TIMESTAMP,
    metadata_json  STRING
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.sessions.transitions (
    transition_id  STRING NOT NULL,
    session_id     STRING NOT NULL,
    from_state     STRING,
    to_state       STRING,
    actor          STRING,
    reason         STRING,
    transitioned_at TIMESTAMP
) USING DELTA
""")

print(f"✓ Session tables created under {ADS_CATALOG}.sessions")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 5 — Create Attrition Tables

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.attrition.plans (
    plan_id        STRING NOT NULL,
    session_id     STRING NOT NULL,
    data_source    STRING,
    total_steps    INT,
    status         STRING,
    created_at     TIMESTAMP,
    approved_at    TIMESTAMP,
    approved_by    STRING
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.attrition.steps (
    step_id         STRING NOT NULL,
    plan_id         STRING NOT NULL,
    session_id      STRING NOT NULL,
    step_number     INT,
    criterion_text  STRING,
    criterion_type  STRING,
    step_type       STRING,
    sql_approved    BOOLEAN,
    sql_approved_by STRING,
    sql_approved_at TIMESTAMP,
    created_at      TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.attrition.final_cohorts (
    cohort_id               STRING NOT NULL,
    session_id              STRING NOT NULL,
    final_sql               STRING,
    attrition_summary_sql   STRING,
    validation_sql          STRING,
    qc_summary_sql          STRING,
    total_initial_count     LONG,
    total_final_count       LONG,
    overall_retention_pct   DOUBLE,
    generated_at            TIMESTAMP,
    approved_by             STRING,
    approved_at             TIMESTAMP
) USING DELTA
""")

print(f"✓ Attrition tables created under {ADS_CATALOG}.attrition")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 6 — Create SQL History Tables

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.sql_history.versions (
    version_id     STRING NOT NULL,
    step_id        STRING NOT NULL,
    session_id     STRING NOT NULL,
    sql_text       STRING,
    qc_sql_text    STRING,
    version_num    INT,
    generated_by   STRING,
    generated_at   TIMESTAMP,
    analyst_edit   BOOLEAN,
    approved       BOOLEAN,
    approved_by    STRING,
    approved_at    TIMESTAMP
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.sql_history.results (
    result_id      STRING NOT NULL,
    step_id        STRING NOT NULL,
    session_id     STRING NOT NULL,
    row_count      LONG,
    execution_ms   LONG,
    executed_at    TIMESTAMP,
    executed_by    STRING,
    status         STRING,
    error_message  STRING
) USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.sql_history.qc_results (
    qc_id          STRING NOT NULL,
    step_id        STRING NOT NULL,
    session_id     STRING NOT NULL,
    qc_sql_text    STRING,
    qc_row_count   LONG,
    qc_passed      BOOLEAN,
    qc_notes       STRING,
    executed_at    TIMESTAMP
) USING DELTA
""")

print(f"✓ SQL history tables created under {ADS_CATALOG}.sql_history")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 7 — Create Audit Table

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {ADS_CATALOG}.audit.log (
    audit_id    STRING NOT NULL,
    session_id  STRING NOT NULL,
    action      STRING,
    target_type STRING,
    target_id   STRING,
    actor       STRING,
    detail_json STRING,
    created_at  TIMESTAMP
) USING DELTA
""")

print(f"✓ Audit table created under {ADS_CATALOG}.audit")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 8 — Create Volume Subdirectories

# COMMAND ----------

VOLUME_BASE = f"/Volumes/__databricks_internal_catalog_tiles_arclight_1258026991313256/pd_70492_5267fb1932774c1d_testing/deepak00"

subdirs = ["protocols", "data_dictionary", "exports"]

import os
for subdir in subdirs:
    path = f"{VOLUME_BASE}/{subdir}"
    os.makedirs(path, exist_ok=True)
    print(f"  ✓ {path}")

print("\nVolume subdirectories ready.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Verification — List All Created Tables

# COMMAND ----------

for schema in ["metadata", "sessions", "attrition", "sql_history", "audit"]:
    tables = spark.sql(f"SHOW TABLES IN {ADS_CATALOG}.{schema}").collect()
    print(f"\n{ADS_CATALOG}.{schema}:")
    for t in tables:
        print(f"  • {t['tableName']}")

print("\n✅ Schema setup complete. You can now deploy the app.")
