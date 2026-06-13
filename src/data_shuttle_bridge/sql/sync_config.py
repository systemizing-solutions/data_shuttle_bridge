"""Re-exports from data_shuttle_bridge.models for backward compatibility."""

from data_shuttle_bridge.models.enums import SyncScope, FilterOperator
from data_shuttle_bridge.models.sync_config import (
    FilterCondition,
    FilterExpression,
    TableSyncRule,
    SchemaSyncConfig,
    SyncConfig,
)

SqlFilter = SyncConfig

__all__ = [
    "SyncScope",
    "FilterOperator",
    "FilterCondition",
    "FilterExpression",
    "TableSyncRule",
    "SchemaSyncConfig",
    "SyncConfig",
    "SqlFilter",
]
