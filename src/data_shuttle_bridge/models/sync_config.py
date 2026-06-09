"""
Synchronization configuration models.

Controls what data is synced: entire database, specific schemas,
specific tables, or specific tables with row-level filters.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
import json
import yaml  # type: ignore
from pathlib import Path

from data_shuttle_bridge.models.enums import SyncScope, FilterOperator


@dataclass
class FilterCondition:
    """
    A single filter condition for row-level filtering.

    Supports both simple columns and cross-table references.

    Attributes:
        field: Column name or cross-table reference (e.g. "status" or "owner.company_id")
        operator: Comparison operator from FilterOperator
        value: Literal value to compare against
        reference_table: Optional table to join with for cross-table filtering
        reference_key: Optional key in the reference table to match against
    """

    field: str  # Column name or dot-notation for joins
    operator: FilterOperator
    value: Any = None  # None for is_null / is_not_null
    reference_table: Optional[str] = None  # For cross-table references
    reference_key: Optional[str] = None  # Column in reference table to match

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FilterCondition:
        """Create FilterCondition from dictionary."""
        operator = data.get("operator", "=")
        if isinstance(operator, str):
            operator = FilterOperator(operator)

        return cls(
            field=data["field"],
            operator=operator,
            value=data.get("value"),
            reference_table=data.get("reference_table"),
            reference_key=data.get("reference_key"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "field": self.field,
            "operator": self.operator.value,
            "value": self.value,
            "reference_table": self.reference_table,
            "reference_key": self.reference_key,
        }


@dataclass
class FilterExpression:
    """
    Composite filter expression supporting AND/OR logic.

    Attributes:
        conditions: List of FilterCondition objects
        logic: "AND" or "OR" for combining conditions
        nested: Optional nested FilterExpression for complex logic
    """

    conditions: List[FilterCondition] = field(default_factory=list)
    logic: str = "AND"  # "AND" or "OR"
    nested: Optional[FilterExpression] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FilterExpression:
        """Create FilterExpression from dictionary."""
        conditions = [FilterCondition.from_dict(c) for c in data.get("conditions", [])]
        nested = None
        if "nested" in data:
            nested = cls.from_dict(data["nested"])

        return cls(
            conditions=conditions,
            logic=data.get("logic", "AND"),
            nested=nested,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "conditions": [c.to_dict() for c in self.conditions],
            "logic": self.logic,
        }
        if self.nested:
            result["nested"] = self.nested.to_dict()
        return result


@dataclass
class TableSyncRule:
    """
    Synchronization rule for a specific table.

    Attributes:
        enabled: Whether this table should be synced
        filter: Optional FilterExpression for row-level filtering
        exclude_columns: Columns to exclude from sync
        include_only_columns: If set, only sync these columns
    """

    enabled: bool = True
    filter: Optional[FilterExpression] = None
    exclude_columns: List[str] = field(default_factory=list)
    include_only_columns: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TableSyncRule:
        """Create TableSyncRule from dictionary."""
        filter_expr = None
        if "filter" in data:
            filter_expr = FilterExpression.from_dict(data["filter"])

        return cls(
            enabled=data.get("enabled", True),
            filter=filter_expr,
            exclude_columns=data.get("exclude_columns", []),
            include_only_columns=data.get("include_only_columns"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "enabled": self.enabled,
            "exclude_columns": self.exclude_columns,
        }
        if self.filter:
            result["filter"] = self.filter.to_dict()
        if self.include_only_columns:
            result["include_only_columns"] = self.include_only_columns
        return result


@dataclass
class SchemaSyncConfig:
    """Configuration for schema-level syncing."""

    name: str
    include_all_tables: bool = True
    tables: Optional[Dict[str, bool]] = None

    def is_table_included(self, table_name: str) -> bool:
        """Check if a table in this schema should be included."""
        if self.include_all_tables:
            return True
        if self.tables:
            return self.tables.get(table_name, False)
        return False


@dataclass
class SyncConfig:
    """
    Master synchronization configuration.

    Controls what data gets synced from a local database to a remote service.
    Supports hierarchy of control: DB -> Schema -> Table -> Row.

    Attributes:
        scope: SyncScope level (database, schema, tables, filtered)
        schemas: List of schemas to sync (for schema-level control)
        tables: Dict mapping table names to TableSyncRule
        enabled: Master enable/disable switch
    """

    scope: SyncScope = SyncScope.DATABASE
    schemas: Optional[List[SchemaSyncConfig]] = None
    tables: Dict[str, TableSyncRule] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self):
        """Validate configuration."""
        if self.scope == SyncScope.SCHEMA and not self.schemas:
            raise ValueError("Must provide schemas when using SCHEMA scope")
        if self.scope == SyncScope.TABLES and not self.tables:
            raise ValueError("Must provide tables when using TABLES scope")
        if self.scope == SyncScope.FILTERED and not self.tables:
            raise ValueError("Must provide tables when using FILTERED scope")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SyncConfig:
        """Create SyncConfig from dictionary."""
        scope_str = data.get("scope", "database")
        scope = SyncScope(scope_str)

        schemas = None
        if "schemas" in data:
            schemas = [
                SchemaSyncConfig(
                    name=s["name"],
                    include_all_tables=s.get("include_all_tables", True),
                    tables=s.get("tables"),
                )
                for s in data.get("schemas", [])
            ]

        tables = {}
        for table_name, table_data in data.get("tables", {}).items():
            if isinstance(table_data, dict):
                tables[table_name] = TableSyncRule.from_dict(table_data)
            elif isinstance(table_data, bool):
                # Simple true/false for table inclusion
                tables[table_name] = TableSyncRule(enabled=table_data)
            else:
                tables[table_name] = TableSyncRule()

        return cls(
            scope=scope,
            schemas=schemas,
            tables=tables,
            enabled=data.get("enabled", True),
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> SyncConfig:
        """
        Load configuration from a file.

        Supports YAML and JSON formats. Format is detected by file extension.

        Args:
            path: Path to configuration file (.yaml, .yml, or .json)

        Returns:
            SyncConfig object
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        data = None
        if path.suffix in (".yaml", ".yml"):
            with open(path) as f:
                data = yaml.safe_load(f)
        elif path.suffix == ".json":
            with open(path) as f:
                data = json.load(f)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

        return cls.from_dict(data or {})

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "scope": self.scope.value,
            "enabled": self.enabled,
        }

        if self.schemas:
            result["schemas"] = [
                {
                    "name": s.name,
                    "include_all_tables": s.include_all_tables,
                    "tables": s.tables,
                }
                for s in self.schemas
            ]

        if self.tables:
            result["tables"] = {
                name: rule.to_dict() for name, rule in self.tables.items()
            }

        return result

    def to_file(self, path: Union[str, Path], format: str = "yaml") -> None:
        """
        Save configuration to a file.

        Args:
            path: Path to save to
            format: "yaml" or "json"
        """
        path = Path(path)
        data = self.to_dict()

        if format == "yaml":
            with open(path, "w") as f:
                yaml.dump(data, f, default_flow_style=False)
        elif format == "json":
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def is_table_synced(self, table_name: str, schema_name: str = None) -> bool:
        """
        Check if a table should be synced.

        Args:
            table_name: Name of the table
            schema_name: Name of the schema (if applicable)

        Returns:
            True if the table should be synced
        """
        if not self.enabled:
            return False

        if self.scope == SyncScope.DATABASE:
            return True

        if self.scope == SyncScope.SCHEMA:
            if not self.schemas or not schema_name:
                return False
            for schema in self.schemas:
                if schema.name == schema_name:
                    return schema.is_table_included(table_name)
            return False

        if self.scope in (SyncScope.TABLES, SyncScope.FILTERED):
            if table_name not in self.tables:
                return False
            return self.tables[table_name].enabled

        return False

    def get_table_rule(self, table_name: str) -> Optional[TableSyncRule]:
        """Get the TableSyncRule for a specific table, if it exists."""
        return self.tables.get(table_name)
