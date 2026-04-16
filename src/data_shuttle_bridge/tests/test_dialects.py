"""Tests for dialect adapters."""

import pytest
from sqlalchemy import Column, Integer, String, Float, literal_column, cast
from sqlalchemy.types import TypeEngine

from data_shuttle_bridge.sql.dialects import (
    PostgresDialect,
    SQLiteDialect,
    MySQLDialect,
)


class TestPostgresDialect:
    def test_coerce_expression(self):
        dialect = PostgresDialect()
        expr = literal_column("'42'")
        result = dialect.coerce_expression(expr, Integer())
        assert result is not None

    def test_coerce_to_string(self):
        dialect = PostgresDialect()
        expr = literal_column("42")
        result = dialect.coerce_expression(expr, String())
        assert result is not None

    def test_coerce_to_float(self):
        dialect = PostgresDialect()
        expr = literal_column("'3.14'")
        result = dialect.coerce_expression(expr, Float())
        assert result is not None


class TestSQLiteDialect:
    def test_coerce_expression(self):
        dialect = SQLiteDialect()
        expr = literal_column("'42'")
        result = dialect.coerce_expression(expr, Integer())
        assert result is not None

    def test_coerce_to_string(self):
        dialect = SQLiteDialect()
        expr = literal_column("42")
        result = dialect.coerce_expression(expr, String())
        assert result is not None


class TestMySQLDialect:
    def test_coerce_expression(self):
        dialect = MySQLDialect()
        expr = literal_column("'42'")
        result = dialect.coerce_expression(expr, Integer())
        assert result is not None

    def test_coerce_to_float(self):
        dialect = MySQLDialect()
        expr = literal_column("'3.14'")
        result = dialect.coerce_expression(expr, Float())
        assert result is not None
