"""
Multi-tenant service client for syncing with tenant-specific endpoints.

Usage:
    client = BridgeClient("http://localhost:5000", "tenant_api_key")
    pulled, pushed = client.sync_all(local_engine, models)
"""

from __future__ import annotations

from typing import Dict, List, Optional

import requests
from data_shuttle_bridge.sql.transport import HttpPeerTransport
from data_shuttle_bridge.sql.typing_ import ChangePayload


class BridgeClient:
    """Client for connecting to a multi-tenant bridge service."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
    ):
        """
        Initialize a bridge client.

        Args:
            base_url: Base URL of the bridge service (e.g., "http://localhost:5000")
            api_key: API key for tenant authentication
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> requests.Response:
        """Make a request to the service."""
        url = f"{self.base_url}{endpoint}"
        resp = self.session.request(
            method,
            url,
            json=json,
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp

    # ===== Tenant Info =====

    def get_tenant_info(self) -> Dict:
        """Get info about the authenticated tenant."""
        resp = self._request("GET", "/api/me")
        return resp.json()

    # ===== Secrets Management =====

    def set_secret(self, key: str, secret: str) -> None:
        """Set a secret."""
        self._request(
            "POST",
            "/api/secrets",
            json={
                "key": key,
                "secret": secret,
            },
        )

    def get_secret(self, key: str) -> str:
        """Get a secret."""
        resp = self._request("GET", f"/api/secrets/{key}")
        data = resp.json()
        return data["secret"]

    def delete_secret(self, key: str) -> None:
        """Delete a secret."""
        self._request("DELETE", f"/api/secrets/{key}")

    def list_secrets(self) -> List[str]:
        """List all secret keys."""
        resp = self._request("GET", "/api/secrets")
        data = resp.json()
        return data.get("secrets", [])

    # ===== Data Sync =====

    def get_changes(self, since_id: int = 0, limit: int = 1000) -> List[ChangePayload]:
        """Get changes since a given ID."""
        resp = self._request(
            "GET",
            "/api/sync/changes",
            params={"since_id": since_id, "limit": limit},
        )
        data = resp.json()
        return data.get("changes", [])

    def apply_changes(self, changes: List[ChangePayload]) -> None:
        """Apply changes to the remote."""
        self._request(
            "POST",
            "/api/sync/apply",
            json={
                "changes": changes,
            },
        )

    def ack_changes(self, change_id: int) -> None:
        """Acknowledge receipt of changes up to change_id."""
        # Optional: not all servers implement this
        try:
            self._request(
                "POST",
                "/api/sync/ack",
                json={
                    "change_id": change_id,
                },
            )
        except requests.exceptions.HTTPError:
            pass

    # ===== HttpPeerTransport Bridge =====

    def as_peer_transport(self) -> HttpPeerTransport:
        """
        Convert this client to an HttpPeerTransport for use with SyncEngine.

        Returns:
            HttpPeerTransport wrapping this client
        """
        return _BridgeHttpPeerTransport(self)


class _BridgeHttpPeerTransport(HttpPeerTransport):
    """
    HttpPeerTransport implementation for Bridge clients.

    This allows using a BridgeClient directly with SyncEngine.pull_then_push().
    """

    def __init__(self, client: BridgeClient):
        self.client = client

    def get_changes_since(
        self, since_id: int, limit: int = 1000
    ) -> List[ChangePayload]:
        """Get changes from remote."""
        return self.client.get_changes(since_id, limit)

    def apply_changes(self, changes: List[ChangePayload]) -> None:
        """Apply changes to remote."""
        self.client.apply_changes(changes)

    def ack(self, change_id: int) -> None:
        """Acknowledge receipt of changes."""
        self.client.ack_changes(change_id)


# ===== Tenant Admin Client =====


class BridgeAdminClient:
    """Admin client for managing tenants (no tenant-specific auth)."""

    def __init__(self, base_url: str, admin_key: str | None = None):
        """
        Initialize a bridge admin client.

        Args:
            base_url: Base URL of the bridge service
            admin_key: Optional admin API key for privileged operations
        """
        self.base_url = base_url.rstrip("/")
        self.admin_key = admin_key
        self.session = requests.Session()
        if admin_key:
            self.session.headers.update({"X-Admin-Key": admin_key})
        self.session.headers.update({"Content-Type": "application/json"})

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> requests.Response:
        """Make a request to the service."""
        url = f"{self.base_url}{endpoint}"
        resp = self.session.request(
            method,
            url,
            json=json,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp

    def create_tenant(
        self, name: str, slug: str | None = None, metadata: Dict | None = None
    ) -> Dict:
        """Create a new tenant."""
        resp = self._request(
            "POST",
            "/api/tenants",
            json={
                "name": name,
                "slug": slug,
                "metadata": metadata or {},
            },
        )
        return resp.json()

    def list_tenants(self) -> List[Dict]:
        """List all tenants."""
        resp = self._request("GET", "/api/tenants")
        return resp.json()

    def get_tenant(self, tenant_id: str) -> Dict:
        """Get a tenant by ID or slug."""
        resp = self._request("GET", f"/api/tenants/{tenant_id}")
        return resp.json()

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete a tenant."""
        self._request("DELETE", f"/api/tenants/{tenant_id}")

    def get_tenant_client(self, api_key: str) -> BridgeClient:
        """
        Get a BridgeClient for a specific tenant.

        Args:
            api_key: API key of the tenant

        Returns:
            BridgeClient configured for that tenant
        """
        return BridgeClient(self.base_url, api_key)
