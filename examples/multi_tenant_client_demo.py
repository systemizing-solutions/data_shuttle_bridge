"""
Example: Multi-tenant bridge client for syncing with service

This example shows how to:
1. Create a local database with sample models
2. Connect to a bridge service using tenant API key
3. Sync data bidirectionally with tenant isolation
4. Store and retrieve encrypted secrets
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import SQLModel, Field, select, create_engine, Session
from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import (
    attach_change_hooks_for_models,
    set_id_generator,
)
from data_shuttle_bridge.sql.bridge_client import BridgeClient, BridgeAdminClient


# ===== Models (must match server) =====


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str
    version: int = Field(default=1)


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    status: str
    total_cents: int
    version: int = Field(default=1)


# ===== Setup =====


def setup_local_db(db_url: str):
    """Initialize local database."""
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def demo_admin():
    """Demonstrate admin operations."""
    print("\n" + "=" * 60)
    print("ADMIN: Setting up demo tenant")
    print("=" * 60)

    admin = BridgeAdminClient("http://localhost:5000")

    try:
        # Create a new tenant
        print("\n1. Creating tenant 'acme-corp'...")
        tenant_data = admin.create_tenant(
            name="ACME Corporation",
            slug="acme-corp",
            metadata={"industry": "Technology", "region": "US-East"},
        )
        print(f"   ✓ Tenant created: {tenant_data['name']}")
        api_key = tenant_data["api_key"]
        print(f"   ✓ API Key: {api_key[:20]}...")

        return api_key
    except Exception as e:
        if "already exists" in str(e).lower():
            print("   ℹ Tenant already exists, using existing...")
            # List tenants to find the API key
            tenants = admin.list_tenants()
            for t in tenants:
                if t["slug"] == "acme-corp":
                    # We can't get the API key directly, so we'll use a fresh one
                    # In practice, you'd store this when creating
                    print(
                        f"   Found existing tenant, but need API key from creation time"
                    )
                    print("   Using demo service setup instead...")
                    return None
        raise


def demo_client_sync(api_key: str):
    """Demonstrate client sync operations."""
    if not api_key:
        print("\n" + "=" * 60)
        print("CLIENT: Requesting demo setup")
        print("=" * 60)

        admin = BridgeAdminClient("http://localhost:5000")
        resp = admin.session.post(
            "http://localhost:5000/api/demo/setup",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        api_key = data["api_key"]
        print(f"\n✓ Demo tenant set up")
        print(f"  Tenant: {data['tenant_slug']}")
        print(f"  API Key: {api_key[:20]}...")

    print("\n" + "=" * 60)
    print("CLIENT: Syncing data")
    print("=" * 60)

    # Create client
    client = BridgeClient("http://localhost:5000", api_key)

    # Setup local database
    print("\n1. Setting up local database...")
    db_url = "sqlite:///bridge_client_demo.db"
    engine = setup_local_db(db_url)
    print("   ✓ Local database ready")

    # Attach change hooks
    models = [Customer, Order]
    attach_change_hooks_for_models(models)
    SCHEMA = build_schema(models)

    set_id_generator("local-client")

    # Create local session
    print("\n2. Creating sync engine...")
    with Session(engine) as sess:
        sync_engine = SyncEngine(
            session=sess,
            peer_id="bridge-server",
            schema=SCHEMA,
            policy=ConflictPolicy.LWW,
            node_id="local-client",
        )

        # Get remote transport
        peer_transport = client.as_peer_transport()

        # Sync: pull first, then push
        print("\n3. Pulling changes from server...")
        pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
        print(f"   ✓ Pulled {pulled} changes, Pushed {pushed} changes")

        # Check what we have
        print("\n4. Local data after sync:")
        customers = sess.exec(select(Customer)).all()
        print(f"   - Customers: {len(customers)}")
        for c in customers:
            print(f"     • {c.name} ({c.email})")

        orders = sess.exec(select(Order)).all()
        print(f"   - Orders: {len(orders)}")
        for o in orders:
            print(
                f"     • Order {o.id}: Customer {o.customer_id}, Status: {o.status}, Total: ${o.total_cents/100:.2f}"
            )

        # Add a new customer locally
        if not customers:
            print("\n5. Creating new local data...")
            new_customer = Customer(
                name="Local Client Inc", email="contact@localclient.com"
            )
            sess.add(new_customer)
            sess.commit()
            sess.refresh(new_customer)
            print(f"   ✓ Created customer: {new_customer.name} (ID: {new_customer.id})")

            # Sync again to push the new customer
            print("\n6. Syncing again to push new data...")
            pulled, pushed = sync_engine.pull_then_push(peer_transport, batch=100)
            print(f"   ✓ Pulled {pulled} changes, Pushed {pushed} changes")

    # Secrets management
    print("\n" + "=" * 60)
    print("CLIENT: Managing secrets")
    print("=" * 60)

    print("\n1. Storing secrets...")
    client.set_secret("db_password", "super_secret_password_123")
    client.set_secret("api_token", "sk_live_abc123xyz")
    print("   ✓ Secrets stored")

    print("\n2. Retrieving secrets...")
    db_pass = client.get_secret("db_password")
    print(f"   ✓ db_password: {db_pass}")

    api_token = client.get_secret("api_token")
    print(f"   ✓ api_token: {api_token}")

    print("\n3. Listing all secrets...")
    keys = client.list_secrets()
    print(f"   ✓ Secret keys: {keys}")

    print("\n4. Deleting a secret...")
    client.delete_secret("api_token")
    print("   ✓ api_token deleted")

    keys = client.list_secrets()
    print(f"   ✓ Remaining secrets: {keys}")


def main():
    """Run the demo."""
    print(
        """
╔════════════════════════════════════════════════════════════════╗
║         Multi-Tenant Bridge Client Sync Example               ║
╚════════════════════════════════════════════════════════════════╝

This example demonstrates:
1. Admin: Creating a new tenant
2. Client: Connecting to the service
3. Client: Syncing data with the server
4. Client: Managing encrypted secrets

Prerequisites:
- The bridge service must be running:
  
  python examples/multi_tenant_service_demo.py

Then run this script in another terminal.
    """
    )

    # Try admin operations first (may fail if server has auth)
    api_key = None
    try:
        api_key = demo_admin()
    except Exception as e:
        print(f"\n   Note: Admin operations failed: {e}")
        print("   This is normal - will use demo setup instead")

    # Run client sync
    try:
        demo_client_sync(api_key)
    except requests.exceptions.ConnectionError:
        print("\n❌ Error: Could not connect to server")
        print("   Make sure the bridge service is running:")
        print("   python examples/multi_tenant_service_demo.py")
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n" + "=" * 60)
    print("✓ Demo completed successfully!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    import requests

    exit(main())
