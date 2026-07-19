# Databricks notebook source
# MAGIC %md
# MAGIC # ADS Automation — Pre-Deploy Checklist
# MAGIC
# MAGIC Run each cell. Every cell prints ✓ or ✗ with a fix instruction.
# MAGIC All cells must show ✓ before you deploy the app.

# COMMAND ----------

ADS_CATALOG = "__databricks_internal_catalog_tiles_arclight_1258026991313256"
VOLUME_BASE = f"/Volumes/__databricks_internal_catalog_tiles_arclight_1258026991313256/pd_70492_5267fb1932774c1d_testing/deepak00"
SECRETS_SCOPE = "ads-secrets"

# COMMAND ----------
# MAGIC %md ### Check 1 — Catalog accessible

# COMMAND ----------

try:
    spark.sql(f"DESCRIBE CATALOG {ADS_CATALOG}")
    print(f"✓ Catalog '{ADS_CATALOG}' is accessible")
except Exception as e:
    print(f"✗ Cannot access catalog: {e}")
    print("  Fix: verify the catalog name and your permissions")

# COMMAND ----------
# MAGIC %md ### Check 2 — All schemas exist

# COMMAND ----------

required_schemas = ["metadata", "sessions", "attrition", "sql_history", "audit"]
schemas = [r["databaseName"] for r in spark.sql(f"SHOW SCHEMAS IN {ADS_CATALOG}").collect()]
missing = [s for s in required_schemas if s not in schemas]

if not missing:
    print(f"✓ All {len(required_schemas)} schemas exist")
else:
    print(f"✗ Missing schemas: {missing}")
    print("  Fix: run notebook 00_schema_setup.py")

# COMMAND ----------
# MAGIC %md ### Check 3 — Metadata is populated

# COMMAND ----------

try:
    n = spark.sql(f"SELECT COUNT(*) AS n FROM {ADS_CATALOG}.metadata.columns").collect()[0]["n"]
    if n > 100:
        print(f"✓ metadata.columns has {n:,} rows — looks populated")
    else:
        print(f"✗ metadata.columns has only {n} rows — needs ingestion")
        print("  Fix: run notebook 01_ingest_metadata.py")
except Exception as e:
    print(f"✗ Cannot read metadata.columns: {e}")
    print("  Fix: run notebook 00_schema_setup.py then 01_ingest_metadata.py")

# COMMAND ----------
# MAGIC %md ### Check 4 — Volume subdirectories exist

# COMMAND ----------

import os
subdirs = ["protocols", "data_dictionary", "exports"]
for sub in subdirs:
    path = f"{VOLUME_BASE}/{sub}"
    if os.path.isdir(path):
        print(f"✓ {path}")
    else:
        print(f"✗ {path} — missing")
        print("  Fix: run Step 8 in 00_schema_setup.py")

# COMMAND ----------
# MAGIC %md ### Check 5 — Secrets scope exists

# COMMAND ----------

try:
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    scopes = [s.name for s in w.secrets.list_scopes()]
    if SECRETS_SCOPE in scopes:
        print(f"✓ Secrets scope '{SECRETS_SCOPE}' exists")
    else:
        print(f"✗ Secrets scope '{SECRETS_SCOPE}' not found")
        print(f"  Fix: databricks secrets create-scope {SECRETS_SCOPE}")
except Exception as e:
    print(f"  Note: could not check secrets scope: {e}")

# COMMAND ----------
# MAGIC %md ### Check 6 — API keys stored as secrets

# COMMAND ----------

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
required_keys = ["anthropic-api-key", "openai-api-key"]
try:
    stored = [s.key for s in w.secrets.list_secrets(scope=SECRETS_SCOPE)]
    for key in required_keys:
        if key in stored:
            print(f"✓ Secret '{key}' is stored in scope '{SECRETS_SCOPE}'")
        else:
            print(f"✗ Secret '{key}' NOT found in scope '{SECRETS_SCOPE}'")
            print(f"  Fix: databricks secrets put-secret --scope {SECRETS_SCOPE} --key {key}")
except Exception as e:
    print(f"✗ Could not list secrets: {e}")
    print(f"  Fix: check your permissions on scope '{SECRETS_SCOPE}'")

# COMMAND ----------
# MAGIC %md ### Check 7 — Foundation Model API / AI functions available

# COMMAND ----------

try:
    result = spark.sql("""
        SELECT ai_classify('This is a test sentence.', array('positive', 'negative', 'neutral')) AS label
    """).collect()[0]["label"]
    print(f"✓ ai_classify() available — test label: {result}")
except Exception as e:
    print(f"✗ ai_classify() failed: {e}")
    print("  Fix: enable Foundation Model API in workspace settings")
    print("  Databricks UI → Settings → Workspace → Foundation Model APIs → Enable")

# COMMAND ----------

try:
    result = spark.sql("""
        SELECT ai_extract('Patient is 45 years old with diabetes.',
               named_struct('age', 'the patient age', 'condition', 'the condition')) AS extracted
    """).collect()[0]["extracted"]
    print(f"✓ ai_extract() available")
    print(f"  Sample: {result}")
except Exception as e:
    print(f"✗ ai_extract() failed: {e}")
    print("  Fix: enable Foundation Model API in workspace settings")

# COMMAND ----------

try:
    result = spark.sql("""
        SELECT ai_query('databricks-meta-llama-3-3-70b-instruct',
                        'Reply with the word: READY') AS response
    """).collect()[0]["response"]
    print(f"✓ ai_query() / Foundation models available — response: {response}")
except Exception as e:
    print(f"✗ ai_query() failed: {e}")
    print("  Note: External LLM API keys (Anthropic, OpenAI) will be used as primary")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC If all checks show ✓ you are ready to deploy.
# MAGIC
# MAGIC **Deploy command (run in terminal):**
# MAGIC ```bash
# MAGIC databricks apps deploy ads-automation --source-code-path /path/to/ATTRITION_AGENT
# MAGIC ```
# MAGIC
# MAGIC Or use Databricks UI:
# MAGIC **Apps → Create App → Upload Code → Select your folder → Deploy**
