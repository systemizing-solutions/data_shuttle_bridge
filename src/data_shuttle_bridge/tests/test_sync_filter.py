"""Tests for sync filter evaluation engine."""

import pytest

from data_shuttle_bridge.sql.sync_config import (
    FilterCondition,
    FilterExpression,
    FilterOperator,
)
from data_shuttle_bridge.sql.sync_filter import (
    RowFilterEvaluator,
    FilterEvaluationError,
    ReferenceTableLookup,
)


class TestRowFilterEvaluatorConditions:
    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_eq(self):
        cond = FilterCondition(
            field="status", operator=FilterOperator.EQ, value="active"
        )
        assert self.evaluator.evaluate_condition(cond, {"status": "active"}) is True
        assert self.evaluator.evaluate_condition(cond, {"status": "inactive"}) is False

    def test_neq(self):
        cond = FilterCondition(
            field="status", operator=FilterOperator.NEQ, value="deleted"
        )
        assert self.evaluator.evaluate_condition(cond, {"status": "active"}) is True
        assert self.evaluator.evaluate_condition(cond, {"status": "deleted"}) is False

    def test_lt(self):
        cond = FilterCondition(field="age", operator=FilterOperator.LT, value=18)
        assert self.evaluator.evaluate_condition(cond, {"age": 10}) is True
        assert self.evaluator.evaluate_condition(cond, {"age": 20}) is False

    def test_lte(self):
        cond = FilterCondition(field="age", operator=FilterOperator.LTE, value=18)
        assert self.evaluator.evaluate_condition(cond, {"age": 18}) is True
        assert self.evaluator.evaluate_condition(cond, {"age": 19}) is False

    def test_gt(self):
        cond = FilterCondition(field="score", operator=FilterOperator.GT, value=50)
        assert self.evaluator.evaluate_condition(cond, {"score": 51}) is True
        assert self.evaluator.evaluate_condition(cond, {"score": 50}) is False

    def test_gte(self):
        cond = FilterCondition(field="score", operator=FilterOperator.GTE, value=50)
        assert self.evaluator.evaluate_condition(cond, {"score": 50}) is True
        assert self.evaluator.evaluate_condition(cond, {"score": 49}) is False

    def test_in(self):
        cond = FilterCondition(
            field="role", operator=FilterOperator.IN, value=["admin", "editor"]
        )
        assert self.evaluator.evaluate_condition(cond, {"role": "admin"}) is True
        assert self.evaluator.evaluate_condition(cond, {"role": "viewer"}) is False

    def test_not_in(self):
        cond = FilterCondition(
            field="role", operator=FilterOperator.NOT_IN, value=["banned"]
        )
        assert self.evaluator.evaluate_condition(cond, {"role": "admin"}) is True
        assert self.evaluator.evaluate_condition(cond, {"role": "banned"}) is False

    def test_contains(self):
        cond = FilterCondition(
            field="name", operator=FilterOperator.CONTAINS, value="john"
        )
        assert self.evaluator.evaluate_condition(cond, {"name": "John Doe"}) is True
        assert self.evaluator.evaluate_condition(cond, {"name": "Jane Doe"}) is False

    def test_not_contains(self):
        cond = FilterCondition(
            field="name", operator=FilterOperator.NOT_CONTAINS, value="admin"
        )
        assert self.evaluator.evaluate_condition(cond, {"name": "user123"}) is True
        assert self.evaluator.evaluate_condition(cond, {"name": "admin_user"}) is False

    def test_is_null(self):
        cond = FilterCondition(field="deleted_at", operator=FilterOperator.IS_NULL)
        assert self.evaluator.evaluate_condition(cond, {"deleted_at": None}) is True
        assert (
            self.evaluator.evaluate_condition(cond, {"deleted_at": "2024-01-01"})
            is False
        )

    def test_is_not_null(self):
        cond = FilterCondition(field="email", operator=FilterOperator.IS_NOT_NULL)
        assert self.evaluator.evaluate_condition(cond, {"email": "a@b.com"}) is True
        assert self.evaluator.evaluate_condition(cond, {"email": None}) is False

    def test_like(self):
        cond = FilterCondition(
            field="name", operator=FilterOperator.LIKE, value="John%"
        )
        assert self.evaluator.evaluate_condition(cond, {"name": "John Doe"}) is True
        assert self.evaluator.evaluate_condition(cond, {"name": "Jane Doe"}) is False

    def test_not_like(self):
        cond = FilterCondition(
            field="name", operator=FilterOperator.NOT_LIKE, value="John%"
        )
        assert self.evaluator.evaluate_condition(cond, {"name": "Jane Doe"}) is True
        assert self.evaluator.evaluate_condition(cond, {"name": "John Doe"}) is False

    def test_in_with_non_list_returns_false(self):
        cond = FilterCondition(
            field="x", operator=FilterOperator.IN, value="not_a_list"
        )
        assert self.evaluator.evaluate_condition(cond, {"x": "a"}) is False

    def test_not_in_with_non_list_returns_true(self):
        cond = FilterCondition(
            field="x", operator=FilterOperator.NOT_IN, value="not_a_list"
        )
        assert self.evaluator.evaluate_condition(cond, {"x": "a"}) is True

    def test_contains_with_none_value(self):
        cond = FilterCondition(
            field="name", operator=FilterOperator.CONTAINS, value="x"
        )
        assert self.evaluator.evaluate_condition(cond, {"name": None}) is False

    def test_missing_field_returns_none(self):
        cond = FilterCondition(field="nonexistent", operator=FilterOperator.IS_NULL)
        assert self.evaluator.evaluate_condition(cond, {}) is True


class TestRowFilterEvaluatorExpressions:
    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_and_logic(self):
        expr = FilterExpression(
            conditions=[
                FilterCondition(
                    field="status", operator=FilterOperator.EQ, value="active"
                ),
                FilterCondition(field="age", operator=FilterOperator.GTE, value=18),
            ],
            logic="AND",
        )
        assert (
            self.evaluator.evaluate_expression(expr, {"status": "active", "age": 20})
            is True
        )
        assert (
            self.evaluator.evaluate_expression(expr, {"status": "active", "age": 10})
            is False
        )

    def test_or_logic(self):
        expr = FilterExpression(
            conditions=[
                FilterCondition(
                    field="role", operator=FilterOperator.EQ, value="admin"
                ),
                FilterCondition(
                    field="role", operator=FilterOperator.EQ, value="editor"
                ),
            ],
            logic="OR",
        )
        assert self.evaluator.evaluate_expression(expr, {"role": "admin"}) is True
        assert self.evaluator.evaluate_expression(expr, {"role": "editor"}) is True
        assert self.evaluator.evaluate_expression(expr, {"role": "viewer"}) is False

    def test_empty_conditions_and(self):
        expr = FilterExpression(conditions=[], logic="AND")
        assert self.evaluator.evaluate_expression(expr, {}) is True

    def test_empty_conditions_or(self):
        expr = FilterExpression(conditions=[], logic="OR")
        assert self.evaluator.evaluate_expression(expr, {}) is False

    def test_nested_expression(self):
        inner = FilterExpression(
            conditions=[
                FilterCondition(field="vip", operator=FilterOperator.EQ, value=True)
            ],
            logic="AND",
        )
        outer = FilterExpression(
            conditions=[
                FilterCondition(
                    field="status", operator=FilterOperator.EQ, value="active"
                )
            ],
            logic="AND",
            nested=inner,
        )
        assert (
            self.evaluator.evaluate_expression(outer, {"status": "active", "vip": True})
            is True
        )
        assert (
            self.evaluator.evaluate_expression(
                outer, {"status": "active", "vip": False}
            )
            is False
        )

    def test_nested_expression_or_logic(self):
        inner = FilterExpression(
            conditions=[
                FilterCondition(field="admin", operator=FilterOperator.EQ, value=True)
            ],
            logic="OR",
        )
        outer = FilterExpression(
            conditions=[
                FilterCondition(
                    field="role", operator=FilterOperator.EQ, value="editor"
                )
            ],
            logic="OR",
            nested=inner,
        )
        assert (
            self.evaluator.evaluate_expression(outer, {"role": "viewer", "admin": True})
            is True
        )

    def test_unknown_logic_raises(self):
        expr = FilterExpression(conditions=[], logic="XOR")
        with pytest.raises(FilterEvaluationError, match="Unknown logic"):
            self.evaluator.evaluate_expression(expr, {})


class TestRowFilterEvaluatorFieldAccess:
    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_dot_notation_dict(self):
        row_data = {"owner": {"company_id": 42}}
        cond = FilterCondition(
            field="owner.company_id", operator=FilterOperator.EQ, value=42
        )
        assert self.evaluator.evaluate_condition(cond, row_data) is True

    def test_dot_notation_object(self):
        class Owner:
            company_id = 42

        row_data = {"owner": Owner()}
        cond = FilterCondition(
            field="owner.company_id", operator=FilterOperator.EQ, value=42
        )
        assert self.evaluator.evaluate_condition(cond, row_data) is True

    def test_dot_notation_missing_relation(self):
        cond = FilterCondition(
            field="owner.company_id", operator=FilterOperator.IS_NULL
        )
        assert self.evaluator.evaluate_condition(cond, {}) is True

    def test_get_from_row_model(self):
        class FakeModel:
            name = "test"

        cond = FilterCondition(field="name", operator=FilterOperator.EQ, value="test")
        assert (
            self.evaluator.evaluate_condition(cond, {}, row_model=FakeModel()) is True
        )

    def test_dot_notation_from_model(self):
        class Related:
            val = 10

        class FakeModel:
            related = Related()

        cond = FilterCondition(
            field="related.val", operator=FilterOperator.EQ, value=10
        )
        assert (
            self.evaluator.evaluate_condition(cond, {}, row_model=FakeModel()) is True
        )


class TestRowFilterEvaluatorSqlWhere:
    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_simple_eq(self):
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "active"}, "status = ?", ["active"]
            )
            is True
        )
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "inactive"}, "status = ?", ["active"]
            )
            is False
        )

    def test_neq(self):
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "active"}, "status != ?", ["deleted"]
            )
            is True
        )
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "deleted"}, "status != ?", ["deleted"]
            )
            is False
        )

    def test_lt_gt(self):
        assert self.evaluator.evaluate_sql_where({"age": 10}, "age < ?", [18]) is True
        assert self.evaluator.evaluate_sql_where({"age": 20}, "age > ?", [18]) is True

    def test_lte_gte(self):
        assert self.evaluator.evaluate_sql_where({"x": 5}, "x <= ?", [5]) is True
        assert self.evaluator.evaluate_sql_where({"x": 5}, "x >= ?", [5]) is True

    def test_like(self):
        assert (
            self.evaluator.evaluate_sql_where(
                {"name": "John"}, "name LIKE ?", ["John%"]
            )
            is True
        )

    def test_not_like(self):
        assert (
            self.evaluator.evaluate_sql_where(
                {"name": "Jane"}, "name NOT LIKE ?", ["John%"]
            )
            is True
        )

    def test_empty_where(self):
        assert self.evaluator.evaluate_sql_where({}, "", []) is True

    def test_no_match_pattern(self):
        assert self.evaluator.evaluate_sql_where({}, "???", []) is True

    def test_literal_value(self):
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "active"}, "status = 'active'", []
            )
            is True
        )

    def test_no_params_for_placeholder(self):
        assert self.evaluator.evaluate_sql_where({"x": 1}, "x = ?", []) is False

    def test_diamond_operator(self):
        assert self.evaluator.evaluate_sql_where({"x": "a"}, "x <> ?", ["b"]) is True
        assert self.evaluator.evaluate_sql_where({"x": "a"}, "x <> ?", ["a"]) is False


class TestLikeMatch:
    def test_percent_wildcard(self):
        assert RowFilterEvaluator._like_match("hello world", "hello%") is True
        assert RowFilterEvaluator._like_match("hello world", "%world") is True
        assert RowFilterEvaluator._like_match("hello world", "%lo wo%") is True

    def test_underscore_wildcard(self):
        assert RowFilterEvaluator._like_match("cat", "c_t") is True
        assert RowFilterEvaluator._like_match("cut", "c_t") is True
        assert RowFilterEvaluator._like_match("cart", "c_t") is False

    def test_none_value(self):
        assert RowFilterEvaluator._like_match(None, "test%") is False

    def test_case_insensitive(self):
        assert RowFilterEvaluator._like_match("HELLO", "hello%") is True


class TestRowFilterEvaluatorCrossTableReference:
    """Tests for cross-table reference lookups in evaluate_condition."""

    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_reference_lookup_used_when_provided(self):
        """Cover lines 170-172: reference_table + reference_key + reference_lookup."""
        cond = FilterCondition(
            field="region_id",
            operator=FilterOperator.EQ,
            value="ignored_since_lookup_overrides",
            reference_table="regions",
            reference_key="region_code",
        )

        def mock_lookup(table, field_value, key):
            assert table == "regions"
            assert key == "region_code"
            return "US"

        assert (
            self.evaluator.evaluate_condition(
                cond, {"region_id": "US"}, reference_lookup=mock_lookup
            )
            is True
        )
        assert (
            self.evaluator.evaluate_condition(
                cond, {"region_id": "EU"}, reference_lookup=mock_lookup
            )
            is False
        )

    def test_unknown_operator_raises(self):
        """Cover line 179: unknown operator raises FilterEvaluationError."""
        cond = FilterCondition(field="x", operator=FilterOperator.EQ, value=1)
        evaluator = RowFilterEvaluator()
        # Manually remove the operator to simulate unknown
        evaluator._operator_functions.pop(FilterOperator.EQ)
        with pytest.raises(FilterEvaluationError, match="Unknown operator"):
            evaluator.evaluate_condition(cond, {"x": 1})

    def test_generic_exception_wrapped(self):
        """Cover lines 188-191: generic exception wrapped as FilterEvaluationError."""
        cond = FilterCondition(
            field="x", operator=FilterOperator.LT, value="not_a_number"
        )
        with pytest.raises(FilterEvaluationError, match="Error evaluating condition"):
            self.evaluator.evaluate_condition(cond, {"x": None})

    def test_filter_evaluation_error_reraise_in_expression(self):
        """Cover lines 224-225: FilterEvaluationError re-raised from evaluate_expression."""
        evaluator = RowFilterEvaluator()
        # Remove operator to trigger FilterEvaluationError
        evaluator._operator_functions.pop(FilterOperator.EQ)
        cond = FilterCondition(field="x", operator=FilterOperator.EQ, value=1)
        expr = FilterExpression(conditions=[cond], logic="AND")
        with pytest.raises(FilterEvaluationError, match="Unknown operator"):
            evaluator.evaluate_expression(expr, {"x": 1})


class TestRowFilterEvaluatorSqlWhereExtended:
    """Additional tests for SQL WHERE false-branch paths."""

    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_unquoted_literal_value(self):
        """Cover line 303: unquoted literal compare_value."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "active"}, "status = active", []
            )
            is True
        )
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "inactive"}, "status = active", []
            )
            is False
        )

    def test_lt_false_branch(self):
        """Cover line 315: field_value < compare_value is False."""
        assert self.evaluator.evaluate_sql_where({"age": 20}, "age < ?", [10]) is False

    def test_lte_false_branch(self):
        """Cover line 318: field_value <= compare_value is False."""
        assert self.evaluator.evaluate_sql_where({"age": 20}, "age <= ?", [10]) is False

    def test_gt_false_branch(self):
        """Cover line 321: field_value > compare_value is False."""
        assert self.evaluator.evaluate_sql_where({"age": 5}, "age > ?", [10]) is False

    def test_gte_false_branch(self):
        """Cover line 324: field_value >= compare_value is False."""
        assert self.evaluator.evaluate_sql_where({"age": 5}, "age >= ?", [10]) is False

    def test_in_false_branch(self):
        """Cover lines 326-327: field_value not in compare_value."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"role": "viewer"}, "role IN ?", [["admin", "editor"]]
            )
            is False
        )

    def test_not_in_false_branch(self):
        """Cover lines 329-330: field_value in compare_value."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"role": "banned"}, "role NOT IN ?", [["banned", "deleted"]]
            )
            is False
        )

    def test_like_false_branch(self):
        """Cover line 333: LIKE doesn't match."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"name": "Jane"}, "name LIKE ?", ["John%"]
            )
            is False
        )

    def test_not_like_false_branch(self):
        """Cover line 336: NOT LIKE matches (so returns False)."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"name": "John Doe"}, "name NOT LIKE ?", ["John%"]
            )
            is False
        )

    def test_double_quoted_literal(self):
        """Cover double-quoted literal value parsing."""
        assert (
            self.evaluator.evaluate_sql_where(
                {"status": "active"}, 'status = "active"', []
            )
            is True
        )


class TestRowFilterEvaluatorDotNotationModelFalse:
    """Test dot-notation when related_obj is falsy on model."""

    def setup_method(self):
        self.evaluator = RowFilterEvaluator()

    def test_dot_notation_model_relation_none(self):
        """Cover line 129: if related_obj: branch false."""

        class FakeModel:
            owner = None

        cond = FilterCondition(
            field="owner.company_id", operator=FilterOperator.IS_NULL
        )
        assert (
            self.evaluator.evaluate_condition(cond, {}, row_model=FakeModel()) is True
        )


class TestReferenceTableLookup:
    def test_lookup_returns_none(self):
        lookup = ReferenceTableLookup(session=None)
        assert lookup.lookup("table", "value", "col") is None

    def test_lookup_caches(self):
        lookup = ReferenceTableLookup(session=None)
        lookup.lookup("table", "value", "col")
        # Second call should use cache
        assert lookup.lookup("table", "value", "col") is None
        assert ("table", "value", "col") in lookup._cache
