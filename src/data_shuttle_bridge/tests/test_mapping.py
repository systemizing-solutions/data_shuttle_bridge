"""Tests for mapping rules engine."""

import json
import pytest
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Float,
    null,
    literal,
)

from data_shuttle_bridge.sql.mapping import (
    RenameRule,
    CastRule,
    ExpressionRule,
    DropRule,
    DefaultMappingRuleEngine,
    parse_mapping_rules_json,
    serialize_mapping_rules,
)


class TestRenameRule:
    def test_to_dict(self):
        rule = RenameRule(src="old_name", dst="new_name")
        d = rule.to_dict()
        assert d == {"kind": "rename", "from": "old_name", "to": "new_name"}

    def test_from_dict(self):
        rule = RenameRule.from_dict({"from": "email", "to": "primary_email"})
        assert rule.src == "email"
        assert rule.dst == "primary_email"

    def test_kind_default(self):
        rule = RenameRule(src="a", dst="b")
        assert rule.kind == "rename"


class TestCastRule:
    def test_to_dict(self):
        rule = CastRule(column="age", target_type="Integer")
        d = rule.to_dict()
        assert d == {"kind": "cast", "column": "age", "target_type": "Integer"}

    def test_from_dict(self):
        rule = CastRule.from_dict({"column": "price", "target_type": "Float"})
        assert rule.column == "price"
        assert rule.target_type == "Float"


class TestExpressionRule:
    def test_to_dict(self):
        rule = ExpressionRule(
            target_column="full_name", sql_expression="first || ' ' || last"
        )
        d = rule.to_dict()
        assert d["kind"] == "expression"
        assert d["target_column"] == "full_name"
        assert d["sql_expression"] == "first || ' ' || last"

    def test_from_dict(self):
        rule = ExpressionRule.from_dict(
            {"target_column": "x", "sql_expression": "y + z"}
        )
        assert rule.target_column == "x"


class TestDropRule:
    def test_to_dict(self):
        rule = DropRule(column="deprecated_col")
        assert rule.to_dict() == {"kind": "drop", "column": "deprecated_col"}

    def test_from_dict(self):
        rule = DropRule.from_dict({"column": "old"})
        assert rule.column == "old"


class TestDefaultMappingRuleEngine:
    @pytest.fixture
    def engine_and_table(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        table = Table(
            "test_v1",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String),
            Column("email", String),
            Column("age", Integer),
        )
        metadata.create_all(engine)
        return engine, table

    def test_no_rules_direct_mapping(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name", "email", "age"],
            rules=[],
            defaults={},
        )
        assert "name" in exprs
        assert "email" in exprs
        assert "age" in exprs

    def test_rename_rule(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        rules = [{"kind": "rename", "from": "email", "to": "primary_email"}]
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name", "primary_email"],
            rules=rules,
            defaults={},
        )
        assert "primary_email" in exprs

    def test_drop_rule(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        rules = [{"kind": "drop", "column": "email"}]
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name", "email"],
            rules=rules,
            defaults={},
        )
        # Dropped column should not be in expressions
        assert "email" not in exprs
        assert "name" in exprs

    def test_missing_column_uses_null_default(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name", "nonexistent"],
            rules=[],
            defaults={},
        )
        assert "nonexistent" in exprs  # Should be NULL

    def test_missing_column_uses_literal_default(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name", "missing_col"],
            rules=[],
            defaults={"missing_col": {"kind": "literal", "value": "unknown"}},
        )
        assert "missing_col" in exprs

    def test_missing_column_uses_schema_default(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["missing"],
            rules=[],
            defaults={"missing": {"kind": "schema_default", "value": 0}},
        )
        assert "missing" in exprs

    def test_missing_column_null_kind(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["missing"],
            rules=[],
            defaults={"missing": {"kind": "null"}},
        )
        assert "missing" in exprs

    def test_missing_column_require_rule_kind(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["missing"],
            rules=[],
            defaults={"missing": {"kind": "require_rule"}},
        )
        assert "missing" in exprs

    def test_cast_rule(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        rules = [{"kind": "cast", "column": "age", "target_type": "String"}]
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["age"],
            rules=rules,
            defaults={},
        )
        assert "age" in exprs

    def test_expression_rule_existing_column(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        rules = [
            {
                "kind": "expression",
                "target_column": "name",
                "sql_expression": "upper(name)",
            }
        ]
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["name"],
            rules=rules,
            defaults={},
        )
        assert "name" in exprs

    def test_expression_rule_missing_column(self, engine_and_table):
        _, table = engine_and_table
        eng = DefaultMappingRuleEngine()
        rules = [
            {"kind": "expression", "target_column": "computed", "sql_expression": "1+1"}
        ]
        exprs = eng.build_projection_expressions(
            version_table=table,
            target_columns=["computed"],
            rules=rules,
            defaults={},
        )
        assert "computed" in exprs

    def test_get_sa_type_valid(self):
        eng = DefaultMappingRuleEngine()
        for type_name in [
            "String",
            "Integer",
            "Float",
            "Boolean",
            "DateTime",
            "Date",
            "Time",
        ]:
            eng._get_sa_type(type_name)

    def test_get_sa_type_invalid(self):
        eng = DefaultMappingRuleEngine()
        with pytest.raises(ValueError, match="Unknown type"):
            eng._get_sa_type("FakeType")

    def test_parse_rules(self):
        eng = DefaultMappingRuleEngine()
        rules_data = [
            {"kind": "rename", "from": "a", "to": "b"},
            {"kind": "cast", "column": "c", "target_type": "Integer"},
            {"kind": "drop", "column": "d"},
            {"kind": "expression", "target_column": "e", "sql_expression": "1"},
        ]
        parsed = eng._parse_rules(rules_data)
        assert len(parsed) == 4
        assert isinstance(parsed[0], RenameRule)
        assert isinstance(parsed[1], CastRule)
        assert isinstance(parsed[2], DropRule)
        assert isinstance(parsed[3], ExpressionRule)

    def test_parse_rules_unknown_kind_skipped(self):
        eng = DefaultMappingRuleEngine()
        parsed = eng._parse_rules([{"kind": "unknown_rule"}])
        assert parsed == []


class TestParseMappingRulesJson:
    def test_empty_string(self):
        assert parse_mapping_rules_json("") == []

    def test_dict_with_rules_key(self):
        data = json.dumps({"rules": [{"kind": "drop", "column": "x"}]})
        result = parse_mapping_rules_json(data)
        assert len(result) == 1
        assert result[0]["kind"] == "drop"

    def test_list_format(self):
        data = json.dumps([{"kind": "rename", "from": "a", "to": "b"}])
        result = parse_mapping_rules_json(data)
        assert len(result) == 1


class TestSerializeMappingRules:
    def test_serialize(self):
        rules = [RenameRule(src="a", dst="b"), DropRule(column="c")]
        result = serialize_mapping_rules(rules)
        parsed = json.loads(result)
        assert len(parsed["rules"]) == 2
        assert parsed["rules"][0]["kind"] == "rename"
        assert parsed["rules"][1]["kind"] == "drop"
