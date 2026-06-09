"""Tests for sync configuration system."""

import json
import tempfile
import os
import pytest

from data_shuttle_bridge.sql.sync_config import (
    SyncScope,
    FilterOperator,
    FilterCondition,
    FilterExpression,
    TableSyncRule,
    SchemaSyncConfig,
    SyncConfig,
)


class TestFilterOperator:
    def test_all_operators_exist(self):
        ops = [
            FilterOperator.EQ,
            FilterOperator.NEQ,
            FilterOperator.LT,
            FilterOperator.LTE,
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.IN,
            FilterOperator.NOT_IN,
            FilterOperator.CONTAINS,
            FilterOperator.NOT_CONTAINS,
            FilterOperator.LIKE,
            FilterOperator.NOT_LIKE,
            FilterOperator.IS_NULL,
            FilterOperator.IS_NOT_NULL,
        ]
        assert len(ops) == 14


class TestFilterCondition:
    def test_from_dict_basic(self):
        data = {"field": "status", "operator": "=", "value": "active"}
        fc = FilterCondition.from_dict(data)
        assert fc.field == "status"
        assert fc.operator == FilterOperator.EQ
        assert fc.value == "active"

    def test_from_dict_with_reference(self):
        data = {
            "field": "company_id",
            "operator": "=",
            "value": None,
            "reference_table": "companies",
            "reference_key": "id",
        }
        fc = FilterCondition.from_dict(data)
        assert fc.reference_table == "companies"
        assert fc.reference_key == "id"

    def test_to_dict(self):
        fc = FilterCondition(field="age", operator=FilterOperator.GTE, value=18)
        d = fc.to_dict()
        assert d["field"] == "age"
        assert d["operator"] == ">="
        assert d["value"] == 18

    def test_roundtrip(self):
        fc = FilterCondition(field="x", operator=FilterOperator.IN, value=[1, 2, 3])
        assert FilterCondition.from_dict(fc.to_dict()).field == fc.field


class TestFilterExpression:
    def test_from_dict_basic(self):
        data = {
            "conditions": [{"field": "status", "operator": "=", "value": "active"}],
            "logic": "AND",
        }
        fe = FilterExpression.from_dict(data)
        assert len(fe.conditions) == 1
        assert fe.logic == "AND"

    def test_from_dict_with_nested(self):
        data = {
            "conditions": [{"field": "a", "operator": "=", "value": 1}],
            "logic": "AND",
            "nested": {
                "conditions": [{"field": "b", "operator": "=", "value": 2}],
                "logic": "OR",
            },
        }
        fe = FilterExpression.from_dict(data)
        assert fe.nested is not None
        assert fe.nested.logic == "OR"

    def test_to_dict(self):
        fe = FilterExpression(
            conditions=[
                FilterCondition(field="x", operator=FilterOperator.EQ, value=1)
            ],
            logic="OR",
        )
        d = fe.to_dict()
        assert d["logic"] == "OR"
        assert len(d["conditions"]) == 1
        assert "nested" not in d  # No nested

    def test_to_dict_with_nested(self):
        inner = FilterExpression(conditions=[], logic="OR")
        outer = FilterExpression(conditions=[], logic="AND", nested=inner)
        d = outer.to_dict()
        assert "nested" in d
        assert d["nested"]["logic"] == "OR"

    def test_default_logic(self):
        fe = FilterExpression.from_dict({"conditions": []})
        assert fe.logic == "AND"


class TestTableSyncRule:
    def test_from_dict_basic(self):
        data = {"enabled": True}
        rule = TableSyncRule.from_dict(data)
        assert rule.enabled is True
        assert rule.filter is None
        assert rule.exclude_columns == []
        assert rule.include_only_columns is None

    def test_from_dict_with_filter(self):
        data = {
            "enabled": True,
            "filter": {
                "conditions": [{"field": "status", "operator": "=", "value": "active"}],
                "logic": "AND",
            },
        }
        rule = TableSyncRule.from_dict(data)
        assert rule.filter is not None
        assert len(rule.filter.conditions) == 1

    def test_from_dict_with_columns(self):
        data = {
            "exclude_columns": ["password"],
            "include_only_columns": ["id", "name", "email"],
        }
        rule = TableSyncRule.from_dict(data)
        assert "password" in rule.exclude_columns
        assert rule.include_only_columns == ["id", "name", "email"]

    def test_to_dict(self):
        rule = TableSyncRule(enabled=True, exclude_columns=["secret"])
        d = rule.to_dict()
        assert d["enabled"] is True
        assert d["exclude_columns"] == ["secret"]

    def test_to_dict_with_filter(self):
        rule = TableSyncRule(
            filter=FilterExpression(
                conditions=[
                    FilterCondition(field="x", operator=FilterOperator.EQ, value=1)
                ]
            )
        )
        d = rule.to_dict()
        assert "filter" in d

    def test_default_enabled(self):
        rule = TableSyncRule()
        assert rule.enabled is True


class TestSchemaSyncConfig:
    def test_include_all_tables(self):
        ssc = SchemaSyncConfig(name="public", include_all_tables=True)
        assert ssc.is_table_included("any_table") is True

    def test_specific_tables(self):
        ssc = SchemaSyncConfig(
            name="public",
            include_all_tables=False,
            tables={"users": True, "orders": True},
        )
        assert ssc.is_table_included("users") is True
        assert ssc.is_table_included("secrets") is False

    def test_no_tables_config(self):
        ssc = SchemaSyncConfig(name="public", include_all_tables=False)
        assert ssc.is_table_included("anything") is False


class TestSyncConfig:
    def test_database_scope_syncs_all(self):
        config = SyncConfig(scope=SyncScope.DATABASE)
        assert config.is_table_synced("any_table") is True

    def test_disabled_syncs_nothing(self):
        config = SyncConfig(scope=SyncScope.DATABASE, enabled=False)
        assert config.is_table_synced("any_table") is False

    def test_tables_scope(self):
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "users": TableSyncRule(enabled=True),
                "orders": TableSyncRule(enabled=False),
            },
        )
        assert config.is_table_synced("users") is True
        assert config.is_table_synced("orders") is False
        assert config.is_table_synced("other") is False

    def test_filtered_scope(self):
        config = SyncConfig(
            scope=SyncScope.FILTERED,
            tables={"users": TableSyncRule(enabled=True)},
        )
        assert config.is_table_synced("users") is True
        assert config.is_table_synced("other") is False

    def test_schema_scope(self):
        config = SyncConfig(
            scope=SyncScope.SCHEMA,
            schemas=[SchemaSyncConfig(name="public", include_all_tables=True)],
        )
        assert config.is_table_synced("users", schema_name="public") is True
        assert config.is_table_synced("users", schema_name="private") is False
        assert config.is_table_synced("users") is False  # No schema name

    def test_schema_scope_no_schemas(self):
        # schema scope with no schemas config returns False
        config = SyncConfig.__new__(SyncConfig)
        config.scope = SyncScope.SCHEMA
        config.schemas = None
        config.tables = {}
        config.enabled = True
        assert config.is_table_synced("x", schema_name="public") is False

    def test_get_table_rule(self):
        rule = TableSyncRule(enabled=True, exclude_columns=["pw"])
        config = SyncConfig(scope=SyncScope.TABLES, tables={"users": rule})
        assert config.get_table_rule("users") is rule
        assert config.get_table_rule("other") is None

    def test_from_dict(self):
        data = {
            "scope": "tables",
            "enabled": True,
            "tables": {
                "users": {"enabled": True, "exclude_columns": ["password"]},
                "orders": True,
                "items": "default",
            },
        }
        config = SyncConfig.from_dict(data)
        assert config.scope == SyncScope.TABLES
        assert config.tables["users"].exclude_columns == ["password"]
        assert config.tables["orders"].enabled is True
        assert "items" in config.tables

    def test_from_dict_with_schemas(self):
        data = {
            "scope": "schema",
            "schemas": [{"name": "public", "include_all_tables": True}],
        }
        config = SyncConfig.from_dict(data)
        assert len(config.schemas) == 1
        assert config.schemas[0].name == "public"

    def test_to_dict(self):
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={"users": TableSyncRule(enabled=True)},
        )
        d = config.to_dict()
        assert d["scope"] == "tables"
        assert "users" in d["tables"]

    def test_to_dict_with_schemas(self):
        config = SyncConfig(
            scope=SyncScope.SCHEMA,
            schemas=[SchemaSyncConfig(name="public")],
        )
        d = config.to_dict()
        assert "schemas" in d
        assert d["schemas"][0]["name"] == "public"

    def test_validation_schema_scope_no_schemas_raises(self):
        with pytest.raises(ValueError, match="Must provide schemas"):
            SyncConfig(scope=SyncScope.SCHEMA)

    def test_validation_tables_scope_no_tables_raises(self):
        with pytest.raises(ValueError, match="Must provide tables"):
            SyncConfig(scope=SyncScope.TABLES)

    def test_validation_filtered_scope_no_tables_raises(self):
        with pytest.raises(ValueError, match="Must provide tables"):
            SyncConfig(scope=SyncScope.FILTERED)

    def test_from_file_yaml(self):
        data = {"scope": "database", "enabled": True}
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            import yaml

            yaml.dump(data, f)
            f.flush()
            config = SyncConfig.from_file(f.name)
        os.unlink(f.name)
        assert config.scope == SyncScope.DATABASE

    def test_from_file_json(self):
        data = {"scope": "database", "enabled": True}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = SyncConfig.from_file(f.name)
        os.unlink(f.name)
        assert config.scope == SyncScope.DATABASE

    def test_from_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            SyncConfig.from_file("/nonexistent/path.yaml")

    def test_from_file_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello")
            f.flush()
        with pytest.raises(ValueError, match="Unsupported file format"):
            SyncConfig.from_file(f.name)
        os.unlink(f.name)

    def test_to_file_yaml(self):
        config = SyncConfig(scope=SyncScope.DATABASE)
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        config.to_file(path, format="yaml")
        loaded = SyncConfig.from_file(path)
        os.unlink(path)
        assert loaded.scope == SyncScope.DATABASE

    def test_to_file_json(self):
        config = SyncConfig(scope=SyncScope.DATABASE)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        config.to_file(path, format="json")
        loaded = SyncConfig.from_file(path)
        os.unlink(path)
        assert loaded.scope == SyncScope.DATABASE

    def test_to_file_unsupported_format(self):
        config = SyncConfig(scope=SyncScope.DATABASE)
        with pytest.raises(ValueError, match="Unsupported format"):
            config.to_file("/tmp/test.txt", format="xml")
