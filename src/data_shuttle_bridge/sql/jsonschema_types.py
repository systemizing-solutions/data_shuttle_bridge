"""JSON Schema to SQLAlchemy type mapping and version table provisioning."""

from typing import Any, Dict, List, Optional, Type
from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    Time,
    Column,
    MetaData,
    Table,
    Text,
    JSON,
)
from sqlalchemy.types import TypeEngine


def sa_type_for_jsonschema(
    prop_schema: Dict[str, Any], dialect: str = "postgresql"
) -> Type[TypeEngine]:
    """
    Map JSON Schema property type to SQLAlchemy type.

    Args:
        prop_schema: JSON Schema property definition
        dialect: Target SQL dialect ('postgresql', 'sqlite', etc.)

    Returns:
        SQLAlchemy type class

    Raises:
        ValueError: If JSON Schema type is not supported
    """
    schema_type = prop_schema.get("type")
    format_hint = prop_schema.get("format")

    # String types
    if schema_type == "string":
        if format_hint == "email":
            return String(254)  # RFC 5321
        elif format_hint == "uri" or format_hint == "url":
            return String(2048)  # Reasonable URL length
        elif format_hint == "date":
            return Date
        elif format_hint == "time":
            return Time
        elif format_hint == "date-time":
            return DateTime
        elif format_hint == "uuid":
            return String(36)
        else:
            # Default string with optional maxLength
            max_length = prop_schema.get("maxLength", 1000)
            return String(max_length)

    # Numeric types
    elif schema_type == "integer":
        return Integer

    elif schema_type == "number":
        return Float

    # Boolean
    elif schema_type == "boolean":
        return Boolean

    # Array (stored as JSON column for MVP)
    elif schema_type == "array":
        return JSON

    # Object (stored as JSON column for MVP)
    elif schema_type == "object":
        return JSON

    # Null type
    elif schema_type == "null":
        return String(1)  # Placeholder for NULL

    else:
        raise ValueError(f"Unsupported JSON Schema type: {schema_type!r}")


def build_version_table(
    metadata: MetaData,
    *,
    table_name: str,
    schema: Dict[str, Any],
    add_surrogate_pk: bool = True,
    add_schema_metadata_columns: bool = True,
) -> Table:
    """
    Create a SQLAlchemy Table from a JSON Schema.

    Args:
        metadata: SQLAlchemy MetaData instance
        table_name: Physical table name (e.g., 'customer__v1')
        schema: JSON Schema document (must be object type)
        add_surrogate_pk: If True, add an auto-incrementing _id primary key
        add_schema_metadata_columns: If True, add _created_at and _updated_at timestamp columns

    Returns:
        SQLAlchemy Table instance

    Raises:
        ValueError: If schema is not of type 'object'
    """
    if schema.get("type") != "object":
        raise ValueError("Only object schemas are supported in MVP")

    required = set(schema.get("required", []))
    properties = schema.get("properties", {})

    cols: List[Column] = []

    # Add surrogate primary key
    if add_surrogate_pk:
        cols.append(Column("_id", Integer, primary_key=True, autoincrement=True))

    # Build columns from properties
    for prop_name, prop_schema in properties.items():
        col_type = sa_type_for_jsonschema(prop_schema)
        is_nullable = prop_name not in required
        default_value = prop_schema.get("default")

        # Create column with appropriate nullability and default
        if default_value is not None:
            col = Column(
                prop_name, col_type, nullable=is_nullable, default=default_value
            )
        else:
            col = Column(prop_name, col_type, nullable=is_nullable)

        cols.append(col)

    # Add metadata columns
    if add_schema_metadata_columns:
        cols.append(Column("_created_at", DateTime, nullable=False))
        cols.append(Column("_updated_at", DateTime, nullable=False))

    # Create and return table
    return Table(table_name, metadata, *cols)


def get_columns_from_schema(schema: Dict[str, Any]) -> List[str]:
    """
    Extract column names from a JSON Schema.

    Args:
        schema: JSON Schema document (object type)

    Returns:
        List of property names (columns)
    """
    if schema.get("type") != "object":
        raise ValueError("Only object schemas are supported")

    return list(schema.get("properties", {}).keys())


def get_required_columns(schema: Dict[str, Any]) -> List[str]:
    """
    Extract required column names from a JSON Schema.

    Args:
        schema: JSON Schema document (object type)

    Returns:
        List of required property names
    """
    if schema.get("type") != "object":
        raise ValueError("Only object schemas are supported")

    return schema.get("required", [])
