"""Schema diffing engine for computing and classifying schema drift."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, asdict
import json


@dataclass(frozen=True)
class DiffRecord:
    """A single change between two schema versions."""

    kind: str  # 'add_column', 'remove_column', 'type_change', 'required_change', 'default_change'
    column: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    severity: str = "info"  # 'info', 'warning', 'error'

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiffRecord":
        """Create from dictionary representation."""
        return cls(**data)


class DiffEngine(ABC):
    """Abstract base for schema diffing engines."""

    @abstractmethod
    def diff(
        self,
        parent_schema: Dict[str, Any],
        child_schema: Dict[str, Any],
    ) -> List[DiffRecord]:
        """
        Compute diff between parent and child schemas.

        Args:
            parent_schema: Parent JSON Schema (dict or None for root)
            child_schema: Child JSON Schema (dict)

        Returns:
            Ordered list of DiffRecord items
        """


class DefaultDiffEngine(DiffEngine):
    """Default implementation: detects property additions, removals, and type changes."""

    def diff(
        self,
        parent_schema: Dict[str, Any],
        child_schema: Dict[str, Any],
    ) -> List[DiffRecord]:
        """
        Compute diff between schemas, focusing on properties.

        Detects:
        - Added columns (new properties)
        - Removed columns (missing properties)
        - Type changes
        - Required changes
        - Default value changes
        """
        diffs: List[DiffRecord] = []

        if parent_schema is None:
            # Root version: no diff
            return diffs

        parent_props = parent_schema.get("properties", {})
        child_props = child_schema.get("properties", {})
        parent_required = set(parent_schema.get("required", []))
        child_required = set(child_schema.get("required", []))

        # Detect added columns
        for col in child_props:
            if col not in parent_props:
                diffs.append(
                    DiffRecord(
                        kind="add_column",
                        column=col,
                        new_value=child_props[col].get("type"),
                        severity="info",
                    )
                )

        # Detect removed columns
        for col in parent_props:
            if col not in child_props:
                diffs.append(
                    DiffRecord(
                        kind="remove_column",
                        column=col,
                        old_value=parent_props[col].get("type"),
                        severity="warning",
                    )
                )

        # Detect type changes and other modifications
        for col in parent_props:
            if col in child_props:
                parent_prop = parent_props[col]
                child_prop = child_props[col]

                parent_type = parent_prop.get("type")
                child_type = child_prop.get("type")

                # Type change
                if parent_type != child_type:
                    diffs.append(
                        DiffRecord(
                            kind="type_change",
                            column=col,
                            old_value=parent_type,
                            new_value=child_type,
                            severity="error",
                        )
                    )

                # Required change
                parent_is_req = col in parent_required
                child_is_req = col in child_required

                if parent_is_req != child_is_req:
                    change = "required" if child_is_req else "optional"
                    diffs.append(
                        DiffRecord(
                            kind="required_change",
                            column=col,
                            old_value=parent_is_req,
                            new_value=child_is_req,
                            severity="warning",
                        )
                    )

                # Default value change
                parent_default = parent_prop.get("default")
                child_default = child_prop.get("default")

                if parent_default != child_default:
                    diffs.append(
                        DiffRecord(
                            kind="default_change",
                            column=col,
                            old_value=parent_default,
                            new_value=child_default,
                            severity="info",
                        )
                    )

        # Sort for deterministic ordering
        diffs.sort(key=lambda d: (d.kind, d.column))

        return diffs


def classify_drift(diffs: List[DiffRecord]) -> Dict[str, Any]:
    """
    Classify drift severity based on computed diffs.

    Returns a summary with categorization:
    - unresolved: type_change, likely_rename (heuristic)
    - auto_handled: add_column, remove_column, required_change
    - info: default_change
    """
    unresolved = [d for d in diffs if d.severity == "error"]
    warnings = [d for d in diffs if d.severity == "warning"]
    info = [d for d in diffs if d.severity == "info"]

    return {
        "total_changes": len(diffs),
        "unresolved_count": len(unresolved),
        "warning_count": len(warnings),
        "info_count": len(info),
        "unresolved": [d.to_dict() for d in unresolved],
        "warnings": [d.to_dict() for d in warnings],
        "info": [d.to_dict() for d in info],
    }


def compute_likely_renames(
    parent_schema: Dict[str, Any],
    child_schema: Dict[str, Any],
    threshold: float = 0.8,
) -> List[Dict[str, str]]:
    """
    Heuristic: suggest potential renames based on string similarity.

    This is for informational purposes only; users must explicitly
    confirm any rename mapping.

    Args:
        parent_schema: Parent JSON Schema
        child_schema: Child JSON Schema
        threshold: Similarity threshold (0-1) for suggesting a rename

    Returns:
        List of {old: column_name, new: column_name} suggestions
    """
    # MVP: simple heuristic based on column name prefixes/suffixes
    # Can be extended with more sophisticated similarity metrics

    parent_props = set(parent_schema.get("properties", {}).keys())
    child_props = set(child_schema.get("properties", {}).keys())

    removed = parent_props - child_props
    added = child_props - parent_props

    suggestions = []

    # Simple heuristic: if removed column is prefix/suffix of added column
    for rem in removed:
        for add in added:
            # Check for simple transformation patterns
            if _similar_enough(rem, add, threshold):
                suggestions.append({"old": rem, "new": add})

    return suggestions


def _similar_enough(old_name: str, new_name: str, threshold: float) -> bool:
    """Simple similarity check (can be replaced with better algorithm)."""
    # Normalize
    old_lower = old_name.lower()
    new_lower = new_name.lower()

    # Check common patterns:
    # - email -> primary_email (suffix addition)
    # - customer_id -> id (prefix removal)
    # - email_address -> email (alias)

    if new_lower.endswith(old_lower) or old_lower.endswith(new_lower):
        return True

    # Levenshtein-like heuristic: character overlap
    overlap = sum(1 for c in old_lower if c in new_lower)
    min_len = min(len(old_lower), len(new_lower))
    similarity = overlap / max(min_len, 1) if min_len > 0 else 0

    return similarity >= threshold
