"""Tests for SchemaRegistry."""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session

from data_shuttle_bridge.sql.schema_registry import SchemaRegistry
from data_shuttle_bridge.sql.versioning_models import (
    SchemaSet,
    SchemaVersion,
    SchemaDiff,
    MappingRule,
    ConsolidationView,
    create_all_tables,
)


VALID_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name"],
}

VALID_SCHEMA_V2 = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "email": {"type": "string"},
    },
    "required": ["name"],
}


@pytest.fixture
def registry_session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    create_all_tables(engine)
    sess = Session(engine)
    registry = SchemaRegistry(engine)
    yield registry, sess
    sess.close()


class TestSchemaRegistryCreateSchemaSet:
    def test_create(self, registry_session):
        reg, sess = registry_session
        ss = reg.create_schema_set(sess, "customer", "Customer", "Customer schema")
        assert ss.key == "customer"
        assert ss.name == "Customer"
        assert ss.description == "Customer schema"
        assert ss.id is not None

    def test_duplicate_key_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "dup", "Dup")
        with pytest.raises(ValueError, match="already exists"):
            reg.create_schema_set(sess, "dup", "Dup Again")


class TestSchemaRegistryAddVersion:
    def test_add_first_version(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        sv = reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        assert sv.version == 1
        assert sv.table_name == "test__v1"
        assert sv.parent_version_id is None

    def test_add_version_with_parent(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        sv2 = reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2, parent_version=1)
        assert sv2.version == 2
        assert sv2.parent_version_id is not None

    def test_add_version_creates_diff(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2, parent_version=1)

        diff = reg.get_schema_diff(sess, "test", 1, 2)
        assert diff is not None
        assert "records" in diff
        assert "classification" in diff

    def test_duplicate_version_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        with pytest.raises(ValueError, match="already exists"):
            reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)

    def test_nonexistent_schema_set_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="not found"):
            reg.add_schema_version(sess, "nope", 1, VALID_SCHEMA_V1)

    def test_nonexistent_parent_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        with pytest.raises(ValueError, match="Parent version"):
            reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2, parent_version=99)

    def test_json_string_schema(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        sv = reg.add_schema_version(sess, "test", 1, json.dumps(VALID_SCHEMA_V1))
        assert sv.version == 1

    def test_invalid_json_string_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        with pytest.raises(ValueError, match="Invalid JSON"):
            reg.add_schema_version(sess, "test", 1, "{bad json")


class TestSchemaRegistryValidation:
    def test_valid_schema(self, registry_session):
        reg, sess = registry_session
        reg._validate_schema(VALID_SCHEMA_V1)

    def test_not_dict_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="must be a dict"):
            reg._validate_schema("not a dict")

    def test_not_object_type_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="type 'object'"):
            reg._validate_schema({"type": "array", "properties": {}})

    def test_no_properties_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="must have 'properties'"):
            reg._validate_schema({"type": "object"})


class TestSchemaRegistryValidatePayload:
    def test_valid_payload(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        assert (
            reg.validate_payload(sess, "test", 1, {"name": "Alice", "age": 30}) is True
        )

    def test_invalid_payload_raises(self, registry_session):
        from jsonschema import ValidationError

        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        with pytest.raises(ValidationError):
            reg.validate_payload(sess, "test", 1, {"age": "not_an_int"})

    def test_nonexistent_version_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        with pytest.raises(ValueError, match="not found"):
            reg.validate_payload(sess, "test", 99, {"name": "test"})


class TestSchemaRegistryIngestData:
    def test_ingest(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        row_id = reg.ingest_data(sess, "test", 1, {"name": "Alice", "age": 30})
        assert row_id is not None

    def test_ingest_invalid_raises(self, registry_session):
        from jsonschema import ValidationError

        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        with pytest.raises(ValidationError):
            reg.ingest_data(sess, "test", 1, {"age": "bad"})


class TestSchemaRegistryGetDiff:
    def test_get_diff(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2, parent_version=1)
        diff = reg.get_schema_diff(sess, "test", 1, 2)
        assert diff is not None

    def test_get_diff_not_found(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2)
        # No parent link, so no diff
        diff = reg.get_schema_diff(sess, "test", 1, 2)
        assert diff is None

    def test_get_diff_nonexistent_set_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="not found"):
            reg.get_schema_diff(sess, "nope", 1, 2)

    def test_get_diff_nonexistent_version_raises(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        with pytest.raises(ValueError, match="not found"):
            reg.get_schema_diff(sess, "test", 1, 2)


class TestSchemaRegistryListOps:
    def test_list_schema_sets(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "a", "A")
        reg.create_schema_set(sess, "b", "B")
        sets = reg.list_schema_sets(sess)
        assert len(sets) == 2
        keys = [s.key for s in sets]
        assert "a" in keys
        assert "b" in keys

    def test_list_schema_versions(self, registry_session):
        reg, sess = registry_session
        reg.create_schema_set(sess, "test", "Test")
        reg.add_schema_version(sess, "test", 1, VALID_SCHEMA_V1)
        reg.add_schema_version(sess, "test", 2, VALID_SCHEMA_V2, parent_version=1)
        versions = reg.list_schema_versions(sess, "test")
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2

    def test_list_schema_versions_nonexistent_raises(self, registry_session):
        reg, sess = registry_session
        with pytest.raises(ValueError, match="not found"):
            reg.list_schema_versions(sess, "nope")
