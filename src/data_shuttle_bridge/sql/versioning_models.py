"""ORM models for schema versioning and consolidation registry."""

from datetime import datetime
from typing import Optional, List
from sqlmodel import (
    SQLModel,
    Field,
    Relationship,
    JSON,
    Column as SQLModelColumn,
    create_engine,
    Session,
)
from sqlalchemy import (
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.engine import Engine
import json


class SchemaSet(SQLModel, table=True):
    """Logical entity representing an evolving schema (e.g., customer, order)."""

    __tablename__ = "schema_sets"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(
        sa_column=SQLModelColumn(String(255), nullable=False, index=True),
        description="Unique key identifier (e.g., 'customer')",
    )
    name: str = Field(
        sa_column=SQLModelColumn(String(255), nullable=False),
        description="Human-readable name",
    )
    description: Optional[str] = Field(
        default=None,
        sa_column=SQLModelColumn(Text),
        description="Optional description of the schema set",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )

    # Relationships
    versions: List["SchemaVersion"] = Relationship(back_populates="schema_set")
    consolidation_views: List["ConsolidationView"] = Relationship(
        back_populates="schema_set"
    )

    __table_args__ = (UniqueConstraint("key", name="uq_schema_sets_key"),)


class SchemaVersion(SQLModel, table=True):
    """A specific version of a schema within a schema set."""

    __tablename__ = "schema_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    schema_set_id: int = Field(foreign_key="schema_sets.id", nullable=False, index=True)
    version: int = Field(
        sa_column=SQLModelColumn(Integer, nullable=False),
        description="Version number (incremental)",
    )
    parent_version_id: Optional[int] = Field(
        foreign_key="schema_versions.id",
        default=None,
        description="Parent version for lineage; None if root",
    )
    schema_json: str = Field(
        sa_column=SQLModelColumn(Text, nullable=False),
        description="JSON Schema document (JSON 2020-12 or similar)",
    )
    table_name: str = Field(
        sa_column=SQLModelColumn(String(255), nullable=False),
        description="Physical table name for this version (e.g., customer__v2)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )

    # Relationships
    schema_set: SchemaSet = Relationship(back_populates="versions")
    diffs: List["SchemaDiff"] = Relationship(back_populates="to_version")
    mapping_rules: List["MappingRule"] = Relationship(back_populates="schema_version")

    __table_args__ = (
        UniqueConstraint(
            "schema_set_id", "version", name="uq_schema_versions_set_version"
        ),
        Index("ix_schema_versions_table_name", "table_name"),
    )


class SchemaDiff(SQLModel, table=True):
    """Computed diff between two schema versions."""

    __tablename__ = "schema_diffs"

    id: Optional[int] = Field(default=None, primary_key=True)
    from_version_id: int = Field(
        foreign_key="schema_versions.id", nullable=False, index=True
    )
    to_version_id: int = Field(
        foreign_key="schema_versions.id", nullable=False, index=True
    )
    diff_json: str = Field(
        sa_column=SQLModelColumn(Text, nullable=False),
        description="Structured diff records (JSON array)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )

    # Relationships
    to_version: SchemaVersion = Relationship(back_populates="diffs")

    __table_args__ = (
        UniqueConstraint(
            "from_version_id", "to_version_id", name="uq_schema_diffs_from_to"
        ),
    )


class MappingRule(SQLModel, table=True):
    """Explicit mapping rules for handling schema drift in view consolidation."""

    __tablename__ = "mapping_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    schema_version_id: int = Field(
        foreign_key="schema_versions.id", nullable=False, index=True
    )
    rules_json: str = Field(
        sa_column=SQLModelColumn(Text, nullable=False),
        description="Array of mapping rule objects (JSON)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )

    # Relationships
    schema_version: SchemaVersion = Relationship(back_populates="mapping_rules")

    __table_args__ = (
        UniqueConstraint("schema_version_id", name="uq_mapping_rules_version"),
    )


class ConsolidationView(SQLModel, table=True):
    """Definition for a consolidated view across multiple schema versions."""

    __tablename__ = "consolidation_views"

    id: Optional[int] = Field(default=None, primary_key=True)
    schema_set_id: int = Field(foreign_key="schema_sets.id", nullable=False, index=True)
    name: str = Field(
        sa_column=SQLModelColumn(String(255), nullable=False),
        description="Name of the consolidation view",
    )
    included_versions: str = Field(
        sa_column=SQLModelColumn(Text, nullable=False),
        description="JSON array of included version IDs",
    )
    target_columns: str = Field(
        sa_column=SQLModelColumn(Text, nullable=False),
        description="JSON array of target unified column names",
    )
    mode: str = Field(
        sa_column=SQLModelColumn(String(50), nullable=False),
        description="View mode: 'selectable', 'db_view', or 'materialized'",
    )
    definition_sql: Optional[str] = Field(
        default=None,
        sa_column=SQLModelColumn(Text),
        description="Generated SQL definition (for reproducibility)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=SQLModelColumn(DateTime, nullable=False),
    )

    # Relationships
    schema_set: SchemaSet = Relationship(back_populates="consolidation_views")

    __table_args__ = (
        UniqueConstraint(
            "schema_set_id", "name", name="uq_consolidation_views_set_name"
        ),
    )


def create_all_tables(engine: Engine) -> None:
    """Create all versioning registry tables in the database."""
    SQLModel.metadata.create_all(engine)
