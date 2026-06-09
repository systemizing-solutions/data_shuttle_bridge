"""Tests for drift policy engine."""

import pytest

from data_shuttle_bridge.sql.policy import (
    ColumnDefault,
    DefaultDriftPolicy,
    StrictDriftPolicy,
    FillDefaultsPolicy,
)


class TestColumnDefault:
    def test_to_dict(self):
        cd = ColumnDefault(kind="null")
        assert cd.to_dict() == {"kind": "null", "value": None}

    def test_to_dict_with_value(self):
        cd = ColumnDefault(kind="literal", value=42)
        assert cd.to_dict() == {"kind": "literal", "value": 42}

    def test_frozen(self):
        cd = ColumnDefault(kind="null")
        with pytest.raises(AttributeError):
            cd.kind = "other"


class TestDefaultDriftPolicy:
    def setup_method(self):
        self.policy = DefaultDriftPolicy()

    def test_existing_column_no_default(self):
        schema = {"properties": {"name": {"type": "string"}}}
        defaults = self.policy.defaults_for_view(["name"], schema)
        assert defaults["name"].kind == "null"

    def test_existing_column_with_schema_default(self):
        schema = {"properties": {"status": {"type": "string", "default": "active"}}}
        defaults = self.policy.defaults_for_view(["status"], schema)
        assert defaults["status"].kind == "schema_default"
        assert defaults["status"].value == "active"

    def test_missing_column(self):
        schema = {"properties": {"name": {"type": "string"}}}
        defaults = self.policy.defaults_for_view(["name", "missing"], schema)
        assert defaults["missing"].kind == "null"

    def test_all_missing(self):
        schema = {"properties": {}}
        defaults = self.policy.defaults_for_view(["a", "b"], schema)
        assert all(d.kind == "null" for d in defaults.values())

    def test_empty_target_columns(self):
        schema = {"properties": {"x": {"type": "string"}}}
        defaults = self.policy.defaults_for_view([], schema)
        assert defaults == {}


class TestStrictDriftPolicy:
    def setup_method(self):
        self.policy = StrictDriftPolicy()

    def test_existing_column(self):
        schema = {"properties": {"name": {"type": "string"}}}
        defaults = self.policy.defaults_for_view(["name"], schema)
        assert defaults["name"].kind == "null"

    def test_existing_column_with_default(self):
        schema = {"properties": {"count": {"type": "integer", "default": 0}}}
        defaults = self.policy.defaults_for_view(["count"], schema)
        assert defaults["count"].kind == "schema_default"
        assert defaults["count"].value == 0

    def test_removed_column_requires_rule(self):
        schema = {"properties": {}}
        diff_records = [{"kind": "remove_column", "column": "old_col"}]
        defaults = self.policy.defaults_for_view(["old_col"], schema, diff_records)
        assert defaults["old_col"].kind == "require_rule"

    def test_missing_column_no_diff(self):
        schema = {"properties": {}}
        defaults = self.policy.defaults_for_view(["new_col"], schema)
        assert defaults["new_col"].kind == "null"

    def test_none_diff_records(self):
        schema = {"properties": {}}
        defaults = self.policy.defaults_for_view(["col"], schema, None)
        assert defaults["col"].kind == "null"


class TestFillDefaultsPolicy:
    def test_fill_with_defaults(self):
        policy = FillDefaultsPolicy(fill_defaults={"color": "red", "size": 10})
        schema = {"properties": {"name": {"type": "string"}}}
        defaults = policy.defaults_for_view(["name", "color", "size"], schema)
        assert defaults["name"].kind == "null"
        assert defaults["color"].kind == "literal"
        assert defaults["color"].value == "red"
        assert defaults["size"].kind == "literal"
        assert defaults["size"].value == 10

    def test_no_fill_defaults(self):
        policy = FillDefaultsPolicy()
        schema = {"properties": {}}
        defaults = policy.defaults_for_view(["missing"], schema)
        assert defaults["missing"].kind == "null"

    def test_existing_with_schema_default_takes_precedence(self):
        policy = FillDefaultsPolicy(fill_defaults={"status": "override"})
        schema = {"properties": {"status": {"type": "string", "default": "active"}}}
        defaults = policy.defaults_for_view(["status"], schema)
        assert defaults["status"].kind == "schema_default"
        assert defaults["status"].value == "active"

    def test_existing_column_no_default(self):
        policy = FillDefaultsPolicy(fill_defaults={"name": "N/A"})
        schema = {"properties": {"name": {"type": "string"}}}
        defaults = policy.defaults_for_view(["name"], schema)
        # Column exists in schema, so no fill default
        assert defaults["name"].kind == "null"
