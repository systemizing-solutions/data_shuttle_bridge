"""
Schema-aware bridge client for multi-tenant sync with version tracking.

This client integrates with the schema-aware multi-tenant server and:
- Handles schema version compatibility
- Tracks local schema changes
- Manages consolidated view querying
- Provides bidirectional sync with version awareness
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional, Type

import requests
from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import attach_change_hooks_for_models
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session


class SchemAwareBridgeClient:
    """
    Client for syncing with schema-aware multi-tenant bridge server.

    Features:
    - Version-aware sync
    - Automatic schema drift detection
    - Consolidated view support
    - Conflict resolution
    """

    def __init__(
        self,
        server_url: str,
        api_key: str,
        local_db_url: str,
        models: List[Type],
        conflict_policy: ConflictPolicy = ConflictPolicy.LWW,
    ):
        """
        Initialize schema-aware bridge client.

        Args:
            server_url: Base URL of multi-tenant bridge server
            api_key: API key for authentication
            local_db_url: Local database URL (SQLite)
            models: List of SQLModel/SQLAlchemy models to sync
            conflict_policy: Conflict resolution strategy
        """
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.local_db_url = local_db_url
        self.models = models
        self.conflict_policy = conflict_policy

        # Initialize local database
        self.engine = create_engine(local_db_url)
        SQLModel.metadata.create_all(self.engine)

        # Build local schema
        attach_change_hooks_for_models(models)
        self.local_schema = build_schema(models)

        # Track remote schema metadata
        self.remote_schema_metadata: Dict[str, Any] = {}

        # Initialize session factory
        from sqlalchemy.orm import sessionmaker

        self.SessionLocal = sessionmaker(bind=self.engine)

    def _get_headers(self) -> Dict[str, str]:
        """Build HTTP headers with API key."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def get_schema_info(self) -> Dict[str, Any]:
        """Get schema version information from server."""
        resp = requests.get(
            f"{self.server_url}/api/schema/versions",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def check_for_drift(self) -> Dict[str, Any]:
        """
        Check if local schema has drifted from server.

        Returns:
            {
                'drift_detected': bool,
                'new_version': int or None,
                'current_version': int,
            }
        """
        resp = requests.post(
            f"{self.server_url}/api/schema/check-drift",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def sync(self) -> Dict[str, Any]:
        """
        Perform bidirectional sync:
        1. Check schema drift
        2. Pull remote changes
        3. Apply local changes

        Returns:
            {
                'local_changes': int,
                'remote_changes': int,
                'schema_drift': bool,
                'new_schema_version': int or None,
                'schema_metadata': dict,
            }
        """
        result = {
            "local_changes": 0,
            "remote_changes": 0,
            "schema_drift": False,
            "new_schema_version": None,
            "schema_metadata": {},
        }

        # Step 1: Check for schema drift
        try:
            drift_info = self.check_for_drift()
            result["schema_drift"] = drift_info.get("drift_detected", False)
            result["new_schema_version"] = drift_info.get("new_version")
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to check schema drift: {e}")

        # Step 2: Pull remote changes
        try:
            changes_resp = requests.get(
                f"{self.server_url}/api/sync/changes",
                headers=self._get_headers(),
                params={"limit": 10000},
            )
            changes_resp.raise_for_status()
            changes_data = changes_resp.json()

            self.remote_schema_metadata = changes_data.get("schema_metadata", {})
            remote_changes = changes_data.get("changes", [])
            result["remote_changes"] = len(remote_changes)

            # Apply remote changes to local database
            if remote_changes:
                self._apply_changes_locally(remote_changes)

        except requests.RequestException as e:
            raise RuntimeError(f"Failed to fetch remote changes: {e}")

        # Step 3: Push local changes
        try:
            with self.SessionLocal() as session:
                engine = SyncEngine(
                    session=session,
                    peer_id="local-client",
                    schema=self.local_schema,
                    policy=self.conflict_policy,
                    node_id="local",
                )

                local_changes = engine.local_changes_since(since_id=0, limit=10000)
                result["local_changes"] = len(local_changes)

                # Send local changes to server
                if local_changes:
                    resp = requests.post(
                        f"{self.server_url}/api/sync/apply",
                        headers=self._get_headers(),
                        json={"changes": local_changes},
                    )
                    resp.raise_for_status()

        except requests.RequestException as e:
            raise RuntimeError(f"Failed to push local changes: {e}")

        return result

    def _apply_changes_locally(self, changes: List[Dict[str, Any]]) -> None:
        """Apply remote changes to local database."""
        with self.SessionLocal() as session:
            try:
                engine = SyncEngine(
                    session=session,
                    peer_id="server",
                    schema=self.local_schema,
                    policy=self.conflict_policy,
                    node_id="server",
                )

                engine.apply_remote_changes(changes)
                session.commit()

            except Exception as e:
                session.rollback()
                raise RuntimeError(f"Failed to apply remote changes: {e}")

    # ===== Secrets Management (Optional) =====

    def set_secret(self, key: str, secret: str) -> None:
        """Store a secret on the server."""
        resp = requests.post(
            f"{self.server_url}/api/secrets",
            headers=self._get_headers(),
            json={"key": key, "secret": secret},
        )
        resp.raise_for_status()

    def get_secret(self, key: str) -> str:
        """Retrieve a secret from the server."""
        resp = requests.get(
            f"{self.server_url}/api/secrets/{key}",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()["secret"]

    def list_secrets(self) -> List[str]:
        """List all secret keys on the server."""
        resp = requests.get(
            f"{self.server_url}/api/secrets",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()["secrets"]

    def delete_secret(self, key: str) -> None:
        """Delete a secret from the server."""
        resp = requests.delete(
            f"{self.server_url}/api/secrets/{key}",
            headers=self._get_headers(),
        )
        resp.raise_for_status()


class SchemAwareBridgeAdminClient:
    """
    Admin client for managing tenants on the multi-tenant bridge server.

    Requires:
    - Master key (X-Tenant-Key header)
    """

    def __init__(self, server_url: str, master_key: str):
        """
        Initialize admin client.

        Args:
            server_url: Base URL of multi-tenant bridge server
            master_key: Master key for admin operations (X-Tenant-Key)
        """
        self.server_url = server_url.rstrip("/")
        self.master_key = master_key

    def _get_headers(self) -> Dict[str, str]:
        """Build HTTP headers with master key."""
        return {
            "X-Tenant-Key": self.master_key,
            "Content-Type": "application/json",
        }

    def create_tenant(
        self,
        name: str,
        slug: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new tenant."""
        resp = requests.post(
            f"{self.server_url}/api/tenants",
            headers=self._get_headers(),
            json={
                "name": name,
                "slug": slug,
                "metadata": metadata or {},
            },
        )
        resp.raise_for_status()
        return resp.json()

    def list_tenants(self) -> List[Dict[str, Any]]:
        """List all tenants."""
        resp = requests.get(
            f"{self.server_url}/api/tenants",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_tenant(self, tenant_id: str) -> Dict[str, Any]:
        """Get tenant by ID or slug."""
        resp = requests.get(
            f"{self.server_url}/api/tenants/{tenant_id}",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete a tenant."""
        resp = requests.delete(
            f"{self.server_url}/api/tenants/{tenant_id}",
            headers=self._get_headers(),
        )
        resp.raise_for_status()
