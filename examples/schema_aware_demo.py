"""
Complete schema-aware multi-tenant demo.

Shows:
1. Server setup with schema versioning
2. Client connection and sync
3. Schema drift detection
4. Version tracking
5. Secrets management
"""

import os
import sys
from datetime import datetime

from sqlmodel import SQLModel, Field, create_engine
from sqlalchemy.orm import sessionmaker

# Server imports
from data_shuttle_bridge.sql.schema_aware_multi_tenant_service import (
    create_schema_aware_multi_tenant_app,
    SchemAwareTenantManager,
)

# Client imports
from data_shuttle_bridge.sql.schema_aware_bridge_client import (
    SchemAwareBridgeClient,
    SchemAwareBridgeAdminClient,
)


# ===========================
# Sample Models
# ===========================


class Customer(SQLModel, table=True):
    """Customer model."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Order(SQLModel, table=True):
    """Order model."""

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    total: float
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Product(SQLModel, table=True):
    """Product model."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    price: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ===========================
# Server Setup
# ===========================


def setup_server():
    """Set up the schema-aware multi-tenant server."""
    print("=" * 70)
    print("SETTING UP SCHEMA-AWARE MULTI-TENANT SERVER")
    print("=" * 70)

    # Create master database
    master_db_url = "sqlite:///./master_server.db"

    # Initialize Flask app
    app, tenant_mgr = create_schema_aware_multi_tenant_app(
        master_db_url=master_db_url,
        models=[Customer, Order, Product],
        secret_key="dev-secret-key",
        fernet_key=b"dev-fernet-key-32-chars-long!!",
        tenant_base_path=".",
        tenant_master_key="master-key-123",
    )

    print(f"✓ Flask app initialized")
    print(f"✓ Master database: {master_db_url}")
    print(f"✓ Models registered: Customer, Order, Product")
    print()

    return app, tenant_mgr


def create_demo_tenants(tenant_mgr: SchemAwareTenantManager):
    """Create demo tenants."""
    print("=" * 70)
    print("CREATING DEMO TENANTS")
    print("=" * 70)

    # Create tenant 1
    tenant1 = tenant_mgr.create_tenant(
        name="ACME Corp",
        slug="acme-corp",
        metadata={"industry": "Manufacturing"},
    )
    print(f"✓ Created tenant: {tenant1.name} (ID: {tenant1.id})")
    print(f"  API Key: {tenant1.api_key}")
    print(f"  Schema Version: {tenant1.current_schema_version}")

    # Create tenant 2
    tenant2 = tenant_mgr.create_tenant(
        name="TechStart Inc",
        slug="techstart",
        metadata={"industry": "Software"},
    )
    print(f"✓ Created tenant: {tenant2.name} (ID: {tenant2.id})")
    print(f"  API Key: {tenant2.api_key}")
    print(f"  Schema Version: {tenant2.current_schema_version}")
    print()

    return tenant1, tenant2


def demo_secrets_management(tenant_mgr: SchemAwareTenantManager, tenant):
    """Demonstrate secrets management."""
    print("=" * 70)
    print("SECRETS MANAGEMENT DEMO")
    print("=" * 70)

    # Store secrets
    tenant_mgr.set_secret(tenant, "db_password", "super-secret-password")
    tenant_mgr.set_secret(tenant, "api_token", "token-xyz-123")
    print(f"✓ Stored secrets for {tenant.name}")

    # List secrets
    keys = tenant_mgr.list_secrets(tenant)
    print(f"✓ Secrets: {keys}")

    # Retrieve secret
    password = tenant_mgr.get_secret(tenant, "db_password")
    print(f"✓ Retrieved password: {password}")

    # Delete secret
    tenant_mgr.delete_secret(tenant, "api_token")
    print(f"✓ Deleted api_token")
    print()


def demo_schema_management(tenant_mgr: SchemAwareTenantManager, tenant):
    """Demonstrate schema version tracking."""
    print("=" * 70)
    print("SCHEMA VERSION TRACKING DEMO")
    print("=" * 70)

    # Get schema versions
    registry = tenant_mgr.get_schema_registry_for_tenant(tenant)
    registry_session = sessionmaker(bind=tenant_mgr._tenant_engines[tenant.id])()

    try:
        versions = registry.list_schema_versions(registry_session, "models")
        print(f"✓ Schema versions for {tenant.name}:")
        for v in versions:
            print(f"  - Version {v.version} (created: {v.created_at})")
    finally:
        registry_session.close()

    print()


# ===========================
# Client Setup & Demo
# ===========================


def setup_client(api_key: str):
    """Set up a schema-aware bridge client."""
    print("=" * 70)
    print("SETTING UP SCHEMA-AWARE BRIDGE CLIENT")
    print("=" * 70)

    client = SchemAwareBridgeClient(
        server_url="http://localhost:5000",
        api_key=api_key,
        local_db_url="sqlite:///./local_client.db",
        models=[Customer, Order, Product],
    )

    print(f"✓ Client initialized")
    print(f"✓ Local database: sqlite:///./local_client.db")
    print(f"✓ Connected to: http://localhost:5000")
    print()

    return client


def demo_client_sync(client: SchemAwareBridgeClient):
    """Demonstrate client sync with schema awareness."""
    print("=" * 70)
    print("CLIENT SYNC DEMO")
    print("=" * 70)

    try:
        # Check schema info
        schema_info = client.get_schema_info()
        print(f"✓ Schema info from server:")
        print(f"  Current version: {schema_info['current_version']}")
        print(f"  Total versions: {len(schema_info['versions'])}")

        # Check for drift
        drift_info = client.check_for_drift()
        print(f"\n✓ Schema drift check:")
        print(f"  Drift detected: {drift_info['drift_detected']}")
        print(f"  Current version: {drift_info['current_version']}")

        # Perform sync
        print(f"\n✓ Performing bidirectional sync...")
        sync_result = client.sync()
        print(f"  Local changes sent: {sync_result['local_changes']}")
        print(f"  Remote changes received: {sync_result['remote_changes']}")
        print(f"  Schema drift detected: {sync_result['schema_drift']}")
        if sync_result["new_schema_version"]:
            print(f"  New schema version: {sync_result['new_schema_version']}")

    except Exception as e:
        print(f"✗ Sync error: {e}")

    print()


def demo_admin_client():
    """Demonstrate admin client for tenant management."""
    print("=" * 70)
    print("ADMIN CLIENT DEMO")
    print("=" * 70)

    admin = SchemAwareBridgeAdminClient(
        server_url="http://localhost:5000",
        master_key="master-key-123",
    )

    try:
        # List tenants
        tenants = admin.list_tenants()
        print(f"✓ Tenants on server: {len(tenants)}")
        for t in tenants:
            print(f"  - {t['name']} (schema v{t['schema_version']})")

    except Exception as e:
        print(f"✗ Admin error: {e}")

    print()


# ===========================
# Main Demo Flow
# ===========================


def main():
    """Run complete schema-aware multi-tenant demo."""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  SCHEMA-AWARE MULTI-TENANT BRIDGE DEMO".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    print("\n")

    # ===== SERVER SETUP =====
    app, tenant_mgr = setup_server()

    # ===== CREATE TENANTS =====
    tenant1, tenant2 = create_demo_tenants(tenant_mgr)

    # ===== DEMO 1: Secrets Management =====
    demo_secrets_management(tenant_mgr, tenant1)

    # ===== DEMO 2: Schema Version Tracking =====
    demo_schema_management(tenant_mgr, tenant1)

    # ===== CLIENT SETUP =====
    client1 = setup_client(tenant1.api_key)

    # ===== DEMO 3: Client Sync =====
    print("NOTE: To test client sync, start the Flask server in another terminal:")
    print(
        '  python -c "from examples.schema_aware_demo import main; app, _ = setup_server(); app.run(debug=True)"'
    )
    print()

    # ===== ADMIN CLIENT =====
    print("NOTE: To test admin client, ensure Flask server is running")
    print()

    # ===== SUMMARY =====
    print("=" * 70)
    print("DEMO SUMMARY")
    print("=" * 70)
    print(
        """
✓ Schema-aware multi-tenant service features:
  • Per-tenant schema registries
  • Automatic schema version tracking
  • Drift detection and management
  • Encrypted secrets storage
  • Bidirectional sync with version awareness
  • Admin APIs for tenant management

✓ Key Components:
  • SchemAwareTenantManager: Tenant lifecycle + schema registry
  • SchemaRegistry: Per-tenant schema versioning
  • DriftPolicyEngine: Policy-driven defaults for schema changes
  • ConsolidationViewBuilder: Unified querying across versions
  • SchemAwareBridgeClient: Client-side sync with version tracking
  • SchemAwareBridgeAdminClient: Admin operations on server

✓ Integration Points:
  • /api/tenants/*: Tenant management (requires X-Tenant-Key)
  • /api/schema/versions: Get tenant schema versions
  • /api/schema/check-drift: Detect and apply schema changes
  • /api/sync/changes: Pull changes with version metadata
  • /api/sync/apply: Push changes with version awareness
  • /api/secrets: Store/retrieve encrypted secrets

Next Steps:
1. Run the Flask server:
   flask --app examples.schema_aware_demo run

2. Connect a client and perform sync:
   - Use SchemAwareBridgeClient(api_key=tenant.api_key)
   - Call client.sync() for bidirectional sync
   - Version tracking is automatic

3. Use admin client for tenant operations:
   - Create/list/delete tenants
   - Monitor schema versions
   - Manage across multiple tenants
    """
    )
    print()


if __name__ == "__main__":
    main()
