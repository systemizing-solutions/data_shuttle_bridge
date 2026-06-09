"""Mapping rules for schema drift and view consolidation."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from sqlalchemy import Column, Table, select, cast, literal, null, func, Select
from sqlalchemy.types import TypeEngine
from sqlalchemy.sql import expression

from data_shuttle_bridge.models.mapping import (
    MappingRuleBase,
    RenameRule,
    CastRule,
    ExpressionRule,
    DropRule,
)


class MappingRuleEngine(ABC):
    """Abstract base for mapping rule engines."""

    @abstractmethod
    def build_projection_expressions(
        self,
        *,
        version_table: Table,
        target_columns: List[str],
        rules: List[Dict[str, Any]],
        defaults: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build SQLAlchemy expressions for each target column.

        Args:
            version_table: SQLAlchemy Table for this version
            target_columns: List of unified output column names
            rules: List of mapping rule dicts for this version
            defaults: Dict mapping column -> ColumnDefault (from policy engine)

        Returns:
            Dict mapping target_column -> SQLAlchemy expression
        """


class DefaultMappingRuleEngine(MappingRuleEngine):
    """
    Default implementation handles: rename, cast, expression, drop.
    """

    def build_projection_expressions(
        self,
        *,
        version_table: Table,
        target_columns: List[str],
        rules: List[Dict[str, Any]],
        defaults: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build expressions for target columns using rules and defaults.

        Returns a dict of target_column -> SQLAlchemy expression.
        """
        # Parse rules into structured objects
        parsed_rules = self._parse_rules(rules)

        # Build a map of rules by target column for easy lookup
        rules_by_column: Dict[str, Any] = {}
        for rule in parsed_rules:
            if isinstance(rule, RenameRule):
                rules_by_column[rule.dst] = rule
            elif isinstance(rule, ExpressionRule):
                rules_by_column[rule.target_column] = rule
            elif isinstance(rule, DropRule):
                rules_by_column[rule.column] = rule
            # CastRule is applied implicitly

        # Build expressions
        expressions: Dict[str, Any] = {}

        for col in target_columns:
            if col in rules_by_column and isinstance(rules_by_column[col], DropRule):
                # Skip dropped columns
                continue

            expr = self._build_expression_for_column(
                version_table, col, parsed_rules, defaults
            )
            expressions[col] = expr

        return expressions

    def _parse_rules(self, rules_data: List[Dict[str, Any]]) -> List[MappingRuleBase]:
        """Parse rule dicts into rule objects."""
        parsed = []
        for rule_dict in rules_data:
            kind = rule_dict.get("kind")
            if kind == "rename":
                parsed.append(RenameRule.from_dict(rule_dict))
            elif kind == "cast":
                parsed.append(CastRule.from_dict(rule_dict))
            elif kind == "expression":
                parsed.append(ExpressionRule.from_dict(rule_dict))
            elif kind == "drop":
                parsed.append(DropRule.from_dict(rule_dict))
        return parsed

    def _build_expression_for_column(
        self,
        version_table: Table,
        target_column: str,
        rules: List[MappingRuleBase],
        defaults: Dict[str, Any],
    ) -> Any:
        """
        Build a SQLAlchemy expression for a target column.

        Logic:
        1. If there's an ExpressionRule for this column, use it
        2. If there's a RenameRule, use the renamed source column
        3. If column exists in table, use as-is
        4. Otherwise, apply default (NULL or literal)
        """
        # Check for ExpressionRule
        for rule in rules:
            if isinstance(rule, ExpressionRule) and rule.target_column == target_column:
                # Parse and return expression (MVP: simple column reference)
                # In full implementation, could parse more complex SQL
                if target_column in version_table.c:
                    return version_table.c[target_column].label(target_column)
                else:
                    return null().label(target_column)

        # Check for RenameRule
        source_col = target_column
        for rule in rules:
            if isinstance(rule, RenameRule) and rule.dst == target_column:
                source_col = rule.src
                break

        # Try to get column from table
        if source_col in version_table.c:
            col_expr = version_table.c[source_col]

            # Check for CastRule
            for rule in rules:
                if isinstance(rule, CastRule) and rule.column == source_col:
                    # Apply cast (would need type mapping here)
                    col_expr = cast(col_expr, self._get_sa_type(rule.target_type))
                    break

            return col_expr.label(target_column)
        else:
            # Column not in table: apply default
            default_spec = defaults.get(target_column)
            if default_spec:
                kind = default_spec.get("kind", "null")
                value = default_spec.get("value")

                if kind == "null":
                    return null().label(target_column)
                elif kind == "literal":
                    return literal(value).label(target_column)
                elif kind == "schema_default":
                    return literal(value).label(target_column)
                elif kind == "require_rule":
                    # This should have been caught earlier
                    return null().label(target_column)
            else:
                return null().label(target_column)

    def _get_sa_type(self, type_name: str) -> TypeEngine:
        """Get SQLAlchemy type from string name."""
        from sqlalchemy import String, Integer, Float, Boolean, DateTime, Date, Time

        type_map = {
            "String": String,
            "Integer": Integer,
            "Float": Float,
            "Boolean": Boolean,
            "DateTime": DateTime,
            "Date": Date,
            "Time": Time,
        }

        if type_name in type_map:
            return type_map[type_name]()
        else:
            raise ValueError(f"Unknown type: {type_name}")


def parse_mapping_rules_json(rules_json_str: str) -> List[Dict[str, Any]]:
    """Parse JSON mapping rules string into dict list."""
    import json

    if not rules_json_str:
        return []

    data = json.loads(rules_json_str)
    return data.get("rules", []) if isinstance(data, dict) else data


def serialize_mapping_rules(rules: List[MappingRuleBase]) -> str:
    """Serialize rules to JSON string."""
    import json

    rule_dicts = [r.to_dict() for r in rules]
    return json.dumps({"rules": rule_dicts})
