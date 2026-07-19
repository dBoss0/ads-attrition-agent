# Databricks notebook source
# MAGIC %md
# MAGIC # ADS Automation — Premier PHD Metadata Ingestion
# MAGIC
# MAGIC This notebook ingests the Premier PHD v2.2 data dictionary into the
# MAGIC ADS metadata Delta tables. The app uses this metadata to generate
# MAGIC SQL without hallucinating table or column names.
# MAGIC
# MAGIC **Run after `00_schema_setup.py` and before deploying the app.**
# MAGIC
# MAGIC ## What this does
# MAGIC 1. Reads the Premier PHD v2.2 Excel data dictionary from your Volume
# MAGIC 2. Loads it into `{ADS_CATALOG}.metadata.tables` and `.columns`
# MAGIC 3. Loads the known join relationships and business rules
# MAGIC
# MAGIC ## What you need
# MAGIC - The Premier PHD v2.2 data dictionary Excel file
# MAGIC - Upload it to:
# MAGIC   `/Volumes/__databricks_internal_catalog_tiles_arclight_1258026991313256/pd_70492_5267fb1932774c1d_testing/deepak00/data_dictionary/PHD_V2_Data_Dictionary.xlsx`

# COMMAND ----------

ADS_CATALOG = "__databricks_internal_catalog_tiles_arclight_1258026991313256"
DICT_PATH   = "/Volumes/__databricks_internal_catalog_tiles_arclight_1258026991313256/pd_70492_5267fb1932774c1d_testing/deepak00/data_dictionary/PHD_V2_Data_Dictionary.xlsx"

print(f"Catalog : {ADS_CATALOG}")
print(f"Dict    : {DICT_PATH}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Option A — Use the built-in MetadataIngestor (recommended)
# MAGIC
# MAGIC The app has a full ingestion pipeline in `application/metadata/ingestor.py`.
# MAGIC We call it directly from here.

# COMMAND ----------

import sys
sys.path.insert(0, "/Workspace/path/to/your/app/code")  # adjust to your workspace path

# If the app is deployed, import directly:
from config.settings import get_settings
from infrastructure.delta.schema import SchemaManager
from application.metadata.ingestor import MetadataIngestor

settings = get_settings()
manager  = SchemaManager(spark, settings)

# Verify schemas exist (creates if missing)
manager.ensure_all_schemas()
print("✓ Schemas verified")

# COMMAND ----------

ingestor = MetadataIngestor(spark=spark, settings=settings)

print(f"Ingesting from: {DICT_PATH}")
result = ingestor.ingest_from_excel(DICT_PATH)

print(f"\nIngestion complete:")
print(f"  Tables loaded  : {result.tables_loaded}")
print(f"  Columns loaded : {result.columns_loaded}")
print(f"  Version ID     : {result.version_id}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Option B — Quick manual spot-check (no app code needed)
# MAGIC
# MAGIC Use this if you just want to verify the metadata is present.

# COMMAND ----------

tables_count   = spark.sql(f"SELECT COUNT(*) AS n FROM {ADS_CATALOG}.metadata.tables").collect()[0]["n"]
columns_count  = spark.sql(f"SELECT COUNT(*) AS n FROM {ADS_CATALOG}.metadata.columns").collect()[0]["n"]

print(f"metadata.tables  : {tables_count:,} rows")
print(f"metadata.columns : {columns_count:,} rows")

# Show a sample
display(spark.sql(f"SELECT * FROM {ADS_CATALOG}.metadata.tables LIMIT 10"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Verify key Premier tables are present

# COMMAND ----------

key_tables = [
    "patdemo", "paticd_diag", "paticd_proc", "patcpt",
    "patbill", "pataprdrg", "genlab", "vitals", "mortality"
]

print("Key Premier PHD table check:")
for t in key_tables:
    row = spark.sql(f"""
        SELECT COUNT(*) AS n
        FROM {ADS_CATALOG}.metadata.tables
        WHERE LOWER(table_name) = '{t}'
    """).collect()[0]
    status = "✓" if row["n"] > 0 else "✗ MISSING"
    print(f"  {status}  {t}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Metadata is loaded. The app can now generate Spark SQL using real Premier
# MAGIC table and column names — no hallucination.
# MAGIC
# MAGIC **Next step**: Run `02_test_app_local.py` or deploy via `databricks apps deploy`.
