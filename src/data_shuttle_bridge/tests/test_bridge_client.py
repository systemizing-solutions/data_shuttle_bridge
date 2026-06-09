"""Tests for BridgeClient, BridgeAdminClient, and _BridgeHttpPeerTransport."""

import pytest
from unittest.mock import MagicMock, patch

from data_shuttle_bridge.sql.bridge_client import (
    BridgeClient,
    BridgeAdminClient,
    _BridgeHttpPeerTransport,
)


class TestBridgeClient:
    @pytest.fixture
    def client(self):
        with patch(
            "data_shuttle_bridge.sql.bridge_client.requests.Session"
        ) as MockSess:
            mock_session = MagicMock()
            MockSess.return_value = mock_session
            c = BridgeClient("http://localhost:5000/", "test_api_key")
            yield c, mock_session

    def test_init(self, client):
        c, _ = client
        assert c.base_url == "http://localhost:5000"
        assert c.api_key == "test_api_key"
        assert c.timeout == 30

    def test_get_tenant_info(self, client):
        c, sess = client
        resp = MagicMock()
        resp.json.return_value = {"name": "Test", "slug": "test"}
        sess.request.return_value = resp
        result = c.get_tenant_info()
        assert result["name"] == "Test"
        sess.request.assert_called_once()

    def test_set_secret(self, client):
        c, sess = client
        resp = MagicMock()
        sess.request.return_value = resp
        c.set_secret("db_pass", "s3cret")
        sess.request.assert_called_once()
        call_args = sess.request.call_args
        assert call_args.kwargs["json"]["key"] == "db_pass"

    def test_get_secret(self, client):
        c, sess = client
        resp = MagicMock()
        resp.json.return_value = {"secret": "my_secret"}
        sess.request.return_value = resp
        result = c.get_secret("db_pass")
        assert result == "my_secret"

    def test_delete_secret(self, client):
        c, sess = client
        resp = MagicMock()
        sess.request.return_value = resp
        c.delete_secret("old_key")
        sess.request.assert_called_once()

    def test_list_secrets(self, client):
        c, sess = client
        resp = MagicMock()
        resp.json.return_value = {"secrets": ["key1", "key2"]}
        sess.request.return_value = resp
        result = c.list_secrets()
        assert result == ["key1", "key2"]

    def test_get_changes(self, client):
        c, sess = client
        resp = MagicMock()
        resp.json.return_value = {"changes": [{"id": 1}, {"id": 2}]}
        sess.request.return_value = resp
        result = c.get_changes(since_id=0, limit=100)
        assert len(result) == 2

    def test_apply_changes(self, client):
        c, sess = client
        resp = MagicMock()
        sess.request.return_value = resp
        c.apply_changes([{"id": 1, "op": "I"}])
        call_args = sess.request.call_args
        assert call_args.kwargs["json"]["changes"] == [{"id": 1, "op": "I"}]

    def test_ack_changes(self, client):
        c, sess = client
        resp = MagicMock()
        sess.request.return_value = resp
        c.ack_changes(42)
        sess.request.assert_called_once()

    def test_ack_changes_ignores_http_error(self, client):
        import requests

        c, sess = client
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError()
        sess.request.return_value = resp
        # Should not raise
        c.ack_changes(42)

    def test_as_peer_transport(self, client):
        c, _ = client
        transport = c.as_peer_transport()
        assert isinstance(transport, _BridgeHttpPeerTransport)


class TestBridgeHttpPeerTransport:
    def test_get_changes_since(self):
        mock_client = MagicMock()
        mock_client.get_changes.return_value = [{"id": 1}]
        t = _BridgeHttpPeerTransport(mock_client)
        result = t.get_changes_since(0)
        assert result == [{"id": 1}]
        mock_client.get_changes.assert_called_once_with(0, 1000)

    def test_apply_changes(self):
        mock_client = MagicMock()
        t = _BridgeHttpPeerTransport(mock_client)
        t.apply_changes([{"id": 1}])
        mock_client.apply_changes.assert_called_once_with([{"id": 1}])

    def test_ack(self):
        mock_client = MagicMock()
        t = _BridgeHttpPeerTransport(mock_client)
        t.ack(42)
        mock_client.ack_changes.assert_called_once_with(42)


class TestBridgeAdminClient:
    @pytest.fixture
    def admin(self):
        with patch(
            "data_shuttle_bridge.sql.bridge_client.requests.Session"
        ) as MockSess:
            mock_session = MagicMock()
            MockSess.return_value = mock_session
            a = BridgeAdminClient("http://localhost:5000/", admin_key="admin123")
            yield a, mock_session

    def test_init(self, admin):
        a, _ = admin
        assert a.base_url == "http://localhost:5000"
        assert a.admin_key == "admin123"

    def test_create_tenant(self, admin):
        a, sess = admin
        resp = MagicMock()
        resp.json.return_value = {"id": 1, "name": "New Tenant"}
        sess.request.return_value = resp
        result = a.create_tenant("New Tenant", slug="new-tenant")
        assert result["name"] == "New Tenant"

    def test_list_tenants(self, admin):
        a, sess = admin
        resp = MagicMock()
        resp.json.return_value = [{"id": 1}, {"id": 2}]
        sess.request.return_value = resp
        result = a.list_tenants()
        assert len(result) == 2

    def test_get_tenant(self, admin):
        a, sess = admin
        resp = MagicMock()
        resp.json.return_value = {"id": 1, "name": "Test"}
        sess.request.return_value = resp
        result = a.get_tenant("test")
        assert result["name"] == "Test"

    def test_delete_tenant(self, admin):
        a, sess = admin
        resp = MagicMock()
        sess.request.return_value = resp
        a.delete_tenant("test-tenant")
        sess.request.assert_called_once()

    def test_get_tenant_client(self, admin):
        a, _ = admin
        with patch("data_shuttle_bridge.sql.bridge_client.requests.Session"):
            tc = a.get_tenant_client("tenant_api_key")
            assert isinstance(tc, BridgeClient)
            assert tc.api_key == "tenant_api_key"

    def test_no_admin_key(self):
        with patch("data_shuttle_bridge.sql.bridge_client.requests.Session"):
            a = BridgeAdminClient("http://localhost:5000")
            assert a.admin_key is None
