from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TableMetadata:
    table_id: str
    table_name: str
    description: str
    use_cases: list[str] = field(default_factory=list)
    is_addon: bool = False
    grain: str = ""
    primary_join_key: str = "pat_key"


@dataclass
class ColumnMetadata:
    column_id: str
    table_name: str
    column_name: str
    data_type: str
    description: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    code_set_type: str | None = None
    valid_values: str | None = None
    is_nullable: bool = True


@dataclass
class RelationshipMetadata:
    relationship_id: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    join_condition: str
    join_type: str = "INNER"
    notes: str = ""


@dataclass
class BusinessRule:
    rule_id: str
    rule_name: str
    rule_category: str
    description: str
    sql_pattern: str
    applicable_tables: list[str] = field(default_factory=list)


@dataclass
class MetadataContext:
    """
    Assembled context injected into SQL generation prompts.
    Today: built from Delta tables.
    Future: built from Vector Search results (same interface, no SQL generator change).
    """
    relevant_tables: list[TableMetadata] = field(default_factory=list)
    relevant_columns: list[ColumnMetadata] = field(default_factory=list)
    join_conditions: list[RelationshipMetadata] = field(default_factory=list)
    business_rules: list[BusinessRule] = field(default_factory=list)
    premier_fqn_prefix: str = "rhealth_premier_phg.bronze_native_premier_phd"

    def fully_qualified(self, table_name: str) -> str:
        return f"{self.premier_fqn_prefix}.{table_name.lower()}"

    def to_prompt_text(self) -> str:
        """Serialize context into structured text for LLM prompt injection."""
        lines = ["=== AVAILABLE PREMIER TABLES ==="]
        for t in self.relevant_tables:
            addon_flag = " [ADD-ON LICENSE REQUIRED]" if t.is_addon else ""
            lines.append(f"\nTABLE: {self.fully_qualified(t.table_name)}{addon_flag}")
            lines.append(f"  Description: {t.description}")
            lines.append(f"  Grain: {t.grain}")
            lines.append(f"  Join key: {t.primary_join_key}")

        lines.append("\n=== RELEVANT COLUMNS ===")
        current_table = ""
        for c in self.relevant_columns:
            if c.table_name != current_table:
                lines.append(f"\n{self.fully_qualified(c.table_name)}:")
                current_table = c.table_name
            vv = f"  [values: {c.valid_values}]" if c.valid_values else ""
            lines.append(f"  {c.column_name} ({c.data_type}): {c.description}{vv}")

        if self.join_conditions:
            lines.append("\n=== JOIN CONDITIONS ===")
            for r in self.join_conditions:
                lines.append(f"  {r.join_type} JOIN on: {r.join_condition}")

        if self.business_rules:
            lines.append("\n=== BUSINESS RULES ===")
            for r in self.business_rules:
                lines.append(f"  [{r.rule_category}] {r.rule_name}: {r.description}")

        return "\n".join(lines)


class MetadataRepository(ABC):
    """
    Abstract port for metadata retrieval.
    Concrete implementations: DeltaMetadataRepository (Phase 4).
    Future implementation: VectorSearchMetadataRepository (Phase 12).
    The SQL generator only depends on this interface.
    """

    @abstractmethod
    def get_table(self, table_name: str) -> TableMetadata | None: ...

    @abstractmethod
    def get_columns(self, table_name: str) -> list[ColumnMetadata]: ...

    @abstractmethod
    def get_all_tables(self) -> list[TableMetadata]: ...

    @abstractmethod
    def get_relationships(self, table_name: str) -> list[RelationshipMetadata]: ...

    @abstractmethod
    def get_business_rules(self, category: str | None = None) -> list[BusinessRule]: ...

    @abstractmethod
    def search_columns(self, query: str, top_k: int = 10) -> list[ColumnMetadata]:
        """Keyword search today. Vector similarity search in Phase 12."""
        ...

    @abstractmethod
    def validate_table_exists(self, table_name: str) -> bool: ...

    @abstractmethod
    def validate_column_exists(self, table_name: str, column_name: str) -> bool: ...

    @abstractmethod
    def build_context_for_criterion(
        self,
        criterion_text: str,
        clinical_concept: str,
        top_k_tables: int = 3,
    ) -> MetadataContext:
        """
        Main entry point for SQL generation.
        Given a criterion, return the relevant MetadataContext.
        """
        ...
