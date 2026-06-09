"""Tests for transport, payloads, schema, wiring, registry, and nodeid modules."""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlmodel import SQLModel, Session, Field, select

from data_shuttle_bridge.sql.transport import (
    PeerTransport,
    InMemoryPeerTransport,
    HttpPeerTransport,
)
from data_shuttle_bridge.sql.payloads import (
    serialize_row,
    apply_row,
    TableSchema,
)
from data_shuttle_bridge.sql.nodeid import (
    ClientNodeConfig,
    ClientNodeManager,
)
from data_shuttle_bridge.sql.registry import (
    NodeRegistry,
    allocate_node_id,
    node_registry_blueprint,
    MAX_NODE,
)


# ============================================================================
# Transport Tests
# ============================================================================


class TestPeerTransport:
    def test_base_raises(self):
        t = PeerTransport()
        with pytest.raises(NotImplementedError):
            t.get_changes_since(0)
        with pytest.raises(NotImplementedError):
            t.apply_changes([])
        # ack is a no-op by default
        t.ack(0)


class TestInMemoryPeerTransport:
    def test_empty(self):
        t = InMemoryPeerTransport()
        assert t.get_changes_since(0) == []

    def test_get_changes_since(self):
        changes = [
            {"id": 1, "data": "a"},
            {"id": 2, "data": "b"},
            {"id": 3, "data": "c"},
        ]
        t = InMemoryPeerTransport(changes)
        assert len(t.get_changes_since(0)) == 3
        assert len(t.get_changes_since(1)) == 2
        assert len(t.get_changes_since(3)) == 0

    def test_get_changes_with_limit(self):
        changes = [{"id": i} for i in range(1, 11)]
        t = InMemoryPeerTransport(changes)
        result = t.get_changes_since(0, limit=3)
        assert len(result) == 3

    def test_apply_changes(self):
        t = InMemoryPeerTransport()
        t.apply_changes([{"id": 1}, {"id": 2}])
        assert len(t._changes) == 2
        t.apply_changes([{"id": 3}])
        assert len(t._changes) == 3


class TestHttpPeerTransport:
    def test_get_changes_since(self):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"changes": [{"id": 1}]}
        mock_session.get.return_value = mock_response

        t = HttpPeerTransport("http://localhost:5000", session=mock_session)
        result = t.get_changes_since(0)
        assert result == [{"id": 1}]
        mock_session.get.assert_called_once()

    def test_get_changes_with_exclude_node_id(self):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"changes": []}
        mock_session.get.return_value = mock_response

        t = HttpPeerTransport("http://localhost:5000/", session=mock_session)
        t.get_changes_since(0, exclude_node_id="node1")
        call_args = mock_session.get.call_args
        assert "exclude_node_id" in call_args.kwargs["params"]

    def test_apply_changes(self):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_session.post.return_value = mock_response

        t = HttpPeerTransport("http://localhost:5000", session=mock_session)
        t.apply_changes([{"id": 1}])
        mock_session.post.assert_called_once()

    def test_ack(self):
        mock_session = MagicMock()
        t = HttpPeerTransport("http://localhost:5000", session=mock_session)
        t.ack(42)
        mock_session.post.assert_called_once()

    def test_strips_trailing_slash(self):
        mock_session = MagicMock()
        t = HttpPeerTransport("http://localhost:5000/", session=mock_session)
        assert t.base_url == "http://localhost:5000"


# ============================================================================
# Payloads Tests
# ============================================================================


class TestSerializeRow:
    def test_basic(self):
        class FakeObj:
            name = "Alice"
            age = 30
            email = "alice@example.com"

        result = serialize_row(FakeObj(), ["name", "age"])
        assert result == {"name": "Alice", "age": 30}

    def test_datetime_serialization(self):
        class FakeObj:
            created_at = datetime(2024, 1, 15, 10, 30, 0)

        result = serialize_row(FakeObj(), ["created_at"])
        assert isinstance(result["created_at"], str)
        assert "2024-01-15" in result["created_at"]

    def test_empty_fields(self):
        class FakeObj:
            pass

        result = serialize_row(FakeObj(), [])
        assert result == {}


class TestApplyRow:
    def test_basic(self):
        class FakeObj:
            name = None
            age = None

        obj = FakeObj()
        apply_row(obj, {"name": "Bob", "age": 25})
        assert obj.name == "Bob"
        assert obj.age == 25

    def test_exclude(self):
        class FakeObj:
            name = None
            id = None

        obj = FakeObj()
        apply_row(obj, {"name": "Charlie", "id": 999}, exclude=["id"])
        assert obj.name == "Charlie"
        assert obj.id is None

    def test_datetime_deserialization(self):
        class FakeObj:
            created_at = None

        obj = FakeObj()
        apply_row(obj, {"created_at": "2024-01-15T10:30:00"})
        assert isinstance(obj.created_at, datetime)

    def test_non_datetime_string(self):
        class FakeObj:
            name = None

        obj = FakeObj()
        apply_row(obj, {"name": "not-a-date"})
        assert obj.name == "not-a-date"


class TestTableSchema:
    def test_basic(self):
        ts = TableSchema(model=str, fields=["a", "b", "c"])
        assert ts.fields == ["a", "b", "c"]
        assert ts.parents == set()

    def test_with_parents(self):
        ts = TableSchema(model=str, fields=["a"], parents=["parent1", "parent2"])
        assert ts.parents == {"parent1", "parent2"}


# ============================================================================
# NodeID Tests
# ============================================================================


class TestClientNodeConfig:
    def test_basic(self):
        cfg = ClientNodeConfig(device_key="abc", node_id=42)
        assert cfg.device_key == "abc"
        assert cfg.node_id == 42

    def test_defaults(self):
        cfg = ClientNodeConfig(device_key="abc")
        assert cfg.node_id is None


class TestClientNodeManager:
    def test_creates_new_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            mgr = ClientNodeManager(config_path=path)
            assert mgr.device_key is not None
            assert len(mgr.device_key) > 0
            assert mgr.node_id is None
            assert os.path.exists(path)

    def test_loads_existing_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            with open(path, "w") as f:
                json.dump({"device_key": "my_key", "node_id": 5}, f)
            mgr = ClientNodeManager(config_path=path)
            assert mgr.device_key == "my_key"
            assert mgr.node_id == 5

    def test_generates_device_key_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            with open(path, "w") as f:
                json.dump({"device_key": None, "node_id": None}, f)
            mgr = ClientNodeManager(config_path=path)
            # Should have generated a new device_key
            assert mgr.device_key is not None
            assert len(mgr.device_key) > 0

    def test_ensure_node_id_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            with open(path, "w") as f:
                json.dump({"device_key": "key", "node_id": 42}, f)
            mgr = ClientNodeManager(config_path=path)
            assert mgr.ensure_node_id("http://localhost:5000") == 42

    def test_ensure_node_id_requests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            mgr = ClientNodeManager(config_path=path)

            mock_session = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"node_id": 7}
            mock_session.post.return_value = mock_resp

            node_id = mgr.ensure_node_id("http://localhost:5000", session=mock_session)
            assert node_id == 7
            # Should be saved
            with open(path) as f:
                data = json.load(f)
            assert data["node_id"] == 7

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "config.json")
            mgr = ClientNodeManager(config_path=path)
            assert os.path.exists(path)


# ============================================================================
# Registry Tests
# ============================================================================


class TestNodeRegistry:
    @pytest.fixture
    def db_session(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            yield session

    def test_allocate_new_node_id(self, db_session):
        node_id = allocate_node_id(db_session, "device_abc")
        assert node_id == 1

    def test_allocate_existing_device(self, db_session):
        id1 = allocate_node_id(db_session, "device_xyz")
        id2 = allocate_node_id(db_session, "device_xyz")
        assert id1 == id2

    def test_allocate_multiple_devices(self, db_session):
        id1 = allocate_node_id(db_session, "dev1")
        id2 = allocate_node_id(db_session, "dev2")
        id3 = allocate_node_id(db_session, "dev3")
        assert id1 != id2 != id3
        assert id1 == 1
        assert id2 == 2
        assert id3 == 3

    def test_allocate_exhaustion(self, db_session):
        for i in range(1, MAX_NODE + 1):
            entry = NodeRegistry(device_key=f"dev_{i}", node_id=i)
            db_session.add(entry)
        db_session.commit()
        with pytest.raises(RuntimeError, match="No available node_id slots"):
            allocate_node_id(db_session, "one_more")


class TestNodeRegistryBlueprint:
    @pytest.fixture
    def app(self):
        from flask import Flask

        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)

        def session_factory():
            return Session(engine)

        app = Flask(__name__)
        app.register_blueprint(node_registry_blueprint(session_factory))
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_register_success(self, client):
        resp = client.post("/node/register", json={"device_key": "mydevice"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "node_id" in data
        assert data["node_id"] == 1

    def test_register_idempotent(self, client):
        resp1 = client.post("/node/register", json={"device_key": "dev_a"})
        resp2 = client.post("/node/register", json={"device_key": "dev_a"})
        assert resp1.get_json()["node_id"] == resp2.get_json()["node_id"]

    def test_register_missing_device_key(self, client):
        resp = client.post("/node/register", json={})
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_register_empty_device_key(self, client):
        resp = client.post("/node/register", json={"device_key": ""})
        assert resp.status_code == 400

    def test_register_non_string_device_key(self, client):
        resp = client.post("/node/register", json={"device_key": 12345})
        assert resp.status_code == 400

    def test_register_device_key_too_long(self, client):
        resp = client.post("/node/register", json={"device_key": "x" * 65})
        assert resp.status_code == 400

    def test_register_device_key_max_length(self, client):
        resp = client.post("/node/register", json={"device_key": "x" * 64})
        assert resp.status_code == 200
        assert resp.get_json()["node_id"] == 1


# ============================================================================
# Schema (build_schema) Tests
# ============================================================================


class TestBuildSchema:
    def test_basic(self):
        from data_shuttle_bridge.sql.schema import build_schema
        from data_shuttle_bridge.sql.mixins import SyncRowSAMixin

        Base = declarative_base()

        class TestModel(Base, SyncRowSAMixin):
            __tablename__ = "test_build_schema_table"
            name = Column(String)

        result = build_schema([TestModel])
        assert "test_build_schema_table" in result
        ts = result["test_build_schema_table"]
        assert "name" in ts.fields
        assert ts.model is TestModel

    def test_no_table_raises(self):
        from data_shuttle_bridge.sql.schema import build_schema

        class BadModel:
            pass

        with pytest.raises(ValueError, match="not mapped"):
            build_schema([BadModel])
