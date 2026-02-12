"""
Example: Using Sync Configurations

This demonstrates how to use the sync configuration system to control
what data gets synchronized from a local database to a remote service.

Sync configurations allow you to:
- Sync entire database (default)
- Sync specific schemas
- Sync specific tables
- Sync rows matching filter conditions
- Include/exclude specific columns
"""

from pathlib import Path
from sqlmodel import SQLModel, Field, create_engine, Session

from data_shuttle_bridge.sql import (
    SyncEngine,
    SyncConfig,
    SyncScope,
    TableSyncRule,
    FilterExpression,
    FilterCondition,
    FilterOperator,
    build_schema,
    attach_change_hooks_for_models,
)


# ===== Models =====


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str
    is_active: bool = True
    tenant_id: str
    version: int = Field(default=1)


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    status: str  # "pending", "processing", "shipped", "delivered"
    amount_cents: int
    created_at: str
    version: int = Field(default=1)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    price_cents: int
    internal_cost_cents: int  # Sensitive - might want to exclude
    version: int = Field(default=1)


class AuditLog(SQLModel, table=True):
    """System table - usually not synced"""

    id: int | None = Field(default=None, primary_key=True)
    action: str
    table_name: str
    created_at: str


# ===== Setup =====


def setup_local_db(db_url: str):
    """Initialize local database."""
    engine = create_engine(db_url, echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


def example_1_sync_everything():
    """Example 1: Sync entire database (default behavior)"""
    print("\n" + "=" * 60)
    print("EXAMPLE 1: Sync Everything (Default)")
    print("=" * 60)

    # Configuration: sync everything
    config = SyncConfig()  # Same as: SyncConfig(scope=SyncScope.DATABASE)

    print(f"Scope: {config.scope.value}")
    print(f"Enabled: {config.enabled}")
    print("Result: All tables and all rows will be synced")


def example_2_sync_specific_tables():
    """Example 2: Sync only specific tables"""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Sync Specific Tables")
    print("=" * 60)

    # Configuration: only sync customers and orders
    config = SyncConfig(
        scope=SyncScope.TABLES,
        tables={
            "customer": TableSyncRule(enabled=True),
            "order": TableSyncRule(enabled=True),
            "product": TableSyncRule(enabled=False),
            "audit_log": TableSyncRule(enabled=False),
        },
    )

    print(f"Scope: {config.scope.value}")
    print("Tables to sync:")
    for table_name, rule in config.tables.items():
        status = "✓" if rule.enabled else "✗"
        print(f"  {status} {table_name}")


def example_3_row_level_filtering():
    """Example 3: Sync only active orders"""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Row-Level Filtering (Active Orders Only)")
    print("=" * 60)

    # Configuration: sync orders with status "processing" or "shipped"
    config = SyncConfig(
        scope=SyncScope.FILTERED,
        tables={
            "order": TableSyncRule(
                filter=FilterExpression(
                    conditions=[
                        FilterCondition(
                            field="status",
                            operator=FilterOperator.IN,
                            value=["processing", "shipped"],
                        )
                    ]
                )
            ),
            "customer": TableSyncRule(),  # All customers
        },
    )

    print(f"Scope: {config.scope.value}")
    print("\nFilter configuration:")
    order_rule = config.get_table_rule("order")
    if order_rule and order_rule.filter:
        for cond in order_rule.filter.conditions:
            print(f"  {cond.field} {cond.operator.value} {cond.value}")


def example_4_multi_tenant_filtering():
    """Example 4: Multi-tenant - sync only tenant-specific data"""
    print("\n" + "=" * 60)
    print("EXAMPLE 4: Multi-Tenant Filtering")
    print("=" * 60)

    tenant_id = "ACME-CORP-001"

    # Configuration: sync only data for specific tenant
    config = SyncConfig(
        scope=SyncScope.FILTERED,
        tables={
            "customer": TableSyncRule(
                filter=FilterExpression(
                    conditions=[
                        FilterCondition(
                            field="tenant_id",
                            operator=FilterOperator.EQ,
                            value=tenant_id,
                        )
                    ]
                )
            ),
            "order": TableSyncRule(
                filter=FilterExpression(
                    conditions=[
                        FilterCondition(
                            field="tenant_id",
                            operator=FilterOperator.EQ,
                            value=tenant_id,
                        )
                    ]
                )
            ),
        },
    )

    print(f"Scope: {config.scope.value}")
    print(f"Tenant: {tenant_id}")
    print("Tables with tenant filtering:")
    for table_name in ["customer", "order"]:
        rule = config.get_table_rule(table_name)
        if rule and rule.filter:
            cond = rule.filter.conditions[0]
            print(f"  ✓ {table_name}: where {cond.field} = {cond.value}")


def example_5_exclude_sensitive_columns():
    """Example 5: Hide sensitive columns"""
    print("\n" + "=" * 60)
    print("EXAMPLE 5: Exclude Sensitive Columns")
    print("=" * 60)

    # Configuration: exclude sensitive product data
    config = SyncConfig(
        scope=SyncScope.TABLES,
        tables={
            "product": TableSyncRule(
                enabled=True, exclude_columns=["internal_cost_cents"]  # Don't sync cost
            ),
            "customer": TableSyncRule(enabled=True),
            "order": TableSyncRule(enabled=True),
        },
    )

    print(f"Scope: {config.scope.value}")
    print("\nColumn filtering:")
    product_rule = config.get_table_rule("product")
    if product_rule:
        print(f"  product: exclude {product_rule.exclude_columns}")


def example_6_include_only_specific_columns():
    """Example 6: Sync only specific columns"""
    print("\n" + "=" * 60)
    print("EXAMPLE 6: Include Only Specific Columns")
    print("=" * 60)

    # Configuration: sync only essential customer data
    config = SyncConfig(
        scope=SyncScope.TABLES,
        tables={
            "customer": TableSyncRule(
                enabled=True, include_only_columns=["id", "name", "email", "tenant_id"]
            ),
        },
    )

    print(f"Scope: {config.scope.value}")
    print("\nColumn filtering:")
    customer_rule = config.get_table_rule("customer")
    if customer_rule:
        print(f"  customer: include only {customer_rule.include_only_columns}")


def example_7_load_from_file():
    """Example 7: Load configuration from YAML file"""
    print("\n" + "=" * 60)
    print("EXAMPLE 7: Load from Configuration File")
    print("=" * 60)

    # Create a temporary config file
    config_yaml = """
scope: filtered
enabled: true

tables:
  customer:
    enabled: true
    filter:
      conditions:
        - field: is_active
          operator: "="
          value: true
  
  order:
    enabled: true
    filter:
      conditions:
        - field: status
          operator: in
          value: [processing, shipped]
"""

    # Write to temporary file
    config_file = Path("/tmp/sync_config_example.yaml")
    config_file.write_text(config_yaml)

    # Load configuration
    config = SyncConfig.from_file(config_file)

    print(f"Scope: {config.scope.value}")
    print(f"Loaded from: {config_file}")
    print("Tables:")
    for table_name, rule in config.tables.items():
        print(f"  ✓ {table_name}")


def example_8_complex_filtering():
    """Example 8: Complex filtering with AND/OR logic"""
    print("\n" + "=" * 60)
    print("EXAMPLE 8: Complex Filtering (AND/OR Logic)")
    print("=" * 60)

    # Configuration: high-value recent orders
    # (amount > 10000 AND status = shipped) OR (flagged_vip = true)
    config = SyncConfig(
        scope=SyncScope.FILTERED,
        tables={
            "order": TableSyncRule(
                filter=FilterExpression(
                    conditions=[
                        FilterCondition(
                            field="amount_cents",
                            operator=FilterOperator.GT,
                            value=1000000,  # > $10,000
                        ),
                        FilterCondition(
                            field="status", operator=FilterOperator.EQ, value="shipped"
                        ),
                    ],
                    logic="AND",
                    nested=FilterExpression(
                        conditions=[
                            FilterCondition(
                                field="is_active",
                                operator=FilterOperator.EQ,
                                value=True,
                            )
                        ],
                        logic="OR",
                    ),
                )
            )
        },
    )

    print(f"Scope: {config.scope.value}")
    print("\nFilter logic:")
    print("  (amount_cents > 1000000 AND status = 'shipped')")
    print("  OR")
    print("  (is_active = true)")


def example_9_integration_with_sync_engine():
    """Example 9: Use configuration with SyncEngine"""
    print("\n" + "=" * 60)
    print("EXAMPLE 9: Integration with SyncEngine")
    print("=" * 60)

    # Setup database
    engine = setup_local_db("sqlite:///demo_sync_config.db")

    # Create schema
    schema = build_schema([Customer, Order, Product, AuditLog])

    # Hook up change tracking
    with Session(engine) as session:
        attach_change_hooks_for_models([Customer, Order, Product, AuditLog])

        # Create configuration
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "customer": TableSyncRule(enabled=True),
                "order": TableSyncRule(enabled=True),
                "product": TableSyncRule(enabled=True),
                "audit_log": TableSyncRule(enabled=False),  # Don't sync logs
            },
        )

        # Create SyncEngine with configuration
        sync_engine = SyncEngine(
            session=session, peer_id="client_local", schema=schema, sync_config=config
        )

        print("SyncEngine configuration:")
        print(f"  Peer ID: {sync_engine.peer_id}")
        print(f"  Schema tables: {list(sync_engine.schema.keys())}")
        print(f"  Sync scope: {sync_engine.sync_config.scope.value}")
        print(f"  Tables to sync:")
        for table_name in schema:
            if sync_engine.sync_config.is_table_synced(table_name):
                print(f"    ✓ {table_name}")
            else:
                print(f"    ✗ {table_name}")


def example_10_save_and_reload():
    """Example 10: Save configuration to file and reload"""
    print("\n" + "=" * 60)
    print("EXAMPLE 10: Save and Reload Configuration")
    print("=" * 60)

    # Create configuration
    config = SyncConfig(
        scope=SyncScope.FILTERED,
        tables={
            "customer": TableSyncRule(
                filter=FilterExpression(
                    conditions=[
                        FilterCondition(
                            field="is_active", operator=FilterOperator.EQ, value=True
                        )
                    ]
                )
            ),
            "order": TableSyncRule(),
        },
    )

    # Save to file
    config_file = Path("/tmp/sync_config_demo.yaml")
    config.to_file(config_file, format="yaml")
    print(f"Saved configuration to: {config_file}")

    # Reload from file
    loaded_config = SyncConfig.from_file(config_file)
    print(f"Reloaded configuration from: {config_file}")
    print(f"Scope: {loaded_config.scope.value}")
    print(f"Tables: {list(loaded_config.tables.keys())}")


# ===== Main =====

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("SYNC CONFIGURATION EXAMPLES")
    print("=" * 60)

    # Run all examples
    example_1_sync_everything()
    example_2_sync_specific_tables()
    example_3_row_level_filtering()
    example_4_multi_tenant_filtering()
    example_5_exclude_sensitive_columns()
    example_6_include_only_specific_columns()
    example_7_load_from_file()
    example_8_complex_filtering()
    example_9_integration_with_sync_engine()
    example_10_save_and_reload()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
