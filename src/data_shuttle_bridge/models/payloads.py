"""Payload data models."""

from typing import Dict, Any, Iterable, Type, Set
from datetime import datetime


class TableSchema:
    def __init__(
        self, model: Type, fields: Iterable[str], parents: Iterable[str] | None = None
    ):
        self.model = model
        self.fields = list(fields)
        self.parents: Set[str] = set(parents or [])
