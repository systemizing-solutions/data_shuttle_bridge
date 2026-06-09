"""Drift policy data models."""

from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDefault:
    """Specification for how to handle a column in a version table."""

    kind: str  # 'null', 'schema_default', 'literal', 'require_rule'
    value: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {"kind": self.kind, "value": self.value}
