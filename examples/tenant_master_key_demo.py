"""
Example: Using Tenant Master Key Authentication

This script demonstrates how to use the X-Tenant-Key header
to authenticate tenant management API calls.

Run the server first:
  export TENANT_MASTER_KEY="my-secret-master-key"
  python examples/multi_tenant_service_demo.py

Then run this script:
  python examples/tenant_master_key_demo.py
"""

import os
import requests
import json

# Configuration
BASE_URL = "http://localhost:5000"

# Get master key from environment or use default for demo
MASTER_KEY = os.environ.get("TENANT_MASTER_KEY", "my-secret-master-key")

# Headers with master key authentication
MASTER_HEADERS = {"X-Tenant-Key": MASTER_KEY, "Content-Type": "application/json"}


# Helper function to print responses nicely
def print_response(title, status, data):
    """Pretty print response."""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(f"Status: {status}")
    print(f"Response:\n{json.dumps(data, indent=2)}")


def main():
    print("🔐 Tenant Master Key Authentication Demo")
    print(f"Using Master Key: {MASTER_KEY[:10]}...")

    # ===== 1. CREATE TENANT =====
    print("\n\n📝 Creating a new tenant...")
    response = requests.post(
        f"{BASE_URL}/api/tenants",
        headers=MASTER_HEADERS,
        json={
            "name": "Demo Company",
            "slug": "demo-company",
            "metadata": {"tier": "premium"},
        },
    )
    tenant_data = response.json()
    print_response("CREATE TENANT", response.status_code, tenant_data)

    if response.status_code != 201:
        print("❌ Failed to create tenant!")
        return

    tenant_id = tenant_data["id"]
    tenant_api_key = tenant_data["api_key"]
    print(f"✅ Tenant created with ID: {tenant_id}")
    print(f"📌 Tenant API Key: {tenant_api_key}")

    # ===== 2. LIST ALL TENANTS =====
    print("\n\n📋 Listing all tenants...")
    response = requests.get(f"{BASE_URL}/api/tenants", headers=MASTER_HEADERS)
    print_response("LIST TENANTS", response.status_code, response.json())

    # ===== 3. GET SPECIFIC TENANT =====
    print("\n\n🔍 Getting specific tenant...")
    response = requests.get(
        f"{BASE_URL}/api/tenants/{tenant_id}", headers=MASTER_HEADERS
    )
    print_response("GET TENANT", response.status_code, response.json())

    # ===== 4. TRY WITHOUT MASTER KEY (Should fail) =====
    print("\n\n❌ Attempting to create tenant WITHOUT master key...")
    response = requests.post(
        f"{BASE_URL}/api/tenants",
        headers={"Content-Type": "application/json"},
        json={"name": "Unauthorized Tenant", "slug": "unauthorized"},
    )
    print_response("CREATE TENANT (NO AUTH)", response.status_code, response.json())

    # ===== 5. TRY WITH WRONG MASTER KEY (Should fail) =====
    print("\n\n❌ Attempting to create tenant with WRONG master key...")
    wrong_headers = {
        "X-Tenant-Key": "wrong-master-key",
        "Content-Type": "application/json",
    }
    response = requests.post(
        f"{BASE_URL}/api/tenants",
        headers=wrong_headers,
        json={"name": "Unauthorized Tenant", "slug": "unauthorized"},
    )
    print_response("CREATE TENANT (WRONG KEY)", response.status_code, response.json())

    # ===== 6. USE TENANT API KEY TO STORE SECRETS =====
    print("\n\n🔐 Storing a secret for the tenant (using tenant's API key)...")
    response = requests.post(
        f"{BASE_URL}/api/secrets",
        headers={"X-API-Key": tenant_api_key, "Content-Type": "application/json"},
        json={"key": "database_password", "secret": "super-secret-password-123"},
    )
    print_response("SET SECRET", response.status_code, response.json())

    # ===== 7. RETRIEVE THE SECRET =====
    print("\n\n🔑 Retrieving the stored secret...")
    response = requests.get(
        f"{BASE_URL}/api/secrets/database_password",
        headers={"X-API-Key": tenant_api_key},
    )
    print_response("GET SECRET", response.status_code, response.json())

    # ===== 8. DELETE TENANT =====
    print("\n\n🗑️  Deleting the tenant...")
    response = requests.delete(
        f"{BASE_URL}/api/tenants/{tenant_id}", headers=MASTER_HEADERS
    )
    print_response("DELETE TENANT", response.status_code, response.json())

    print("\n\n" + "=" * 60)
    print("✅ Demo completed!")
    print("=" * 60)
    print("\nKey Takeaways:")
    print("1. Tenant Management (/api/tenants/*) requires X-Tenant-Key header")
    print("2. Tenant Operations (/api/secrets/*) require X-API-Key header (per-tenant)")
    print("3. Without master key, management endpoints return 401")
    print("4. Master key is validated from environment variable TENANT_MASTER_KEY")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print("\n❌ Error: Could not connect to server!")
        print("Make sure the server is running:")
        print("  export TENANT_MASTER_KEY='my-secret-master-key'")
        print("  python examples/multi_tenant_service_demo.py")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
