"""Multi-tenant change log and sync state SQLModel table models."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlmodel import SQLModel, Field, Column as SQLModelColumn

from sqlalchemy import String as SA_String, Integer as SA_Integer, JSON


class ChangeLogMT(SQLModel, table=True):
    """
    Tenant-scoped change log. Use this when multiple tenants share a single database.
    """

    __tablename__ = "change_log_mt"

    id: int | None = Field(default=None, primary_key=True)
    tenant: str = Field(
        sa_column=SQLModelColumn(SA_String(64), nullable=False, index=True)
    )
    table: str = Field(sa_column=SQLModelColumn(SA_String(64), nullable=False))
    pk: int = Field(
        nullable=False
    )  # use BigInteger via SQLModel type adapters if needed
    op: str = Field(sa_column=SQLModelColumn(SA_String(1), nullable=False))
    version: int = Field(sa_column=SQLModelColumn(SA_Integer, nullable=False))
    summary: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=SQLModelColumn(
            JSON,
            nullable=True,
        ),
    )


class SyncStateMT(SQLModel, table=True):
    """
    Tenant-scoped sync watermarks.
    """

    __tablename__ = "sync_state_mt"
    tenant: str = Field(sa_column=SQLModelColumn(SA_String(64), primary_key=True))
    peer_id: str = Field(sa_column=SQLModelColumn(SA_String(64), primary_key=True))
    last_pushed_change_id: int = Field(default=0, nullable=False)
    last_pulled_change_id: int = Field(default=0, nullable=False)
