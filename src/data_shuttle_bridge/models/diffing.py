"""Schema diffing data models."""

from typing import Any, Dict, Optional
from dataclasses import dataclass, asdict


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
