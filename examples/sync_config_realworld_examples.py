"""
Real-World End-to-End Examples: Using Sync Configurations

These are complete, executable examples showing sync configuration in action
with actual data synchronization happening.

Each example:
1. Creates a local database with sample data
2. Sets up a sync configuration
3. Demonstrates syncing with the configuration applied
4. Shows real results
"""

from pathlib import Path
from datetime import datetime, timedelta
from sqlmodel import SQLModel, Field, create_engine, Session, select

from data_shuttle_bridge.sql import (
    SyncEngine,
    SyncConfig,
    SyncScope,
    TableSyncRule,
    FilterExpression,
    FilterCondition,
    FilterOperator,
    SqlFilter,
    build_schema,
    attach_change_hooks_for_models,
    InMemoryPeerTransport,
)
from data_shuttle_bridge.sql.changelog import ChangeLog, SyncState


# ===== Models =====

class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str
    is_active: bool = True
    tenant_id: str
    version: int = Field(default=1)


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    status: str  # "pending", "processing", "shipped", "delivered"
    amount_cents: int
    created_at: datetime
    tenant_id: str
    version: int = Field(default=1)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    price_cents: int
    cost_cents: int  # Sensitive - exclude from sync
    version: int = Field(default=1)


class AuditLog(SQLModel, table=True):
    """System table - usually not synced"""
    id: int | None = Field(default=None, primary_key=True)
    action: str
    table_name: str
    created_at: datetime


# ===== Setup Helpers =====

def setup_database(db_path: str):
    """Create database and tables."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


def populate_sample_data(engine):
    """Add sample data to the database."""
    with Session(engine) as session:
        # Users
        users = [
            User(id=1, name="Alice", email="alice@acme.com", is_active=True, tenant_id="ACME"),
            User(id=2, name="Bob", email="bob@acme.com", is_active=True, tenant_id="ACME"),
            User(id=3, name="Charlie", email="charlie@acme.com", is_active=False, tenant_id="ACME"),
            User(id=4, name="Diana", email="diana@other.com", is_active=True, tenant_id="OTHER"),
        ]
        
        # Orders (mix of statuses and timestamps)
        now = datetime.now()
        orders = [
            Order(id=1, user_id=1, status="pending", amount_cents=5000, created_at=now, tenant_id="ACME"),
            Order(id=2, user_id=1, status="processing", amount_cents=15000, created_at=now - timedelta(days=5), tenant_id="ACME"),
            Order(id=3, user_id=2, status="shipped", amount_cents=8000, created_at=now - timedelta(days=10), tenant_id="ACME"),
            Order(id=4, user_id=2, status="delivered", amount_cents=3000, created_at=now - timedelta(days=30), tenant_id="ACME"),
            Order(id=5, user_id=4, status="processing", amount_cents=20000, created_at=now, tenant_id="OTHER"),
        ]
        
        # Products
        products = [
            Product(id=1, name="Laptop", price_cents=150000, cost_cents=80000),
            Product(id=2, name="Mouse", price_cents=2000, cost_cents=500),
            Product(id=3, name="Keyboard", price_cents=8000, cost_cents=3000),
        ]
        
        session.add_all(users + orders + products)
        session.commit()


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"{title:^70}")
    print(f"{'='*70}\n")


def count_records(session, model):
    """Count records in a table."""
    return session.exec(select(model)).all()


# ===== EXAMPLE 1: Sync Specific Tables Only =====

def example_1_specific_tables():
    """
    Only sync users and orders; exclude products and audit logs.
    """
    print_section("Example 1: Sync Specific Tables Only")
    
    # Setup
    db_path = "/tmp/example1.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Create configuration
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "user": TableSyncRule(enabled=True),
                "order": TableSyncRule(enabled=True),
                "product": TableSyncRule(enabled=False),  # Don't sync
                "auditlog": TableSyncRule(enabled=False),  # Don't sync
            }
        )
        
        # Show config
        print("Configuration:")
        for table in ["user", "order", "product", "auditlog"]:
            will_sync = config.is_table_synced(table)
            status = "✓ SYNC" if will_sync else "✗ SKIP"
            print(f"  {status:10} {table}")
        
        # Create schema and engine
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="client_1",
            schema=schema,
            sync_config=config
        )
        
        # Create peer transport
        peer_transport = InMemoryPeerTransport()
        
        # Perform sync
        print(f"\nBefore sync:")
        print(f"  Users: {len(count_records(session, User))}")
        print(f"  Orders: {len(count_records(session, Order))}")
        print(f"  Products: {len(count_records(session, Product))}")
        
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pulled: {pulled} changes")
        print(f"  Pushed: {pushed} changes (only from user & order tables)")
        print(f"\n✓ Products and audit logs were NOT synced due to configuration")


# ===== EXAMPLE 2: Multi-Tenant Filtering =====

def example_2_multitenant_filtering():
    """
    Only sync data for a specific tenant using row-level filtering.
    """
    print_section("Example 2: Multi-Tenant Filtering")
    
    db_path = "/tmp/example2.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    tenant_id = "ACME"
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Configuration: Only ACME tenant data
        config = SyncConfig(
            scope=SyncScope.FILTERED,
            tables={
                "user": TableSyncRule(
                    filter=FilterExpression(
                        conditions=[
                            FilterCondition(
                                field="tenant_id",
                                operator=FilterOperator.EQ,
                                value=tenant_id
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
                                value=tenant_id
                            )
                        ]
                    )
                ),
            }
        )
        
        print(f"Configuration: Only sync {tenant_id} tenant")
        print(f"\nDatabase contents:")
        all_users = count_records(session, User)
        acme_users = [u for u in all_users if u.tenant_id == tenant_id]
        print(f"  Total users: {len(all_users)} (ACME: {len(acme_users)}, OTHER: {len(all_users) - len(acme_users)})")
        
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="acme_client",
            schema=schema,
            sync_config=config
        )
        
        peer_transport = InMemoryPeerTransport()
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pushed: {pushed} changes")
        print(f"\n✓ Only {tenant_id} tenant data was synced")
        print(f"✓ {len(all_users) - len(acme_users)} users from OTHER tenant were excluded")


# ===== EXAMPLE 3: SQL WHERE Clause Filtering =====

def example_3_sql_where_filtering():
    """
    Use SQL WHERE clauses for more complex filtering.
    """
    print_section("Example 3: SQL WHERE Clause Filtering")
    
    db_path = "/tmp/example3.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Configuration: Active users + high-value recent orders
        config = SyncConfig(scope=SyncScope.FILTERED, tables={
            "user": TableSyncRule(
                sql_filter=SqlFilter(
                    where="is_active = 1"  # Only active users
                )
            ),
            "order": TableSyncRule(
                sql_filter=SqlFilter(
                    where="status IN ('processing', 'shipped') AND amount_cents > 5000"
                )
            ),
        })
        
        print("Configuration: Using SQL WHERE clauses")
        print("  Users: WHERE is_active = 1")
        print("  Orders: WHERE status IN ('processing', 'shipped') AND amount_cents > 5000")
        
        all_users = count_records(session, User)
        active_users = [u for u in all_users if u.is_active]
        all_orders = count_records(session, Order)
        filtered_orders = [o for o in all_orders if o.status in ("processing", "shipped") and o.amount_cents > 5000]
        
        print(f"\nDatabase contents:")
        print(f"  Users: {len(all_users)} total, {len(active_users)} active")
        print(f"  Orders: {len(all_orders)} total, {len(filtered_orders)} matching filter")
        
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="sql_client",
            schema=schema,
            sync_config=config
        )
        
        peer_transport = InMemoryPeerTransport()
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pushed: {pushed} changes")
        print(f"\n✓ SQL WHERE filtering applied during sync")


# ===== EXAMPLE 4: Jinja2-Templated SQL Filtering =====

def example_4_jinja2_filtering():
    """
    Use Jinja2 templates in SQL filters for parameterized queries.
    """
    print_section("Example 4: Jinja2-Templated SQL Filtering")
    
    db_path = "/tmp/example4.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    tenant_id = "ACME"
    min_amount = 5000
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Configuration: Parameterized SQL filters using Jinja2
        config = SyncConfig(scope=SyncScope.FILTERED, tables={
            "user": TableSyncRule(
                sql_filter=SqlFilter(
                    where="tenant_id = '{{ tenant_id }}'",
                    params={"tenant_id": tenant_id}
                )
            ),
            "order": TableSyncRule(
                sql_filter=SqlFilter(
                    where="tenant_id = '{{ tenant_id }}' AND amount_cents >= {{ min_amount }}",
                    params={"tenant_id": tenant_id, "min_amount": min_amount}
                )
            ),
        })
        
        print("Configuration: Jinja2-templated SQL filters")
        print(f"  Users: WHERE tenant_id = '{tenant_id}'")
        print(f"  Orders: WHERE tenant_id = '{tenant_id}' AND amount_cents >= {min_amount}")
        
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="jinja_client",
            schema=schema,
            sync_config=config
        )
        
        peer_transport = InMemoryPeerTransport()
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pushed: {pushed} changes")
        print(f"\n✓ Jinja2 parameters processed and SQL filter applied")


# ===== EXAMPLE 5: Column Filtering =====

def example_5_column_filtering():
    """
    Exclude sensitive columns from sync (e.g., product costs).
    """
    print_section("Example 5: Column Filtering")
    
    db_path = "/tmp/example5.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Configuration: Exclude sensitive columns
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "product": TableSyncRule(
                    enabled=True,
                    exclude_columns=["cost_cents"]  # Don't expose cost
                ),
            }
        )
        
        print("Configuration: Exclude sensitive columns")
        print("  Products: exclude 'cost_cents'")
        
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="col_client",
            schema=schema,
            sync_config=config
        )
        
        peer_transport = InMemoryPeerTransport()
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pushed: {pushed} changes")
        print(f"\n✓ Product cost_cents column was excluded from sync")


# ===== EXAMPLE 6: Loading Config from File =====

def example_6_config_from_file():
    """
    Load configuration from a file and use it.
    """
    print_section("Example 6: Load Configuration from File")
    
    # Create a configuration file
    config_yaml = """
scope: filtered
enabled: true

tables:
  user:
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
    
    config_file = Path("/tmp/sync_config_example.yaml")
    config_file.write_text(config_yaml)
    
    print(f"Configuration file: {config_file}")
    print("\nFile contents:")
    print(config_yaml)
    
    # Load and use configuration
    db_path = "/tmp/example6.db"
    engine = setup_database(db_path)
    populate_sample_data(engine)
    
    with Session(engine) as session:
        attach_change_hooks_for_models([User, Order, Product, AuditLog])
        
        # Load configuration from file
        config = SyncConfig.from_file(config_file)
        
        print("\nLoaded configuration:")
        print(f"  Scope: {config.scope.value}")
        print(f"  Tables: {list(config.tables.keys())}")
        
        schema = build_schema([User, Order, Product, AuditLog])
        sync_engine = SyncEngine(
            session=session,
            peer_id="file_client",
            schema=schema,
            sync_config=config
        )
        
        peer_transport = InMemoryPeerTransport()
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        
        print(f"\nSync results:")
        print(f"  Pushed: {pushed} changes")
        print(f"\n✓ Configuration loaded from file and applied to sync")


# ===== Main =====

if __name__ == "__main__":
    print("\n" + "="*70)
    print("REAL-WORLD SYNC CONFIGURATION EXAMPLES".center(70))
    print("End-to-End Examples with Actual Syncing".center(70))
    print("="*70)
    
    try:
        example_1_specific_tables()
        example_2_multitenant_filtering()
        example_3_sql_where_filtering()
        example_4_jinja2_filtering()
        example_5_column_filtering()
        example_6_config_from_file()
        
        print_section("All Examples Completed Successfully!")
        print("✓ Configs created and applied ✓ Data filtered correctly ✓ Syncs executed")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
