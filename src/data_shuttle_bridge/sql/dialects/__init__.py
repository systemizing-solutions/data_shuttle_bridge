"""Dialect adapters for database-specific SQL generation."""

from abc import ABC, abstractmethod
from sqlalchemy import cast, Column
from sqlalchemy.types import TypeEngine


class DialectAdapter(ABC):
    """Base class for database dialect adapters."""

    @abstractmethod
    def coerce_expression(self, expr, target_sa_type: TypeEngine):
        """
        Return a dialect-appropriate cast/coercion expression.

        Args:
            expr: SQLAlchemy expression
            target_sa_type: Target SQLAlchemy type

        Returns:
            Coerced expression
        """


class PostgresDialect(DialectAdapter):
    """Postgres-specific SQL helpers."""

    def coerce_expression(self, expr, target_sa_type: TypeEngine):
        """Cast using Postgres CAST syntax."""
        return cast(expr, target_sa_type)


class SQLiteDialect(DialectAdapter):
    """SQLite-specific SQL helpers."""

    def coerce_expression(self, expr, target_sa_type: TypeEngine):
        """
        Cast using SQLite CAST syntax.
        Note: SQLite has limited type support; may need workarounds.
        """
        return cast(expr, target_sa_type)


class MySQLDialect(DialectAdapter):
    """MySQL-specific SQL helpers."""

    def coerce_expression(self, expr, target_sa_type: TypeEngine):
        """Cast using MySQL CAST syntax."""
        return cast(expr, target_sa_type)
