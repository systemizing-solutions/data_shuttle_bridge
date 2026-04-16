"""Re-exports from data_shuttle_bridge.models.mixins for backward compatibility."""

from data_shuttle_bridge.models.mixins import (
    SyncRowSQLModelMixin,
    SyncRowSAMixin,
    _get_next_id,
    _get_utc_now,
)

__all__ = ["SyncRowSQLModelMixin", "SyncRowSAMixin"]
