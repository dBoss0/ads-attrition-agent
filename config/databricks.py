"""
Databricks-specific constants and fully qualified name helpers.
All Premier table references flow through this module — never hardcode
catalog.schema.table strings elsewhere in the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import get_settings


@dataclass(frozen=True)
class DatabricksConfig:
    """
    Centralises all Databricks location strings.
    Constructed once from Settings so they stay in sync with env vars.
    """

    # ── Premier (READ ONLY) ───────────────────────────────────────────────────
    premier_catalog: str
    premier_schema: str

    # ── ADS Automation (READ/WRITE) ───────────────────────────────────────────
    ads_catalog: str
    metadata_schema: str
    sessions_schema: str
    attrition_schema: str
    sql_history_schema: str
    audit_schema: str

    # ── Volume Paths ──────────────────────────────────────────────────────────
    protocols_volume: str
    data_dictionary_volume: str
    exports_volume: str

    # ── Premier Patient-Level Tables ──────────────────────────────────────────
    @property
    def patdemo(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.patdemo"

    @property
    def paticd_diag(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.paticd_diag"

    @property
    def paticd_proc(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.paticd_proc"

    @property
    def patcpt(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.patcpt"

    @property
    def patbill(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.patbill"

    @property
    def pataprdrg(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.pataprdrg"

    @property
    def genlab(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.genlab"

    @property
    def vitals(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.vitals"

    @property
    def mortality(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.mortality"

    @property
    def proc_supply(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.proc_supply"

    @property
    def pat_sdoh(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.pat_sdoh"

    @property
    def providers(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.providers"

    @property
    def prov_enrollment(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.prov_enrollment"

    @property
    def lab_res(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.lab_res"

    @property
    def lab_sens(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.lab_sens"

    @property
    def mother_infant_link(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.mother_infant_link"

    @property
    def tokens(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.tokens"

    # ── Premier Lookup Tables ─────────────────────────────────────────────────
    @property
    def icdcode(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.icdcode"

    @property
    def cptcode(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.cptcode"

    @property
    def pattype(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.pattype"

    @property
    def payor(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.payor"

    @property
    def admtype(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.admtype"

    @property
    def disstat(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.disstat"

    @property
    def msdrg(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.msdrg"

    @property
    def chgmstr(self) -> str:
        return f"{self.premier_catalog}.{self.premier_schema}.chgmstr"

    # ── ADS Metadata Tables ───────────────────────────────────────────────────
    @property
    def metadata_tables(self) -> str:
        return f"{self.ads_catalog}.{self.metadata_schema}.tables"

    @property
    def metadata_columns(self) -> str:
        return f"{self.ads_catalog}.{self.metadata_schema}.columns"

    @property
    def metadata_relationships(self) -> str:
        return f"{self.ads_catalog}.{self.metadata_schema}.relationships"

    @property
    def metadata_business_rules(self) -> str:
        return f"{self.ads_catalog}.{self.metadata_schema}.business_rules"

    @property
    def metadata_versions(self) -> str:
        return f"{self.ads_catalog}.{self.metadata_schema}.versions"

    @property
    def sessions_runs(self) -> str:
        return f"{self.ads_catalog}.{self.sessions_schema}.runs"

    @property
    def sessions_transitions(self) -> str:
        return f"{self.ads_catalog}.{self.sessions_schema}.transitions"

    @property
    def attrition_steps(self) -> str:
        return f"{self.ads_catalog}.{self.attrition_schema}.steps"

    @property
    def attrition_plans(self) -> str:
        return f"{self.ads_catalog}.{self.attrition_schema}.plans"

    @property
    def attrition_final_cohorts(self) -> str:
        return f"{self.ads_catalog}.{self.attrition_schema}.final_cohorts"

    @property
    def sql_history_versions(self) -> str:
        return f"{self.ads_catalog}.{self.sql_history_schema}.versions"

    @property
    def sql_history_results(self) -> str:
        return f"{self.ads_catalog}.{self.sql_history_schema}.results"

    @property
    def sql_history_qc(self) -> str:
        return f"{self.ads_catalog}.{self.sql_history_schema}.qc_results"

    @property
    def audit_log(self) -> str:
        return f"{self.ads_catalog}.{self.audit_schema}.log"

    def premier_table(self, name: str) -> str:
        """Return a fully qualified Premier table name."""
        return f"{self.premier_catalog}.{self.premier_schema}.{name.lower()}"

    def ads_table(self, schema: str, name: str) -> str:
        """Return a fully qualified ADS table name."""
        return f"{self.ads_catalog}.{schema}.{name.lower()}"


def get_databricks_config() -> DatabricksConfig:
    s = get_settings()
    return DatabricksConfig(
        premier_catalog=s.premier_catalog,
        premier_schema=s.premier_schema,
        ads_catalog=s.catalog,
        metadata_schema=s.metadata_schema,
        sessions_schema=s.sessions_schema,
        attrition_schema=s.attrition_schema,
        sql_history_schema=s.sql_history_schema,
        audit_schema=s.audit_schema,
        protocols_volume=s.protocols_path,
        data_dictionary_volume=s.data_dictionary_path,
        exports_volume=s.exports_path,
    )
