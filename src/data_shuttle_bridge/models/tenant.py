"""Tenant SQLModel table models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field, Relationship


class Tenant(SQLModel, table=True):
    """Represents a tenant in the multi-tenant system."""

    __tablename__ = "mt_tenants"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    slug: str = Field(unique=True, nullable=False, index=True)
    api_key: str = Field(unique=True, nullable=False)

    # Tenant metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Database connection info
    database_url: str = Field(nullable=False)  # SQLite file path or connection string

    # Schema version tracking
    current_schema_version: int = Field(default=1)
    schema_set_id: Optional[int] = Field(default=None, nullable=True)

    # Configuration
    is_active: bool = Field(default=True, nullable=False)
    metadata_json: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    secrets: list["TenantSecret"] = Relationship(
        back_populates="tenant",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TenantSecret(SQLModel, table=True):
    """Encrypted secrets storage for tenants."""

    __tablename__ = "mt_tenant_secrets"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="mt_tenants.id", nullable=False, index=True)

    key: str = Field(nullable=False, index=True)
    secret: str = Field(nullable=False)  # Encrypted

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    tenant: Optional["Tenant"] = Relationship(back_populates="secrets")
