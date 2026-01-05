from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, Column as SQLModelColumn

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import declared_attr
from sqlalchemy.orm import declarative_mixin
from sqlalchemy import (
    Column as SAColumn,
    DateTime as SA_DateTime,
    Integer as SA_Integer,
)
from sqlalchemy.sql import func as sa_func


def _get_next_id():
    """Get next ID from the global ID generator."""
    from .ids import get_id_generator

    return get_id_generator()()


def _get_utc_now():
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class SyncRowSQLModelMixin(SQLModel):
    id: int = Field(
        default_factory=_get_next_id,
        sa_type=Integer,
        sa_column_kwargs={"primary_key": True},
    )

    updated_at: datetime = Field(
        default_factory=_get_utc_now,  # Python-side default (timezone-aware)
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "nullable": False,
            "server_default": func.now(),  # DB-side default
            "onupdate": func.now(),  # DB-side auto-update
        },
    )

    version: int = Field(
        default=1,
        sa_type=Integer,
        sa_column_kwargs={"nullable": False},
    )

    deleted_at: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    @declared_attr.directive
    def __mapper_args__(cls):
        return {}

    @declared_attr
    def __table_args__(cls):
        return {}


@declarative_mixin
class SyncRowSAMixin:
    id = SAColumn(
        SA_Integer,
        primary_key=True,
        default=_get_next_id,
    )
    updated_at = SAColumn(
        SA_DateTime(timezone=True),
        server_default=sa_func.now(),
        onupdate=sa_func.now(),
        nullable=False,
    )
    version = SAColumn(
        SA_Integer,
        nullable=False,
        default=1,
    )
    deleted_at = SAColumn(
        SA_DateTime(timezone=True),
        nullable=True,
    )
