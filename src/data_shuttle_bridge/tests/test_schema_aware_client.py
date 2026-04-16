"""Tests for SchemAwareBridgeClient and SchemAwareBridgeAdminClient."""

import pytest
from unittest.mock import MagicMock, patch, call

import requests

from data_shuttle_bridge.sql.schema_aware_bridge_client import (
    SchemAwareBridgeClient,
    SchemAwareBridgeAdminClient,
)
from data_shuttle_bridge.sql.sync import ConflictPolicy


class TestSchemAwareBridgeAdminClient:
    def test_init(self):
        admin = SchemAwareBridgeAdminClient("http://localhost:5000/", "master_key")
        assert admin.server_url == "http://localhost:5000"
        assert admin.master_key == "master_key"

    def test_get_headers(self):
        admin = SchemAwareBridgeAdminClient("http://localhost:5000", "master_key")
        headers = admin._get_headers()
        assert headers["X-Tenant-Key"] == "master_key"
        assert headers["Content-Type"] == "application/json"

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_create_tenant(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "name": "Test"}
        mock_requests.post.return_value = mock_resp

        admin = SchemAwareBridgeAdminClient("http://localhost:5000", "key")
        result = admin.create_tenant("Test")
        assert result["name"] == "Test"
        mock_requests.post.assert_called_once()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_list_tenants(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1}, {"id": 2}]
        mock_requests.get.return_value = mock_resp

        admin = SchemAwareBridgeAdminClient("http://localhost:5000", "key")
        result = admin.list_tenants()
        assert len(result) == 2

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_get_tenant(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "slug": "test"}
        mock_requests.get.return_value = mock_resp

        admin = SchemAwareBridgeAdminClient("http://localhost:5000", "key")
        result = admin.get_tenant("test")
        assert result["slug"] == "test"

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_delete_tenant(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.delete.return_value = mock_resp

        admin = SchemAwareBridgeAdminClient("http://localhost:5000", "key")
        admin.delete_tenant("test")
        mock_requests.delete.assert_called_once()


class TestSchemAwareBridgeClientSecrets:
    """Test secrets management methods on SchemAwareBridgeClient without requiring full init."""

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_set_secret(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.post.return_value = mock_resp

        # Mock to avoid full init
        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        client.set_secret("key1", "val1")
        mock_requests.post.assert_called_once()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_get_secret(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"secret": "val1"}
        mock_requests.get.return_value = mock_resp

        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        result = client.get_secret("key1")
        assert result == "val1"

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_list_secrets(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"secrets": ["a", "b"]}
        mock_requests.get.return_value = mock_resp

        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        result = client.list_secrets()
        assert result == ["a", "b"]

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_delete_secret(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.delete.return_value = mock_resp

        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        client.delete_secret("key1")
        mock_requests.delete.assert_called_once()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_get_schema_info(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"versions": [{"version": 1}]}
        mock_requests.get.return_value = mock_resp

        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        result = client.get_schema_info()
        assert "versions" in result

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_check_for_drift(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "drift_detected": False,
            "new_version": None,
            "current_version": 1,
        }
        mock_requests.post.return_value = mock_resp

        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }

        result = client.check_for_drift()
        assert result["drift_detected"] is False


class TestSchemAwareBridgeClientInit:
    """Test full client initialization."""

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.build_schema")
    @patch(
        "data_shuttle_bridge.sql.schema_aware_bridge_client.attach_change_hooks_for_models"
    )
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SQLModel")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.create_engine")
    def test_init_defaults(
        self, mock_create_engine, mock_sqlmodel, mock_attach, mock_build
    ):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_build.return_value = {"table1": {}}

        models = [MagicMock()]
        client = SchemAwareBridgeClient(
            server_url="http://localhost:5000/",
            api_key="my_key",
            local_db_url="sqlite:///test.db",
            models=models,
        )

        assert client.server_url == "http://localhost:5000"
        assert client.api_key == "my_key"
        assert client.local_db_url == "sqlite:///test.db"
        assert client.models is models
        assert client.conflict_policy == ConflictPolicy.LWW
        mock_create_engine.assert_called_once_with("sqlite:///test.db")
        mock_sqlmodel.metadata.create_all.assert_called_once_with(mock_engine)
        mock_attach.assert_called_once_with(models)
        mock_build.assert_called_once_with(models)
        assert client.local_schema == {"table1": {}}
        assert client.remote_schema_metadata == {}
        assert client.SessionLocal is not None

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.build_schema")
    @patch(
        "data_shuttle_bridge.sql.schema_aware_bridge_client.attach_change_hooks_for_models"
    )
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SQLModel")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.create_engine")
    def test_init_custom_conflict_policy(
        self, mock_create_engine, mock_sqlmodel, mock_attach, mock_build
    ):
        mock_create_engine.return_value = MagicMock()
        mock_build.return_value = {}

        client = SchemAwareBridgeClient(
            server_url="http://example.com",
            api_key="key",
            local_db_url="sqlite:///test.db",
            models=[],
            conflict_policy=ConflictPolicy.VERSION,
        )

        assert client.conflict_policy == ConflictPolicy.VERSION


class TestSchemAwareBridgeClientGetHeaders:
    """Test _get_headers on the client (not admin)."""

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.build_schema")
    @patch(
        "data_shuttle_bridge.sql.schema_aware_bridge_client.attach_change_hooks_for_models"
    )
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SQLModel")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.create_engine")
    def test_get_headers(
        self, mock_create_engine, mock_sqlmodel, mock_attach, mock_build
    ):
        mock_create_engine.return_value = MagicMock()
        mock_build.return_value = {}

        client = SchemAwareBridgeClient(
            server_url="http://localhost:5000",
            api_key="my_api_key",
            local_db_url="sqlite:///test.db",
            models=[],
        )

        headers = client._get_headers()
        assert headers == {
            "X-API-Key": "my_api_key",
            "Content-Type": "application/json",
        }


class TestSchemAwareBridgeClientSync:
    """Test the sync method."""

    def _make_client(self):
        """Create a client instance bypassing __init__."""
        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.server_url = "http://localhost:5000"
        client.api_key = "test_key"
        client.local_schema = {"users": {}}
        client.conflict_policy = ConflictPolicy.LWW
        client.remote_schema_metadata = {}
        client._get_headers = lambda: {
            "X-API-Key": "test_key",
            "Content-Type": "application/json",
        }
        return client

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SyncEngine")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_sync_no_changes(self, mock_requests, mock_sync_engine_cls):
        client = self._make_client()
        mock_session = MagicMock()
        client.SessionLocal = MagicMock(return_value=mock_session)

        # Mock drift check
        drift_resp = MagicMock()
        drift_resp.json.return_value = {"drift_detected": False, "new_version": None}

        # Mock pull changes
        changes_resp = MagicMock()
        changes_resp.json.return_value = {"schema_metadata": {}, "changes": []}

        mock_requests.post.return_value = drift_resp
        mock_requests.get.return_value = changes_resp
        mock_requests.RequestException = requests.RequestException

        # Mock sync engine for local changes
        mock_engine = MagicMock()
        mock_engine.local_changes_since.return_value = []
        mock_sync_engine_cls.return_value = mock_engine

        result = client.sync()

        assert result["local_changes"] == 0
        assert result["remote_changes"] == 0
        assert result["schema_drift"] is False
        assert result["new_schema_version"] is None

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SyncEngine")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_sync_with_drift_and_changes(self, mock_requests, mock_sync_engine_cls):
        client = self._make_client()
        mock_session = MagicMock()
        client.SessionLocal = MagicMock(return_value=mock_session)

        # Mock drift check - drift detected
        drift_resp = MagicMock()
        drift_resp.json.return_value = {"drift_detected": True, "new_version": 2}

        # Mock pull changes - some remote changes
        remote_changes = [
            {
                "id": 1,
                "table": "users",
                "pk": 1,
                "op": "I",
                "version": 1,
                "data": {"name": "Alice"},
                "at": None,
            },
        ]
        changes_resp = MagicMock()
        changes_resp.json.return_value = {
            "schema_metadata": {"v": 2},
            "changes": remote_changes,
        }

        mock_requests.post.side_effect = [
            drift_resp,
            MagicMock(),
        ]  # drift check, then push
        mock_requests.get.return_value = changes_resp
        mock_requests.RequestException = requests.RequestException

        # Mock _apply_changes_locally
        client._apply_changes_locally = MagicMock()

        # Mock sync engine for local changes
        local_changes = [
            {
                "id": 10,
                "table": "users",
                "pk": 2,
                "op": "I",
                "version": 1,
                "data": {"name": "Bob"},
                "at": None,
            },
            {
                "id": 11,
                "table": "users",
                "pk": 3,
                "op": "I",
                "version": 1,
                "data": {"name": "Carol"},
                "at": None,
            },
        ]
        mock_engine = MagicMock()
        mock_engine.local_changes_since.return_value = local_changes
        mock_sync_engine_cls.return_value = mock_engine

        result = client.sync()

        assert result["schema_drift"] is True
        assert result["new_schema_version"] == 2
        assert result["remote_changes"] == 1
        assert result["local_changes"] == 2
        assert client.remote_schema_metadata == {"v": 2}
        client._apply_changes_locally.assert_called_once_with(remote_changes)

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_sync_drift_check_failure(self, mock_requests):
        client = self._make_client()

        mock_requests.post.side_effect = requests.RequestException("connection error")
        mock_requests.RequestException = requests.RequestException

        with pytest.raises(RuntimeError, match="Failed to check schema drift"):
            client.sync()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_sync_pull_failure(self, mock_requests):
        client = self._make_client()

        # Drift check succeeds
        drift_resp = MagicMock()
        drift_resp.json.return_value = {"drift_detected": False, "new_version": None}

        mock_requests.post.return_value = drift_resp
        mock_requests.get.side_effect = requests.RequestException("fetch failed")
        mock_requests.RequestException = requests.RequestException

        with pytest.raises(RuntimeError, match="Failed to fetch remote changes"):
            client.sync()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SyncEngine")
    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.requests")
    def test_sync_push_failure(self, mock_requests, mock_sync_engine_cls):
        client = self._make_client()
        mock_session = MagicMock()
        client.SessionLocal = MagicMock(return_value=mock_session)

        # Drift check succeeds
        drift_resp = MagicMock()
        drift_resp.json.return_value = {"drift_detected": False, "new_version": None}

        # Pull succeeds
        changes_resp = MagicMock()
        changes_resp.json.return_value = {"schema_metadata": {}, "changes": []}

        # First post is drift check (succeeds), second post is push (fails)
        mock_requests.post.side_effect = [
            drift_resp,
            requests.RequestException("push failed"),
        ]
        mock_requests.get.return_value = changes_resp
        mock_requests.RequestException = requests.RequestException

        # Local changes exist
        mock_engine = MagicMock()
        mock_engine.local_changes_since.return_value = [{"id": 1}]
        mock_sync_engine_cls.return_value = mock_engine

        with pytest.raises(RuntimeError, match="Failed to push local changes"):
            client.sync()


class TestSchemAwareBridgeClientApplyChanges:
    """Test _apply_changes_locally method."""

    def _make_client(self):
        client = SchemAwareBridgeClient.__new__(SchemAwareBridgeClient)
        client.local_schema = {"users": {}}
        client.conflict_policy = ConflictPolicy.LWW
        return client

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SyncEngine")
    def test_apply_changes_locally_success(self, mock_sync_engine_cls):
        client = self._make_client()
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        client.SessionLocal = MagicMock(return_value=mock_ctx)

        mock_engine = MagicMock()
        mock_sync_engine_cls.return_value = mock_engine

        changes = [{"id": 1, "table": "users", "pk": 1, "op": "I"}]
        client._apply_changes_locally(changes)

        mock_engine.apply_remote_changes.assert_called_once_with(changes)
        mock_session.commit.assert_called_once()

    @patch("data_shuttle_bridge.sql.schema_aware_bridge_client.SyncEngine")
    def test_apply_changes_locally_failure_rolls_back(self, mock_sync_engine_cls):
        client = self._make_client()
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        client.SessionLocal = MagicMock(return_value=mock_ctx)

        mock_engine = MagicMock()
        mock_engine.apply_remote_changes.side_effect = Exception("db error")
        mock_sync_engine_cls.return_value = mock_engine

        with pytest.raises(RuntimeError, match="Failed to apply remote changes"):
            client._apply_changes_locally([{"id": 1}])

        mock_session.rollback.assert_called_once()
