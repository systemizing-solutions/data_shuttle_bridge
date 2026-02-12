"""Drift policy engine for default column handling in consolidation views."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDefault:
    """Specification for how to handle a column in a version table."""

    kind: str  # 'null', 'schema_default', 'literal', 'require_rule'
    value: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {"kind": self.kind, "value": self.value}


class DriftPolicyEngine(ABC):
    """Abstract base for drift policy engines."""

    @abstractmethod
    def defaults_for_view(
        self,
        target_columns: List[str],
        version_schema: Dict[str, Any],
        diff_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, ColumnDefault]:
        """
        Determine default behavior for each target column in a specific version.

        Args:
            target_columns: List of unified column names (output columns)
            version_schema: The JSON Schema for this version
            diff_records: Optional list of diff records to inform policy

        Returns:
            Dict mapping target_column -> ColumnDefault
        """


class DefaultDriftPolicy(DriftPolicyEngine):
    """
    Default policy for handling schema drift:
    - missing columns in version: return NULL
    - new columns in version: include as-is
    - type changes: require explicit mapping rule
    """

    def defaults_for_view(
        self,
        target_columns: List[str],
        version_schema: Dict[str, Any],
        diff_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, ColumnDefault]:
        """
        Generate defaults for each target column.

        Logic:
        - If column exists in version_schema: use schema default if defined, else NULL
        - If column missing in version_schema: NULL
        - Type mismatches must be handled via explicit mapping rules
        """
        defaults: Dict[str, ColumnDefault] = {}
        version_props = version_schema.get("properties", {})
        version_required = set(version_schema.get("required", []))

        for col in target_columns:
            if col in version_props:
                # Column exists in this version
                prop = version_props[col]
                schema_default = prop.get("default")

                if schema_default is not None:
                    defaults[col] = ColumnDefault(
                        kind="schema_default",
                        value=schema_default,
                    )
                else:
                    # Use NULL if column is optional or nullable
                    defaults[col] = ColumnDefault(kind="null")
            else:
                # Column missing from this version: use NULL
                defaults[col] = ColumnDefault(kind="null")

        return defaults


class StrictDriftPolicy(DriftPolicyEngine):
    """
    Strict policy: only allow missing columns if they are added in later versions.
    Removed columns cause errors.
    """

    def defaults_for_view(
        self,
        target_columns: List[str],
        version_schema: Dict[str, Any],
        diff_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, ColumnDefault]:
        """
        Generate defaults, raising errors for removed columns.
        """
        defaults: Dict[str, ColumnDefault] = {}
        version_props = version_schema.get("properties", {})
        removed_cols = set()

        if diff_records:
            removed_cols = {
                r["column"] for r in diff_records if r.get("kind") == "remove_column"
            }

        for col in target_columns:
            if col in removed_cols:
                # Column was removed: require explicit rule
                defaults[col] = ColumnDefault(kind="require_rule")
            elif col in version_props:
                # Column exists: use schema default or NULL
                prop = version_props[col]
                schema_default = prop.get("default")

                if schema_default is not None:
                    defaults[col] = ColumnDefault(
                        kind="schema_default",
                        value=schema_default,
                    )
                else:
                    defaults[col] = ColumnDefault(kind="null")
            else:
                # Column not in this version: NULL
                defaults[col] = ColumnDefault(kind="null")

        return defaults


class FillDefaultsPolicy(DriftPolicyEngine):
    """
    Policy that fills missing columns with provided defaults.
    Useful for backfilling across versions.
    """

    def __init__(self, fill_defaults: Optional[Dict[str, Any]] = None):
        """
        Initialize with optional fill defaults.

        Args:
            fill_defaults: Dict mapping column -> default value to use
        """
        self.fill_defaults = fill_defaults or {}

    def defaults_for_view(
        self,
        target_columns: List[str],
        version_schema: Dict[str, Any],
        diff_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, ColumnDefault]:
        """
        Generate defaults, using fill_defaults for missing columns.
        """
        defaults: Dict[str, ColumnDefault] = {}
        version_props = version_schema.get("properties", {})

        for col in target_columns:
            if col in version_props:
                prop = version_props[col]
                schema_default = prop.get("default")

                if schema_default is not None:
                    defaults[col] = ColumnDefault(
                        kind="schema_default",
                        value=schema_default,
                    )
                else:
                    defaults[col] = ColumnDefault(kind="null")
            else:
                # Column missing: use fill_defaults if available
                if col in self.fill_defaults:
                    defaults[col] = ColumnDefault(
                        kind="literal",
                        value=self.fill_defaults[col],
                    )
                else:
                    defaults[col] = ColumnDefault(kind="null")

        return defaults
