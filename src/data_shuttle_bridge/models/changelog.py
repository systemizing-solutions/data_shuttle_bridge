"""Change log and sync state SQLModel table models."""

from datetime import datetime
from typing import Optional, Dict, Any

from sqlmodel import SQLModel, Field, Column as SQLModelColumn

from sqlalchemy import (
    BigInteger,
    String,
    Integer,
    DateTime,
    JSON,
    UniqueConstraint,
    func,
)


class ChangeLog(SQLModel, table=True):
    __tablename__ = "change_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    table: str = Field(
        sa_column=SQLModelColumn(
            String(64),
            nullable=False,
        )
    )
    pk: int = Field(
        sa_column=SQLModelColumn(
            BigInteger,
            nullable=False,
        )
    )
    op: str = Field(
        sa_column=SQLModelColumn(
            String(1),
            nullable=False,
        )
    )
    version: int = Field(
        sa_column=SQLModelColumn(
            Integer,
            nullable=False,
        )
    )
    node_id: Optional[str] = Field(
        default=None,
        sa_column=SQLModelColumn(
            String(64),
            nullable=True,
        ),
    )
    at: datetime = Field(
        sa_column=SQLModelColumn(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )
    )
    summary: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=SQLModelColumn(
            JSON,
        ),
    )


class SyncState(SQLModel, table=True):
    __tablename__ = "sync_state"

    peer_id: str = Field(
        sa_column=SQLModelColumn(String(64), primary_key=True, nullable=False)
    )
    last_pushed_change_id: int = Field(
        default=0,
        sa_column=SQLModelColumn(
            Integer,
            nullable=False,
        ),
    )
    last_pulled_change_id: int = Field(
        default=0,
        sa_column=SQLModelColumn(
            Integer,
            nullable=False,
        ),
    )

    __table_args__ = (UniqueConstraint("peer_id", name="uq_syncstate_peer"),)
