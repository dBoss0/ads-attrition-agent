# ADS Automation — Attrition Module
## Project Guide for Claude Code

**Client:** Johnson & Johnson MedTech via Mu Sigma  
**Deployment:** Databricks Apps (Runtime 17.3 LTS, Python 3.12, Spark 4.0)  
**Entry point:** `app.py` at project root (Databricks Apps requirement — never rename)

---

## Scope Boundary

**ONLY implement: ATTRITION**

Do not implement: outcomes, covariates, treatment arms, feasibility, statistical analysis.  
Do not generate SQL for anything outside the attrition waterfall workflow.

---

## Premier Healthcare Database

**READ ONLY.** Never create, modify, or drop Premier tables.

```
Catalog:  rhealth_premier_phg
Schema:   bronze_native_premier_phd
```

All table references go through `config/databricks.py::DatabricksConfig`.  
Never hardcode `rhealth_premier_phg.bronze_native_premier_phd.anything` as a raw string outside that file.

### Critical Join Rules
- All patient-level tables join via `pat_key` (encounter key)
- `MORTALITY` and `TOKENS` join via `medrec_key` (patient key)
- ICD joins ALWAYS include `icd_version` — ICD-9 and ICD-10 codes overlap
- Inpatient filter: `i_o_ind = 'I'` or `pat_type = '08'`
- `PROV_ENROLLMENT` joins: `prov_id AND disc_mon AND i_o_ind = 'I'`

### Add-on Tables (flag in SQL comments)
GENLAB, VITALS, LAB_RES, LAB_SENS, PROC_SUPPLY, MORTALITY, TOKENS, MOTHER_INFANT_LINK, PAT_SDOH  
→ These require additional Premier licensing. Always add `-- ADD-ON LICENSE REQUIRED` comment.

---

## SQL Generation Rules (non-negotiable)

1. **Spark SQL ONLY** — never Redshift, PostgreSQL, SQL Server syntax
2. **ROW_NUMBER()** — never RANK() unless there is a specific technical reason stated in a comment
3. **Temp views**: `CREATE OR REPLACE TEMP VIEW ads_attrition_{session_id}_{step_num}_{slug} AS`
4. **Fully qualified names**: always `rhealth_premier_phg.bronze_native_premier_phd.{table}`
5. **No SELECT *** in production step SQL
6. **No hardcoded ICD/CPT codes** — codes come from protocol extraction, not from the LLM
7. **Every SQL must have a corresponding QC SQL**

---

## Architecture — Clean Architecture + DDD

```
domain/         Pure Python — zero framework imports. Entities + abstract ports.
infrastructure/ Concrete implementations of ports. Spark, Delta, LLM clients.
application/    Orchestration services. Depend on ports, not implementations.
ui/             Gradio components. Depend on application layer only.
config/         Settings (env-driven), LLM model map, Databricks table constants.
```

Dependency direction: `ui → application → domain ← infrastructure`  
Infrastructure depends on domain interfaces (ports), never the reverse.

---

## Human-in-the-Loop Gates (5 mandatory)

Nothing auto-proceeds past these states. All require explicit analyst action.

| State | What analyst does |
|---|---|
| `EXTRACTION_COMPLETE` | Review/edit extracted criteria → Approve Criteria |
| `STEPS_COMPLETE` | Review/reorder/edit attrition steps → Approve Steps |
| `SQL_COMPLETE` | Review SQL per step → Approve SQL (each step individually) |
| `EXECUTED` | Review row counts + QC → Approve Results |
| `COHORT_READY` | Review final cohort SQL → Approve Final Cohort |

The state machine is in `domain/entities/session.py::VALID_TRANSITIONS`.  
Never add auto-transitions that skip analyst approval.

---

## LLM Routing

Never call LLM clients directly. Always go through `infrastructure/llm/router.py::LLMRouter`.

| Task | Model | Why |
|---|---|---|
| Section boundary detection | Claude Opus 4.8 | Best long-doc reasoning |
| Criteria extraction | Claude Opus 4.8 | Medical domain, conservative |
| Step sequencing | GPT-5.6 | Ordered list + dependency chains |
| SQL generation | GPT-5.5 | Code generation |
| QC SQL | Claude Opus 4.8 | Conservative validation |
| Business explanation | Sol | Narrative output |
| Metadata matching | Luna | Premier domain familiarity |
| Fast classification | GPT-5.5 | Cheap, sub-second |

Model assignments live in `config/llm_models.py::TASK_MODEL_MAP`.

---

## Metadata-First SQL

SQL is NEVER generated from LLM knowledge alone.  
The pipeline is:

```
Criterion text
    → MetadataContextProvider.build_context_for_criterion()
    → MetadataContext (tables, columns, joins, rules)
    → Injected into SQL prompt
    → LLM generates SQL using only the provided metadata
    → SqlValidator checks table/column names against metadata
    → If validation fails → retry, do not surface to analyst
```

`MetadataContextProvider` is the seam for Vector Search (Phase 12).  
Today it queries Delta. Tomorrow it queries Vector Search. SQL generator never changes.

---

## Key File Locations

| What | Where |
|---|---|
| All settings | `config/settings.py` — env var prefix `ADS_` |
| Table FQN constants | `config/databricks.py::DatabricksConfig` |
| LLM model assignment | `config/llm_models.py::TASK_MODEL_MAP` |
| Session state machine | `domain/entities/session.py` |
| Abstract repo interfaces | `domain/ports/` |
| Delta implementations | `infrastructure/delta/` (Phase 3) |
| LLM clients | `infrastructure/llm/` (Phase 3) |
| Parser replacement | `application/document_ai/` (Phase 5) |
| Attrition engine | `application/attrition/` (Phase 6) |
| SQL generator | `application/sql_generation/` (Phase 7) |
| Gradio UI components | `ui/components/` (Phase 9) |

---

## Parser.py Business Rules to Preserve (Phase 5)

These rules are correct and calibrated — do not change them:

1. Last occurrence of section headers wins (skip TOC)
2. Longest-key-first data source matching
3. Erase matched data source key before next search
4. Inclusion steps numbered before exclusion steps (global counter)
5. Strip leading `\d+.` from step text
6. `detect_data_source` runs on FULL document text
7. `DATA_SOURCE_MASTER` dictionary with canonical names
8. Suppress preamble: "patients will be included/excluded", "must meet all the following"
9. Drop lines starting with "individuals", "see", "product codes"

---

## What NOT to Do

- Never hardcode table/column names outside `config/databricks.py`
- Never generate SQL without first consulting the metadata repository
- Never call LLM clients directly — always via LLMRouter
- Never add auto-transitions past analyst gates
- Never generate Redshift, PostgreSQL, or SQL Server syntax
- Never use RANK() — use ROW_NUMBER()
- Never hallucinate Premier table or column names
- Never modify Premier tables (READ ONLY)
- Never add pyspark or delta-spark to requirements.txt (runtime provides them)
