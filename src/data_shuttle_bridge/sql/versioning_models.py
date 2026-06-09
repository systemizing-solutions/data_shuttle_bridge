"""Re-exports from data_shuttle_bridge.models.versioning for backward compatibility.

Also provides create_all_tables utility function.
"""

from sqlmodel import SQLModel
from sqlalchemy.engine import Engine

from data_shuttle_bridge.models.versioning import (
    SchemaSet,
    SchemaVersion,
    SchemaDiff,
    MappingRule,
    ConsolidationView,
)

__all__ = [
    "SchemaSet",
    "SchemaVersion",
    "SchemaDiff",
    "MappingRule",
    "ConsolidationView",
    "create_all_tables",
]


def create_all_tables(engine: Engine) -> None:
    """Create all versioning registry tables in the database."""
    SQLModel.metadata.create_all(engine)
