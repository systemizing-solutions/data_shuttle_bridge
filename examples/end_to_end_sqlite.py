"""
End-to-end example demonstrating schema versioning with SQLite.

This example shows:
1. Creating a schema set
2. Adding multiple schema versions with differences
3. Computing and inspecting diffs
4. Ingesting data into different versions
5. Building a consolidated view across versions
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import MetaData, Table, select, union_all, create_engine
from sqlmodel import Session

# Import versioning components
from data_shuttle_bridge.sql.versioning_models import (
    SchemaSet,
    SchemaVersion,
    create_all_tables,
)
from data_shuttle_bridge.sql.schema_registry import SchemaRegistry
from data_shuttle_bridge.sql.view_builder import build_consolidated_select
from data_shuttle_bridge.sql.diffing import DefaultDiffEngine


def main():
    """Run the end-to-end example."""

    # Setup
    db_url = "sqlite:///example_versioning.db"
    engine = create_engine(db_url, echo=False)

    # Create all registry tables
    from data_shuttle_bridge.sql.versioning_models import SchemaSet as Base
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)

    print("=" * 70)
    print("Schema Versioning End-to-End Example")
    print("=" * 70)
    print()

    # Step 1: Create schema set
    print("Step 1: Creating schema set 'customer'...")
    registry = SchemaRegistry(engine)

    with Session(engine) as session:
        schema_set = registry.create_schema_set(
            session,
            key="customer",
            name="Customer",
            description="Customer entity with evolving schema",
        )
        print(f"✓ Created schema set: {schema_set.key} (id={schema_set.id})\n")

        # Define schemas
        schema_v1 = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "customer",
            "type": "object",
            "required": ["customer_id", "email"],
            "properties": {
                "customer_id": {"type": "string"},
                "email": {"type": "string", "format": "email"},
                "age": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        }

        schema_v2 = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "customer",
            "type": "object",
            "required": ["customer_id", "primary_email"],
            "properties": {
                "customer_id": {"type": "string"},
                "primary_email": {"type": "string", "format": "email"},
                "country": {"type": "string", "default": "ZA"},
            },
            "additionalProperties": False,
        }

        # Step 2: Add schema versions
        print("Step 2: Adding schema versions...")

        version_v1 = registry.add_schema_version(
            session,
            schema_set_key="customer",
            version=1,
            schema_json=schema_v1,
        )
        print(f"✓ Created version 1")
        print(f"  Table: {version_v1.table_name}\n")

        version_v2 = registry.add_schema_version(
            session,
            schema_set_key="customer",
            version=2,
            schema_json=schema_v2,
            parent_version=1,
        )
        print(f"✓ Created version 2 (parent: v1)")
        print(f"  Table: {version_v2.table_name}\n")

        # Step 3: Inspect diff
        print("Step 3: Computing diff between v1 and v2...")
        diff = registry.get_schema_diff(
            session,
            schema_set_key="customer",
            from_version=1,
            to_version=2,
        )

        if diff:
            print("Diff Classification:")
            classification = diff.get("classification", {})
            print(f"  Total changes: {classification.get('total_changes', 0)}")
            print(f"  Unresolved: {classification.get('unresolved_count', 0)}")
            print(f"  Warnings: {classification.get('warning_count', 0)}")
            print()

            if classification.get("unresolved"):
                print("  Unresolved changes (require mapping rules):")
                for record in classification["unresolved"]:
                    print(f"    - {record['kind']}: {record['column']}")
            print()

        # Step 4: Ingest data
        print("Step 4: Ingesting sample data...")

        record_v1 = {
            "customer_id": "c_001",
            "email": "alice@example.com",
            "age": 30,
        }

        row_id_v1 = registry.ingest_data(
            session,
            schema_set_key="customer",
            version=1,
            payload=record_v1,
        )
        print(f"✓ Inserted v1 record (id={row_id_v1})")

        record_v2 = {
            "customer_id": "c_002",
            "primary_email": "bob@example.com",
            "country": "UK",
        }

        row_id_v2 = registry.ingest_data(
            session,
            schema_set_key="customer",
            version=2,
            payload=record_v2,
        )
        print(f"✓ Inserted v2 record (id={row_id_v2})\n")

        # Step 5: Build consolidated view
        print("Step 5: Building consolidated view...")

        # Load tables
        metadata = MetaData()
        table_v1 = Table("customer__v1", metadata, autoload_with=engine)
        table_v2 = Table("customer__v2", metadata, autoload_with=engine)

        # Define mapping rules for v2: primary_email -> email
        rename_rules_by_version = {
            2: {
                "email": "primary_email"
            }  # map unified column 'email' from 'primary_email'
        }

        # Define defaults for missing columns
        defaults_by_column = {"country": "ZA"}  # fill v1 records with country default

        # Build the unified select
        unified_select = build_consolidated_select(
            version_tables=[(1, table_v1), (2, table_v2)],
            target_columns=["customer_id", "email", "country"],
            rename_rules_by_version=rename_rules_by_version,
            defaults_by_column=defaults_by_column,
        )

        print("✓ Built consolidated view\n")

        # Step 6: Query consolidated view
        print("Step 6: Querying consolidated view...")
        print()

        rows = session.execute(select(unified_select)).all()

        print("Query results:")
        print(f"{'customer_id':<15} {'email':<30} {'country':<15} {'version':<10}")
        print("-" * 70)

        for row in rows:
            print(f"{row[0]:<15} {row[1]:<30} {row[2]:<15} {row[3]:<10}")

        print()
        print("=" * 70)
        print("Example complete!")
        print("=" * 70)


if __name__ == "__main__":
    main()
