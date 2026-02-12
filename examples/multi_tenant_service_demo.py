"""
Example: Multi-tenant service with Flask

This example shows how to:
1. Create a multi-tenant Flask server
2. Create multiple tenants (protected by TENANT_MASTER_KEY)
3. Store and retrieve encrypted secrets per tenant
4. Sync data between clients and the server with tenant isolation

Environment Variables:
- TENANT_MASTER_KEY: Master key for tenant management endpoints (/api/tenants/*)
- SECRET_KEY: Flask secret key
- FERNET_KEY: Encryption key for secrets (auto-generated if not provided)

Run:
  # Without master key (development)
  python examples/multi_tenant_service_demo.py

  # With master key (recommended)
  export TENANT_MASTER_KEY="your-secret-key"
  python examples/multi_tenant_service_demo.py
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import SQLModel, Field
from data_shuttle_bridge.sql.multi_tenant_service import create_multi_tenant_app
from data_shuttle_bridge.sql.sync import ConflictPolicy


# ===== Define Your Models =====


class Customer(SQLModel, table=True):
    """Customer model for demonstration."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str
    version: int = Field(default=1)


class Order(SQLModel, table=True):
    """Order model for demonstration."""

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    status: str
    total_cents: int
    version: int = Field(default=1)


# ===== Create the App =====

# Master database URL (contains tenant metadata)
MASTER_DB_URL = "sqlite:///bridge_master.db"

# Base path for tenant databases
TENANT_BASE_PATH = "."

# Create the app
app, tenant_mgr = create_multi_tenant_app(
    master_db_url=MASTER_DB_URL,
    models=[Customer, Order],
    secret_key=os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production"),
    fernet_key=None,  # Will auto-generate or load from FERNET_KEY env var
    tenant_base_path=TENANT_BASE_PATH,
    conflict_policy=ConflictPolicy.LWW,
    tenant_master_key=os.environ.get(
        "TENANT_MASTER_KEY"
    ),  # NEW: Master key for tenant management
)


# ===== Additional Demo Endpoints =====


@app.get("/api/demo/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "multi-tenant-bridge"}


@app.post("/api/demo/setup")
def setup_demo():
    """Set up a demo tenant with sample data."""
    from flask import g

    try:
        # Create a demo tenant if it doesn't exist
        existing = tenant_mgr.get_tenant("demo")
        if existing:
            tenant = existing
            api_key = tenant.api_key
        else:
            tenant = tenant_mgr.create_tenant(
                name="Demo Tenant", slug="demo", metadata={"demo": True}
            )
            api_key = tenant.api_key

        # Add some sample data
        session_factory = tenant_mgr.get_session_factory_for_tenant(tenant)
        with session_factory() as sess:
            # Check if we already have data
            from sqlmodel import select

            existing_customer = sess.exec(select(Customer)).first()

            if not existing_customer:
                # Create sample customers
                alice = Customer(name="Alice Smith", email="alice@example.com")
                bob = Customer(name="Bob Johnson", email="bob@example.com")
                sess.add(alice)
                sess.add(bob)
                sess.flush()

                # Create sample orders
                order1 = Order(customer_id=alice.id, status="shipped", total_cents=5999)
                order2 = Order(customer_id=bob.id, status="pending", total_cents=12999)
                sess.add(order1)
                sess.add(order2)
                sess.commit()

        # Store a demo secret
        tenant_mgr.set_secret(tenant, "api_token", "sk_demo_secret_token_xyz")

        return {
            "tenant_id": tenant.id,
            "tenant_slug": tenant.slug,
            "api_key": api_key,
            "message": "Demo tenant set up successfully",
        }, 201
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/api/demo/customers")
def list_customers():
    """List all customers for the authenticated tenant."""
    from flask import g
    from sqlmodel import select

    tenant = g.get("tenant")
    if not tenant:
        return {"error": "Invalid API key"}, 401

    session_factory = tenant_mgr.get_session_factory_for_tenant(tenant)
    with session_factory() as sess:
        customers = sess.exec(select(Customer)).all()
        return {
            "customers": [
                {"id": c.id, "name": c.name, "email": c.email} for c in customers
            ]
        }


@app.post("/api/demo/customers")
def create_customer():
    """Create a new customer for the authenticated tenant."""
    from flask import g

    tenant = g.get("tenant")
    if not tenant:
        return {"error": "Invalid API key"}, 401

    data = app.config.get("_request_data") or {}
    if not data:
        from flask import request

        data = request.get_json() or {}

    name = data.get("name")
    email = data.get("email")

    if not name or not email:
        return {"error": "name and email are required"}, 400

    session_factory = tenant_mgr.get_session_factory_for_tenant(tenant)
    with session_factory() as sess:
        customer = Customer(name=name, email=email)
        sess.add(customer)
        sess.commit()
        sess.refresh(customer)

        return {
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
        }, 201


# ===== CLI Helper =====


def show_usage():
    """Print usage instructions."""
    print(
        """
╔════════════════════════════════════════════════════════════════╗
║         Multi-Tenant Bridge Service Example                   ║
╚════════════════════════════════════════════════════════════════╝

This is a multi-tenant data sync service.

🚀 Usage:

1. Start the server:
   
   python examples/multi_tenant_service_demo.py

2. In another terminal, test with curl:

   # Set up demo tenant
   curl -X POST http://localhost:5000/api/demo/setup

   # Example output:
   # {
   #   "tenant_id": 1,
   #   "tenant_slug": "demo",
   #   "api_key": "...",
   #   "message": "Demo tenant set up successfully"
   # }

3. Use the API key to access tenant data:

   API_KEY="<the api_key from above>"

   # Create a tenant
   curl -X POST http://localhost:5000/api/tenants \\
     -H "Content-Type: application/json" \\
     -d '{"name": "Acme Corp", "slug": "acme"}'

   # Store a secret
   curl -X POST http://localhost:5000/api/secrets \\
     -H "X-API-Key: $API_KEY" \\
     -H "Content-Type: application/json" \\
     -d '{"key": "db_password", "secret": "my-secret-password"}'

   # Retrieve a secret
   curl -X GET http://localhost:5000/api/secrets/db_password \\
     -H "X-API-Key: $API_KEY"

   # List all secrets
   curl -X GET http://localhost:5000/api/secrets \\
     -H "X-API-Key: $API_KEY"

   # Get sync changes
   curl -X GET http://localhost:5000/api/sync/changes?since_id=0 \\
     -H "X-API-Key: $API_KEY"

   # Apply changes
   curl -X POST http://localhost:5000/api/sync/apply \\
     -H "X-API-Key: $API_KEY" \\
     -H "Content-Type: application/json" \\
     -d '{"changes": []}'

📚 Architecture:

- Each tenant has its own SQLite database file
- Master database tracks tenant metadata (names, API keys, etc.)
- Secrets are encrypted using Fernet encryption
- Sync state is isolated per tenant
- All sync operations filtered by tenant ID

🔐 Security:

- API keys required for all operations
- Secrets encrypted at rest
- Each tenant's data completely isolated
- Per-tenant database connections

📝 Next Steps:

1. Read MULTI_TENANT_SERVICE_README.md for full documentation
2. Build a client library using HttpPeerTransport
3. Add custom models specific to your use case
4. Deploy to production with proper secret management
    """
    )


if __name__ == "__main__":
    # Show usage
    show_usage()

    # Start Flask server
    print("\n▶ Starting server at http://localhost:5000")
    print("  Press Ctrl+C to stop\n")

    app.run(debug=True, port=5000)
