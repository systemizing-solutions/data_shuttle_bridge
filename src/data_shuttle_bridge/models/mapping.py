"""Mapping rule data models for schema drift and view consolidation."""

from typing import Any, Dict
from dataclasses import dataclass


@dataclass(frozen=True)
class MappingRuleBase:
    """Base class for mapping rules."""

    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        raise NotImplementedError


@dataclass(frozen=True)
class RenameRule(MappingRuleBase):
    """Rename a source column to a target column name."""

    src: str = ""  # Source column in version table
    dst: str = ""  # Target unified column name
    kind: str = "rename"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "from": self.src, "to": self.dst}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RenameRule":
        return cls(src=data["from"], dst=data["to"])


@dataclass(frozen=True)
class CastRule(MappingRuleBase):
    """Cast a column to a target SQL type."""

    column: str = ""  # Column name in version table
    target_type: str = ""  # Target type (e.g., 'String', 'Integer')
    kind: str = "cast"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "column": self.column,
            "target_type": self.target_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CastRule":
        return cls(column=data["column"], target_type=data["target_type"])


@dataclass(frozen=True)
class ExpressionRule(MappingRuleBase):
    """Use a custom SQL expression for a target column."""

    target_column: str = ""  # Target unified column name
    sql_expression: str = ""  # SQL expression as string (interpreted by engine)
    kind: str = "expression"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "target_column": self.target_column,
            "sql_expression": self.sql_expression,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExpressionRule":
        return cls(
            target_column=data["target_column"],
            sql_expression=data["sql_expression"],
        )


@dataclass(frozen=True)
class DropRule(MappingRuleBase):
    """Drop a column from the consolidated view."""

    column: str = ""  # Column to drop
    kind: str = "drop"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "column": self.column}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DropRule":
        return cls(column=data["column"])
