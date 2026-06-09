"""
Filter evaluation engine for row-level synchronization filtering.

This module provides runtime evaluation of FilterExpression objects against
actual row data to determine which rows should be synced.

Supports:
- Simple column comparisons
- Multiple operators (=, !=, <, >, <=, >=, in, not_in, etc.)
- Cross-table references for rule-based filtering
- Complex boolean logic (AND/OR/nested)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Callable, TYPE_CHECKING
from sqlmodel import Session

from data_shuttle_bridge.sql.sync_config import (
    FilterCondition,
    FilterExpression,
    FilterOperator,
    SqlFilter,
)

if TYPE_CHECKING:
    from sqlmodel import Session


class FilterEvaluationError(Exception):
    """Raised when filter evaluation fails."""

    pass


class RowFilterEvaluator:
    """
    Evaluates FilterExpression objects against row data.

    Supports cross-table references and complex filter logic.
    """

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize the evaluator.

        Args:
            session: Optional SQLModel session for cross-table lookups
        """
        self.session = session
        self._operator_functions = self._setup_operators()

    @staticmethod
    def _setup_operators() -> Dict[FilterOperator, Callable]:
        """Set up operator implementation functions."""
        return {
            FilterOperator.EQ: lambda a, b: a == b,
            FilterOperator.NEQ: lambda a, b: a != b,
            FilterOperator.LT: lambda a, b: a < b,
            FilterOperator.LTE: lambda a, b: a <= b,
            FilterOperator.GT: lambda a, b: a > b,
            FilterOperator.GTE: lambda a, b: a >= b,
            FilterOperator.IN: lambda a, b: (
                a in b if isinstance(b, (list, tuple, set)) else False
            ),
            FilterOperator.NOT_IN: lambda a, b: (
                a not in b if isinstance(b, (list, tuple, set)) else True
            ),
            FilterOperator.CONTAINS: lambda a, b: (
                str(b).lower() in str(a).lower() if a and b else False
            ),
            FilterOperator.NOT_CONTAINS: lambda a, b: (
                str(b).lower() not in str(a).lower() if a and b else True
            ),
            FilterOperator.LIKE: lambda a, b: self._like_match(a, b),
            FilterOperator.NOT_LIKE: lambda a, b: not self._like_match(a, b),
            FilterOperator.IS_NULL: lambda a, _: a is None,
            FilterOperator.IS_NOT_NULL: lambda a, _: a is not None,
        }

    @staticmethod
    def _like_match(value: Any, pattern: str) -> bool:
        """Simple SQL LIKE pattern matching."""
        if value is None:
            return False
        s = str(value)
        # Replace SQL wildcards with regex equivalents
        pattern = pattern.replace("%", ".*").replace("_", ".")
        import re

        return bool(re.match(f"^{pattern}$", s, re.IGNORECASE))

    def _get_field_value(
        self,
        row_data: Dict[str, Any],
        field: str,
        row_model: Optional[Any] = None,
    ) -> Any:
        """
        Get a field value from row data.

        Supports dot notation for nested/joined fields (e.g. "user.name").

        Args:
            row_data: Dictionary of row data
            field: Field name, optionally with dot notation
            row_model: Optional SQLModel instance for cross-table lookups

        Returns:
            The field value
        """
        if "." in field:
            # Cross-table reference (e.g. "owner.company_id")
            parts = field.split(".", 1)
            relation_name = parts[0]
            nested_field = parts[1]

            # Try to get from row_data first
            if relation_name in row_data:
                related_data = row_data[relation_name]
                if isinstance(related_data, dict):
                    return self._get_field_value(related_data, nested_field)
                else:
                    # It's an object
                    return getattr(related_data, nested_field, None)

            # Try from row_model
            if row_model:
                related_obj = getattr(row_model, relation_name, None)
                if related_obj:
                    return self._get_field_value({}, nested_field, related_obj)

            return None
        else:
            # Simple field lookup
            if field in row_data:
                return row_data[field]
            if row_model:
                return getattr(row_model, field, None)
            return None

    def evaluate_condition(
        self,
        condition: FilterCondition,
        row_data: Dict[str, Any],
        row_model: Optional[Any] = None,
        reference_lookup: Optional[Callable[[str, Any], Optional[Any]]] = None,
    ) -> bool:
        """
        Evaluate a single condition against row data.

        Args:
            condition: FilterCondition to evaluate
            row_data: Dictionary of row column data
            row_model: Optional SQLModel instance
            reference_lookup: Optional function to lookup reference table values

        Returns:
            True if condition matches
        """
        try:
            field_value = self._get_field_value(row_data, condition.field, row_model)

            # Handle cross-table references
            if (
                condition.reference_table
                and condition.reference_key
                and reference_lookup
            ):
                # Lookup in reference table
                filter_value = reference_lookup(
                    condition.reference_table, field_value, condition.reference_key
                )
            else:
                filter_value = condition.value

            # Get the operator function
            op_func = self._operator_functions.get(condition.operator)
            if not op_func:
                raise FilterEvaluationError(f"Unknown operator: {condition.operator}")

            # For LIKE operator, we need custom handling
            if condition.operator == FilterOperator.LIKE:
                return self._like_match(field_value, filter_value)
            elif condition.operator == FilterOperator.NOT_LIKE:
                return not self._like_match(field_value, filter_value)
            else:
                return op_func(field_value, filter_value)
        except Exception as e:
            raise FilterEvaluationError(
                f"Error evaluating condition {condition}: {e}"
            ) from e

    def evaluate_expression(
        self,
        expression: FilterExpression,
        row_data: Dict[str, Any],
        row_model: Optional[Any] = None,
        reference_lookup: Optional[Callable[[str, Any], Optional[Any]]] = None,
    ) -> bool:
        """
        Evaluate a complete filter expression against row data.

        Args:
            expression: FilterExpression to evaluate
            row_data: Dictionary of row column data
            row_model: Optional SQLModel instance
            reference_lookup: Optional function for cross-table reference lookups

        Returns:
            True if the expression matches
        """
        results = []

        # Evaluate all conditions in this expression
        for condition in expression.conditions:
            try:
                result = self.evaluate_condition(
                    condition,
                    row_data,
                    row_model,
                    reference_lookup,
                )
                results.append(result)
            except FilterEvaluationError:
                raise

        # Determine how to combine results
        if expression.logic.upper() == "AND":
            combined = all(results) if results else True
        elif expression.logic.upper() == "OR":
            combined = any(results) if results else False
        else:
            raise FilterEvaluationError(f"Unknown logic operator: {expression.logic}")

        # Handle nested expression
        if expression.nested:
            nested_result = self.evaluate_expression(
                expression.nested,
                row_data,
                row_model,
                reference_lookup,
            )
            if expression.logic.upper() == "AND":
                combined = combined and nested_result
            else:
                combined = combined or nested_result

        return combined

    def evaluate_sql_where(
        self,
        row_data: Dict[str, Any],
        where_clause: str,
        where_params: Optional[list] = None,
    ) -> bool:
        """
        Evaluate a simple SQL WHERE clause syntax.

        This provides a lighter-weight alternative to FilterExpression
        for simple cases.

        Args:
            row_data: Dictionary of row column data
            where_clause: SQL-like WHERE clause (e.g. "status = ?")
            where_params: Parameters for the WHERE clause

        Returns:
            True if the row matches the condition
        """
        if not where_clause:
            return True

        # Simple SQL-like evaluation
        # This is a simplified implementation for common cases
        # For complex cases, use FilterExpression instead

        where_params = where_params or []
        params_copy = list(where_params)

        # Parse simple comparisons
        import re

        # Match patterns like "field op ?" or "field op value"
        pattern = r'(\w+)\s*(=|!=|<>|<=|>=|<|>|IN|NOT IN|LIKE|NOT LIKE)\s*(\?|\'[^\']*\'|"[^"]*"|[\w\-\.]+)'

        matches = re.findall(pattern, where_clause, re.IGNORECASE)
        if not matches:
            return True

        for field, op, value_str in matches:
            field_value = row_data.get(field)

            # Resolve parameter
            if value_str == "?":
                if not params_copy:
                    return False
                compare_value = params_copy.pop(0)
            else:
                # Remove quotes if present
                if value_str.startswith(("'", '"')):
                    compare_value = value_str[1:-1]
                else:
                    compare_value = value_str

            # Perform comparison
            op_upper = op.upper()
            if op_upper == "=":
                if field_value != compare_value:
                    return False
            elif op_upper in ("!=", "<>"):
                if field_value == compare_value:
                    return False
            elif op_upper == "<":
                if not (field_value < compare_value):
                    return False
            elif op_upper == "<=":
                if not (field_value <= compare_value):
                    return False
            elif op_upper == ">":
                if not (field_value > compare_value):
                    return False
            elif op_upper == ">=":
                if not (field_value >= compare_value):
                    return False
            elif op_upper in ("IN",):
                if field_value not in compare_value:
                    return False
            elif op_upper in ("NOT IN",):
                if field_value in compare_value:
                    return False
            elif op_upper == "LIKE":
                if not self._like_match(field_value, compare_value):
                    return False
            elif op_upper == "NOT LIKE":
                if self._like_match(field_value, compare_value):
                    return False

        return True

    def evaluate_sql_filter(
        self,
        sql_filter: SqlFilter,
        row_data: Dict[str, Any],
        row_model: Optional[Any] = None,
    ) -> bool:
        """
        Evaluate a SQL-based filter against row data.
        
        Supports raw SQL WHERE clauses and Jinja2-templated WHERE clauses.
        
        Args:
            sql_filter: SqlFilter to evaluate
            row_data: Dictionary of row column data
            row_model: Optional SQLModel instance
            
        Returns:
            True if the row matches the filter
        """
        try:
            # Render the WHERE clause (handles Jinja2 templating if needed)
            where_clause = sql_filter.render_where()
            
            if not where_clause:
                return True
            
            # Evaluate the WHERE clause
            return self.evaluate_sql_where(where_clause, where_params=None)
        except Exception as e:
            import sys
            print(f"WARNING: Error evaluating SQL filter: {e}", file=sys.stderr)
            return True  # Default to allowing the row on error


class ReferenceTableLookup:
    """Helper for looking up values in reference tables using the session."""

    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[tuple, Any] = {}

    def lookup(
        self,
        table_name: str,
        lookup_value: Any,
        lookup_column: str,
    ) -> Optional[Any]:
        """
        Look up a value in a reference table.

        Args:
            table_name: Name of the reference table  lookup_value: Value to look up
            lookup_column: Column name to match against

        Returns:
            The matched row, or None if not found
        """
        cache_key = (table_name, lookup_value, lookup_column)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # This would need to be implemented based on the actual table registry
        # For now, return None
        result = None
        self._cache[cache_key] = result
        return result
