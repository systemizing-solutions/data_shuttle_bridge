"""Node registry SQLModel table model."""

from typing import Optional

from sqlmodel import SQLModel, Field, Column as SQLModelColumn

from sqlalchemy import Integer, String, UniqueConstraint

MAX_NODE = (1 << 10) - 1


class NodeRegistry(SQLModel, table=True):
    __tablename__ = "node_registry"
    id: Optional[int] = Field(default=None, primary_key=True)
    device_key: str = Field(
        sa_column=SQLModelColumn(
            String(64),
            nullable=False,
            index=True,
        )
    )
    node_id: int = Field(
        sa_column=SQLModelColumn(
            Integer,
            nullable=False,
        )
    )

    __table_args__ = (
        UniqueConstraint("device_key", name="uq_node_registry_device_key"),
        UniqueConstraint("node_id", name="uq_node_registry_node_id"),
    )
