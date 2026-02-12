"""Consolidation view builder for unified querying across schema versions."""

from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import Table, select, union_all, Select, literal
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from data_shuttle_bridge.sql.mapping import (
    DefaultMappingRuleEngine,
    parse_mapping_rules_json,
)
from data_shuttle_bridge.sql.policy import DefaultDriftPolicy, ColumnDefault


class ConsolidationViewBuilder:
    """
    Builds unified SELECT queries across multiple schema versions.

    Responsibilities:
    - Determine target unified columns
    - For each version, apply mapping rules and defaults
    - Compose UNION ALL across versions
    - Generate optional DB VIEW definition
    """

    def __init__(
        self,
        engine: Engine,
        policy_engine=None,
        mapping_engine=None,
    ):
        """
        Initialize builder.

        Args:
            engine: SQLAlchemy Engine
            policy_engine: DriftPolicyEngine instance (defaults to DefaultDriftPolicy)
            mapping_engine: MappingRuleEngine instance (defaults to DefaultMappingRuleEngine)
        """
        self.engine = engine
        self.policy_engine = policy_engine or DefaultDriftPolicy()
        self.mapping_engine = mapping_engine or DefaultMappingRuleEngine()

    def build_union_select(
        self,
        *,
        version_tables: List[Tuple[int, Table]],
        target_columns: List[str],
        mapping_rules_by_version: Optional[Dict[int, str]] = None,
        schemas_by_version: Optional[Dict[int, Dict[str, Any]]] = None,
        include_schema_version_column: bool = True,
    ) -> Select:
        """
        Build a UNION ALL select across versions.

        Args:
            version_tables: List of (version_number, Table) tuples
            target_columns: List of unified output column names
            mapping_rules_by_version: Dict mapping version_id -> mapping_rules_json_str
            schemas_by_version: Dict mapping version_id -> JSON Schema dict
            include_schema_version_column: If True, add _schema_version column

        Returns:
            SQLAlchemy Select object representing the unified view
        """
        mapping_rules_by_version = mapping_rules_by_version or {}
        schemas_by_version = schemas_by_version or {}

        per_version_selects: List[Select] = []

        for version_num, table in version_tables:
            # Get mapping rules and schema for this version
            rules_json = mapping_rules_by_version.get(version_num, "")
            rules = parse_mapping_rules_json(rules_json)
            schema = schemas_by_version.get(version_num, {})

            # Get defaults from policy
            defaults = self.policy_engine.defaults_for_view(
                target_columns=target_columns,
                version_schema=schema,
            )

            # Build expressions for target columns
            expressions = self.mapping_engine.build_projection_expressions(
                version_table=table,
                target_columns=target_columns,
                rules=rules,
                defaults=self._convert_defaults_to_dicts(defaults),
            )

            # Collect expressions in target column order
            select_exprs = [expressions[col] for col in target_columns]

            # Add schema version column if requested
            if include_schema_version_column:
                select_exprs.append(literal(version_num).label("_schema_version"))

            # Create select for this version
            version_select = select(*select_exprs).select_from(table)
            per_version_selects.append(version_select)

        # Compose UNION ALL
        if not per_version_selects:
            raise ValueError("No version selects to union")

        return union_all(*per_version_selects)

    def create_db_view(
        self,
        *,
        session: Session,
        view_name: str,
        version_tables: List[Tuple[int, Table]],
        target_columns: List[str],
        mapping_rules_by_version: Optional[Dict[int, str]] = None,
        schemas_by_version: Optional[Dict[int, Dict[str, Any]]] = None,
        if_exists: str = "replace",
    ) -> str:
        """
        Create a database VIEW from the consolidated select.

        Args:
            session: SQLAlchemy Session
            view_name: Name for the view
            version_tables: List of (version_number, Table) tuples
            target_columns: List of unified output column names
            mapping_rules_by_version: Dict mapping version_id -> rules_json_str
            schemas_by_version: Dict mapping version_id -> schema dict
            if_exists: 'replace' to DROP and recreate, 'ignore' to skip if exists

        Returns:
            The CREATE VIEW SQL statement string
        """
        # Build the union select
        unified_select = self.build_union_select(
            version_tables=version_tables,
            target_columns=target_columns,
            mapping_rules_by_version=mapping_rules_by_version,
            schemas_by_version=schemas_by_version,
        )

        # Compile to SQL
        compiled = unified_select.compile(bind=self.engine)
        union_sql = str(compiled)

        # Build CREATE VIEW statement
        create_view_sql = f"CREATE VIEW {view_name} AS\n{union_sql}"

        # Optionally drop existing view
        if if_exists == "replace":
            drop_sql = f"DROP VIEW IF EXISTS {view_name}"
            session.execute(drop_sql)

        # Create the view
        session.execute(create_view_sql)
        session.commit()

        return create_view_sql

    def _convert_defaults_to_dicts(
        self, defaults: Dict[str, ColumnDefault]
    ) -> Dict[str, Any]:
        """Convert ColumnDefault objects to dicts for mapping engine."""
        return {k: v.to_dict() for k, v in defaults.items()}


def build_consolidated_select(
    *,
    version_tables: List[Tuple[int, Table]],
    target_columns: List[str],
    rename_rules_by_version: Optional[Dict[int, Dict[str, str]]] = None,
    defaults_by_column: Optional[Dict[str, Any]] = None,
) -> Select:
    """
    Convenience function for building a consolidated view with simple rename rules.

    This is a simplified interface for the common case of renames + defaults.

    Args:
        version_tables: List of (version_number, Table) tuples
        target_columns: Unified output columns
        rename_rules_by_version: Dict[version] -> Dict[dst] -> src mapping
        defaults_by_column: Dict[column] -> literal default value

    Returns:
        SQLAlchemy Select object
    """
    rename_rules_by_version = rename_rules_by_version or {}
    defaults_by_column = defaults_by_column or {}

    per_version_selects: List[Select] = []

    for version_num, table in version_tables:
        rename_map = rename_rules_by_version.get(version_num, {})

        exprs = []
        for col in target_columns:
            # Determine source column name
            source_col = rename_map.get(col, col)

            if source_col in table.c:
                exprs.append(table.c[source_col].label(col))
            else:
                # Column missing: use default or NULL
                if col in defaults_by_column:
                    exprs.append(literal(defaults_by_column[col]).label(col))
                else:
                    from sqlalchemy import null

                    exprs.append(null().label(col))

        # Add schema version tracking
        exprs.append(literal(version_num).label("_schema_version"))

        version_select = select(*exprs).select_from(table)
        per_version_selects.append(version_select)

    return union_all(*per_version_selects)
