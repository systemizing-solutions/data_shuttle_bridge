"""Enumeration types for data_shuttle_bridge."""

from enum import Enum


class ConflictPolicy(str, Enum):
    LWW = "last_write_wins"
    VERSION = "version_strict"


class SyncScope(str, Enum):
    """Scope level for what to sync."""

    DATABASE = "database"  # Sync entire database
    SCHEMA = "schema"  # Sync entire schema(s)
    TABLES = "tables"  # Sync specific tables
    FILTERED = "filtered"  # Sync specific tables with row filters


class FilterOperator(str, Enum):
    """Operators for row-level filters."""

    EQ = "="
    NEQ = "!="
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    LIKE = "like"
    NOT_LIKE = "not_like"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
