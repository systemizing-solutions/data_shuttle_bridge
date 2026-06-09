import threading
from datetime import datetime
from typing import Type, Iterable, Optional

from sqlmodel import SQLModel

from sqlalchemy import event

from data_shuttle_bridge.sql.changelog import ChangeLog

# Track models that already have hooks attached to prevent duplicate registration
_hooked_models: set = set()

# Thread-local storage for current node_id during sync operations
_current_node_id: threading.local = threading.local()


def set_current_node_id(node_id: Optional[str]) -> None:
    """Set the current node_id for change logging."""
    _current_node_id.value = node_id


def get_current_node_id() -> Optional[str]:
    """Get the current node_id for change logging."""
    return getattr(_current_node_id, "value", None)


def _summary(obj, keys=("updated_at", "deleted_at", "version")):
    out = {}
    for k in keys:
        if hasattr(obj, k):
            value = getattr(obj, k)
            # Convert datetime objects to ISO format strings for JSON serialization
            if isinstance(value, datetime):
                out[k] = value.isoformat()
            else:
                out[k] = value
    return out


def _log(connection, table: str, pk: int, op: str, version: int, summary):
    connection.execute(
        ChangeLog.__table__.insert().values(
            table=table,
            pk=pk,
            op=op,
            version=version,
            summary=summary,
            node_id=get_current_node_id(),
        )
    )


def attach_change_hooks(model: Type, table_name: str):
    if id(model) in _hooked_models:
        return
    _hooked_models.add(id(model))

    @event.listens_for(model, "before_update", propagate=True)
    def _bump_version(mapper, connection, target):
        # Check if any actual data changed (not just updated_at)
        from sqlalchemy.orm import attributes

        # Get the session history for modified attributes
        has_real_changes = False
        for attr in mapper.attrs:
            if attr.key in ("updated_at", "version", "deleted_at", "id"):
                # Skip system fields
                continue
            # history is (added, unchanged, deleted) tuple - if added or deleted is non-empty, it changed
            history = attributes.get_history(target, attr.key)
            if history.added or history.deleted:
                has_real_changes = True
                break

        if has_real_changes:
            cur = getattr(target, "version", 1)
            try:
                next_v = int(cur) + 1
            except Exception:
                next_v = 1
            setattr(target, "version", next_v)

    @event.listens_for(model, "after_insert")
    def _after_insert(mapper, connection, target):
        _log(
            connection,
            table_name,
            int(getattr(target, "id")),
            "I",
            int(getattr(target, "version", 1)),
            _summary(target),
        )

    @event.listens_for(model, "after_update")
    def _after_update(mapper, connection, target):
        # Check if any real data changed (not just updated_at)
        from sqlalchemy.orm import attributes

        # Get the session history for modified attributes
        has_real_changes = False
        for attr in mapper.attrs:
            if attr.key in ("updated_at", "version", "deleted_at", "id"):
                # Skip system fields
                continue
            # history is (added, unchanged, deleted) tuple - if added or deleted is non-empty, it changed
            history = attributes.get_history(target, attr.key)
            if history.added or history.deleted:
                has_real_changes = True
                break

        # Only log if actual data changed
        if has_real_changes:
            _log(
                connection,
                table_name,
                int(getattr(target, "id")),
                "U",
                int(getattr(target, "version", 1)),
                _summary(target),
            )

    @event.listens_for(model, "after_delete")
    def _after_delete(mapper, connection, target):
        _log(
            connection,
            table_name,
            int(getattr(target, "id")),
            "D",
            int(getattr(target, "version", 1)),
            None,
        )


def attach_change_hooks_for_models(models: Iterable[Type]):
    for m in models:
        table = getattr(m, "__table__", None)
        table_name = getattr(m, "__tablename__", None) or (
            table.name if table is not None else None
        )
        if not table_name:
            raise ValueError(f"Model {m} has no table mapping")
        attach_change_hooks(m, table_name)
