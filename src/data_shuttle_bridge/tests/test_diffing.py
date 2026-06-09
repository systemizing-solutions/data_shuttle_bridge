"""Tests for schema diffing engine."""

import pytest

from data_shuttle_bridge.sql.diffing import (
    DiffRecord,
    DefaultDiffEngine,
    classify_drift,
    compute_likely_renames,
    _similar_enough,
)


class TestDiffRecord:
    def test_to_dict(self):
        rec = DiffRecord(
            kind="add_column", column="email", new_value="string", severity="info"
        )
        d = rec.to_dict()
        assert d["kind"] == "add_column"
        assert d["column"] == "email"
        assert d["new_value"] == "string"
        assert d["severity"] == "info"
        assert d["old_value"] is None

    def test_from_dict(self):
        data = {
            "kind": "type_change",
            "column": "age",
            "old_value": "string",
            "new_value": "integer",
            "severity": "error",
        }
        rec = DiffRecord.from_dict(data)
        assert rec.kind == "type_change"
        assert rec.column == "age"
        assert rec.old_value == "string"
        assert rec.new_value == "integer"

    def test_frozen(self):
        rec = DiffRecord(kind="add_column", column="x")
        with pytest.raises(AttributeError):
            rec.kind = "other"

    def test_roundtrip(self):
        rec = DiffRecord(
            kind="default_change",
            column="status",
            old_value="active",
            new_value="pending",
            severity="info",
        )
        assert DiffRecord.from_dict(rec.to_dict()) == rec


class TestDefaultDiffEngine:
    def setup_method(self):
        self.engine = DefaultDiffEngine()

    def test_root_version_no_diff(self):
        diffs = self.engine.diff(
            None, {"type": "object", "properties": {"id": {"type": "integer"}}}
        )
        assert diffs == []

    def test_add_column(self):
        parent = {"type": "object", "properties": {"id": {"type": "integer"}}}
        child = {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        }
        diffs = self.engine.diff(parent, child)
        assert len(diffs) == 1
        assert diffs[0].kind == "add_column"
        assert diffs[0].column == "name"
        assert diffs[0].new_value == "string"
        assert diffs[0].severity == "info"

    def test_remove_column(self):
        parent = {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "old_col": {"type": "string"}},
        }
        child = {"type": "object", "properties": {"id": {"type": "integer"}}}
        diffs = self.engine.diff(parent, child)
        assert len(diffs) == 1
        assert diffs[0].kind == "remove_column"
        assert diffs[0].column == "old_col"
        assert diffs[0].severity == "warning"

    def test_type_change(self):
        parent = {"type": "object", "properties": {"age": {"type": "string"}}}
        child = {"type": "object", "properties": {"age": {"type": "integer"}}}
        diffs = self.engine.diff(parent, child)
        type_changes = [d for d in diffs if d.kind == "type_change"]
        assert len(type_changes) == 1
        assert type_changes[0].old_value == "string"
        assert type_changes[0].new_value == "integer"
        assert type_changes[0].severity == "error"

    def test_required_change(self):
        parent = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        child = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": [],
        }
        diffs = self.engine.diff(parent, child)
        req_changes = [d for d in diffs if d.kind == "required_change"]
        assert len(req_changes) == 1
        assert req_changes[0].old_value is True
        assert req_changes[0].new_value is False

    def test_default_change(self):
        parent = {
            "type": "object",
            "properties": {"status": {"type": "string", "default": "active"}},
        }
        child = {
            "type": "object",
            "properties": {"status": {"type": "string", "default": "pending"}},
        }
        diffs = self.engine.diff(parent, child)
        default_changes = [d for d in diffs if d.kind == "default_change"]
        assert len(default_changes) == 1
        assert default_changes[0].old_value == "active"
        assert default_changes[0].new_value == "pending"

    def test_multiple_changes(self):
        parent = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "old_col": {"type": "string"},
                "age": {"type": "string"},
            },
            "required": ["id"],
        }
        child = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "new_col": {"type": "boolean"},
                "age": {"type": "integer"},
            },
            "required": [],
        }
        diffs = self.engine.diff(parent, child)
        kinds = {d.kind for d in diffs}
        assert "add_column" in kinds
        assert "remove_column" in kinds
        assert "type_change" in kinds
        assert "required_change" in kinds

    def test_no_changes(self):
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        diffs = self.engine.diff(schema, schema)
        assert diffs == []

    def test_deterministic_ordering(self):
        parent = {
            "type": "object",
            "properties": {"b": {"type": "string"}, "a": {"type": "string"}},
        }
        child = {
            "type": "object",
            "properties": {"b": {"type": "integer"}, "a": {"type": "integer"}},
        }
        diffs = self.engine.diff(parent, child)
        assert diffs[0].column == "a"
        assert diffs[1].column == "b"

    def test_empty_schemas(self):
        parent = {"type": "object", "properties": {}}
        child = {"type": "object", "properties": {}}
        assert self.engine.diff(parent, child) == []

    def test_missing_properties_key(self):
        parent = {"type": "object"}
        child = {"type": "object", "properties": {"x": {"type": "string"}}}
        diffs = self.engine.diff(parent, child)
        assert len(diffs) == 1
        assert diffs[0].kind == "add_column"


class TestClassifyDrift:
    def test_empty(self):
        result = classify_drift([])
        assert result["total_changes"] == 0
        assert result["unresolved_count"] == 0

    def test_mixed_severities(self):
        diffs = [
            DiffRecord(kind="type_change", column="x", severity="error"),
            DiffRecord(kind="remove_column", column="y", severity="warning"),
            DiffRecord(kind="add_column", column="z", severity="info"),
        ]
        result = classify_drift(diffs)
        assert result["total_changes"] == 3
        assert result["unresolved_count"] == 1
        assert result["warning_count"] == 1
        assert result["info_count"] == 1
        assert len(result["unresolved"]) == 1
        assert len(result["warnings"]) == 1
        assert len(result["info"]) == 1


class TestComputeLikelyRenames:
    def test_suffix_rename(self):
        parent = {"properties": {"email": {"type": "string"}}}
        child = {"properties": {"primary_email": {"type": "string"}}}
        renames = compute_likely_renames(parent, child)
        assert len(renames) == 1
        assert renames[0]["old"] == "email"
        assert renames[0]["new"] == "primary_email"

    def test_no_rename(self):
        parent = {"properties": {"x": {"type": "string"}}}
        child = {"properties": {"y": {"type": "string"}}}
        renames = compute_likely_renames(parent, child, threshold=1.0)
        assert renames == []

    def test_empty_schemas(self):
        assert compute_likely_renames({"properties": {}}, {"properties": {}}) == []


class TestSimilarEnough:
    def test_suffix_match(self):
        assert _similar_enough("email", "primary_email", 0.8) is True

    def test_prefix_match(self):
        assert _similar_enough("customer_id", "id", 0.8) is True

    def test_no_match(self):
        assert _similar_enough("abc", "xyz", 0.99) is False

    def test_high_overlap(self):
        assert _similar_enough("user_name", "username", 0.8) is True
