"""Tests for JSON Schema to SQLAlchemy type mapping."""

import pytest
from sqlalchemy import (
    MetaData,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    Time,
    JSON,
    Text,
)

from data_shuttle_bridge.sql.jsonschema_types import (
    sa_type_for_jsonschema,
    build_version_table,
    get_columns_from_schema,
    get_required_columns,
)


class TestSaTypeForJsonSchema:
    def test_string_default(self):
        result = sa_type_for_jsonschema({"type": "string"})
        assert isinstance(result, String)

    def test_string_with_max_length(self):
        result = sa_type_for_jsonschema({"type": "string", "maxLength": 50})
        assert isinstance(result, String)

    def test_string_email_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "email"})
        assert isinstance(result, String)

    def test_string_uri_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "uri"})
        assert isinstance(result, String)

    def test_string_url_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "url"})
        assert isinstance(result, String)

    def test_string_date_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "date"})
        assert result is Date

    def test_string_time_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "time"})
        assert result is Time

    def test_string_datetime_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "date-time"})
        assert result is DateTime

    def test_string_uuid_format(self):
        result = sa_type_for_jsonschema({"type": "string", "format": "uuid"})
        assert isinstance(result, String)

    def test_integer(self):
        assert sa_type_for_jsonschema({"type": "integer"}) is Integer

    def test_number(self):
        assert sa_type_for_jsonschema({"type": "number"}) is Float

    def test_boolean(self):
        assert sa_type_for_jsonschema({"type": "boolean"}) is Boolean

    def test_array(self):
        assert sa_type_for_jsonschema({"type": "array"}) is JSON

    def test_object(self):
        assert sa_type_for_jsonschema({"type": "object"}) is JSON

    def test_null(self):
        result = sa_type_for_jsonschema({"type": "null"})
        assert isinstance(result, String)

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            sa_type_for_jsonschema({"type": "foobar"})

    def test_missing_type_raises(self):
        with pytest.raises(ValueError):
            sa_type_for_jsonschema({})


class TestBuildVersionTable:
    def test_basic_table(self):
        metadata = MetaData()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        table = build_version_table(metadata, table_name="test__v1", schema=schema)
        col_names = [c.name for c in table.columns]
        assert "_id" in col_names
        assert "name" in col_names
        assert "age" in col_names
        assert "_created_at" in col_names
        assert "_updated_at" in col_names

    def test_nullable_columns(self):
        metadata = MetaData()
        schema = {
            "type": "object",
            "properties": {
                "required_col": {"type": "string"},
                "optional_col": {"type": "string"},
            },
            "required": ["required_col"],
        }
        table = build_version_table(metadata, table_name="test__v2", schema=schema)
        for col in table.columns:
            if col.name == "required_col":
                assert col.nullable is False
            elif col.name == "optional_col":
                assert col.nullable is True

    def test_no_surrogate_pk(self):
        metadata = MetaData()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        table = build_version_table(
            metadata, table_name="test__v3", schema=schema, add_surrogate_pk=False
        )
        col_names = [c.name for c in table.columns]
        assert "_id" not in col_names

    def test_no_metadata_columns(self):
        metadata = MetaData()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        table = build_version_table(
            metadata,
            table_name="test__v4",
            schema=schema,
            add_schema_metadata_columns=False,
        )
        col_names = [c.name for c in table.columns]
        assert "_created_at" not in col_names
        assert "_updated_at" not in col_names

    def test_non_object_schema_raises(self):
        metadata = MetaData()
        with pytest.raises(ValueError, match="Only object"):
            build_version_table(metadata, table_name="bad", schema={"type": "array"})

    def test_column_with_default(self):
        metadata = MetaData()
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "active"},
            },
        }
        table = build_version_table(metadata, table_name="test__v5", schema=schema)
        status_col = [c for c in table.columns if c.name == "status"][0]
        assert status_col.default is not None


class TestGetColumnsFromSchema:
    def test_basic(self):
        schema = {"type": "object", "properties": {"a": {}, "b": {}, "c": {}}}
        assert get_columns_from_schema(schema) == ["a", "b", "c"]

    def test_empty_properties(self):
        schema = {"type": "object", "properties": {}}
        assert get_columns_from_schema(schema) == []

    def test_non_object_raises(self):
        with pytest.raises(ValueError, match="Only object"):
            get_columns_from_schema({"type": "array"})


class TestGetRequiredColumns:
    def test_basic(self):
        schema = {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a"]}
        assert get_required_columns(schema) == ["a"]

    def test_no_required(self):
        schema = {"type": "object", "properties": {"a": {}}}
        assert get_required_columns(schema) == []

    def test_non_object_raises(self):
        with pytest.raises(ValueError, match="Only object"):
            get_required_columns({"type": "array"})
