from typing import Dict, Any, Iterable, Type, Set
from datetime import datetime

from data_shuttle_bridge.models.payloads import TableSchema


def serialize_row(obj: object, include_fields: Iterable[str]) -> Dict[str, Any]:
    d = {}
    for f in include_fields:
        value = getattr(obj, f)
        # Convert datetime objects to ISO format strings for JSON serialization
        if isinstance(value, datetime):
            d[f] = value.isoformat()
        else:
            d[f] = value
    return d


def apply_row(obj: object, data: Dict[str, Any], exclude: Iterable[str] = ()):
    for k, v in data.items():
        if k in exclude:
            continue
        # Convert ISO format datetime strings back to datetime objects
        if isinstance(v, str):
            try:
                # Try to parse as ISO format datetime
                v = datetime.fromisoformat(v)
            except (ValueError, TypeError):
                # Not a datetime, keep original value
                pass
        setattr(obj, k, v)
