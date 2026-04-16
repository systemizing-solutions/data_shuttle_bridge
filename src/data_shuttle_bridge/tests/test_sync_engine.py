"""Tests for the SyncEngine and wiring module."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlmodel import SQLModel, Session as SMSession, select

from data_shuttle_bridge.sql.mixins import SyncRowSAMixin
from data_shuttle_bridge.sql.ids import set_id_generator
from data_shuttle_bridge.sql.changelog import ChangeLog, SyncState
from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import (
    attach_change_hooks_for_models,
    set_current_node_id,
    get_current_node_id,
    _summary,
)
from data_shuttle_bridge.sql.transport import InMemoryPeerTransport
from data_shuttle_bridge.sql.sync_config import (
    SyncConfig,
    SyncScope,
    TableSyncRule,
    FilterExpression,
    FilterCondition,
    FilterOperator,
)


# ============================================================================
# Test Models
# ============================================================================

SABase = declarative_base()


class SyncUser(SABase, SyncRowSAMixin):
    __tablename__ = "sync_test_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


class SyncOrder(SABase, SyncRowSAMixin):
    __tablename__ = "sync_test_orders"
    product = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def setup_node():
    set_id_generator("sync_test_node")
    yield
    set_current_node_id(None)


@pytest.fixture
def session_and_schema():
    engine = create_engine("sqlite:///:memory:")
    SABase.metadata.create_all(engine)
    ChangeLog.metadata.create_all(engine)
    SyncState.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, class_=SMSession)
    session = SessionLocal()

    models = [SyncUser, SyncOrder]
    attach_change_hooks_for_models(models)
    schema = build_schema(models)

    yield session, schema

    session.close()
    engine.dispose()


# ============================================================================
# Wiring Tests
# ============================================================================


class TestWiring:
    def test_set_get_node_id(self):
        set_current_node_id("node_abc")
        assert get_current_node_id() == "node_abc"
        set_current_node_id(None)
        assert get_current_node_id() is None

    def test_summary_basic(self):
        class Obj:
            updated_at = datetime(2024, 1, 1)
            deleted_at = None
            version = 3

        s = _summary(Obj())
        assert s["version"] == 3
        assert s["deleted_at"] is None
        assert "2024" in s["updated_at"]

    def test_summary_missing_attrs(self):
        class Obj:
            version = 1

        s = _summary(Obj())
        assert s["version"] == 1
        assert "updated_at" not in s

    def test_change_hooks_insert(self, session_and_schema):
        session, schema = session_and_schema
        user = SyncUser(name="Alice", email="alice@test.com")
        session.add(user)
        session.commit()

        logs = session.exec(select(ChangeLog)).all()
        assert len(logs) == 1
        assert logs[0].op == "I"
        assert logs[0].table == "sync_test_users"

    def test_change_hooks_update(self, session_and_schema):
        session, schema = session_and_schema
        user = SyncUser(name="Bob", email="bob@test.com")
        session.add(user)
        session.commit()

        user.name = "Bobby"
        session.commit()

        logs = session.exec(select(ChangeLog).where(ChangeLog.op == "U")).all()
        assert len(logs) == 1
        assert logs[0].version == 2

    def test_change_hooks_delete(self, session_and_schema):
        session, schema = session_and_schema
        user = SyncUser(name="Charlie", email="charlie@test.com")
        session.add(user)
        session.commit()

        session.delete(user)
        session.commit()

        logs = session.exec(select(ChangeLog).where(ChangeLog.op == "D")).all()
        assert len(logs) == 1

    def test_no_table_raises(self):
        class NoTable:
            pass

        with pytest.raises(ValueError, match="no table"):
            attach_change_hooks_for_models([NoTable])


# ============================================================================
# SyncEngine Tests
# ============================================================================


class TestSyncEngine:
    def test_local_changes_since(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        user = SyncUser(name="Alice", email="alice@test.com")
        session.add(user)
        session.commit()

        changes = eng.local_changes_since(0)
        assert len(changes) >= 1
        assert changes[0]["table"] == "sync_test_users"
        assert changes[0]["op"] == "I"
        assert changes[0]["data"]["name"] == "Alice"

    def test_remote_changes_since(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        user = SyncUser(name="Bob", email="bob@test.com")
        session.add(user)
        session.commit()

        changes = eng.remote_changes_since(0)
        assert len(changes) >= 1

    def test_remote_changes_exclude_node(self, session_and_schema):
        session, schema = session_and_schema
        set_current_node_id("node_A")
        eng = SyncEngine(
            session=session, peer_id="peer1", schema=schema, node_id="node_A"
        )

        user = SyncUser(name="Eve", email="eve@test.com")
        session.add(user)
        session.commit()

        # Changes from node_A should be excluded when exclude_node_id="node_A"
        changes = eng.remote_changes_since(0, exclude_node_id="node_A")
        # The change was logged with node_id=node_A, so it should be excluded
        node_a_changes = [c for c in changes if c.get("node_id") == "node_A"]
        assert len(node_a_changes) == 0

    def test_apply_remote_insert(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        changes = [
            {
                "id": 1,
                "table": "sync_test_users",
                "pk": 999999,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 999999,
                    "name": "Remote",
                    "email": "remote@test.com",
                    "version": 1,
                },
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        user = session.exec(select(SyncUser).where(SyncUser.id == 999999)).first()
        assert user is not None
        assert user.name == "Remote"

    def test_apply_remote_update(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        user = SyncUser(name="Original", email="orig@test.com")
        session.add(user)
        session.commit()
        user_id = user.id

        changes = [
            {
                "id": 2,
                "table": "sync_test_users",
                "pk": user_id,
                "op": "U",
                "version": 5,
                "data": {"name": "Updated", "email": "updated@test.com"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(SyncUser).where(SyncUser.id == user_id)).first()
        assert updated.name == "Updated"

    def test_apply_remote_delete(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        user = SyncUser(name="ToDelete", email="del@test.com")
        session.add(user)
        session.commit()
        user_id = user.id

        changes = [
            {
                "id": 3,
                "table": "sync_test_users",
                "pk": user_id,
                "op": "D",
                "version": 2,
                "data": None,
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        assert (
            session.exec(select(SyncUser).where(SyncUser.id == user_id)).first() is None
        )

    def test_apply_remote_delete_nonexistent(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        changes = [
            {
                "id": 4,
                "table": "sync_test_users",
                "pk": 999998,
                "op": "D",
                "version": 1,
                "data": None,
                "at": None,
            }
        ]
        # Should not raise
        eng.apply_remote_changes(changes)

    def test_conflict_lww(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(
            session=session, peer_id="peer1", schema=schema, policy=ConflictPolicy.LWW
        )

        user = SyncUser(name="Original", email="orig@test.com")
        session.add(user)
        session.commit()
        user_id = user.id

        changes = [
            {
                "id": 5,
                "table": "sync_test_users",
                "pk": user_id,
                "op": "U",
                "version": 10,
                "data": {"name": "HighVersion"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(SyncUser).where(SyncUser.id == user_id)).first()
        assert updated.name == "HighVersion"
        assert updated.version == 10

    def test_conflict_version_strict_rejects_lower(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(
            session=session,
            peer_id="peer1",
            schema=schema,
            policy=ConflictPolicy.VERSION,
        )

        user = SyncUser(name="Original", email="orig@test.com")
        session.add(user)
        session.commit()
        user_id = user.id
        current_version = user.version

        # Try to apply change with lower version
        changes = [
            {
                "id": 6,
                "table": "sync_test_users",
                "pk": user_id,
                "op": "U",
                "version": 0,
                "data": {"name": "ShouldNotApply"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(SyncUser).where(SyncUser.id == user_id)).first()
        assert updated.name == "Original"

    def test_compute_order(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)
        assert isinstance(eng.order, list)
        assert "sync_test_users" in eng.order
        assert "sync_test_orders" in eng.order

    def test_pull_then_push(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="peer1", schema=schema)

        # Create a local change
        user = SyncUser(name="Local", email="local@test.com")
        session.add(user)
        session.commit()

        transport = InMemoryPeerTransport()
        pulled, pushed = eng.pull_then_push(transport)
        assert pulled == 0
        assert pushed >= 1

    def test_pull_then_push_with_remote_changes(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(
            session=session, peer_id="peer1", schema=schema, node_id="local"
        )

        remote_changes = [
            {
                "id": 100,
                "table": "sync_test_users",
                "pk": 888888,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 888888,
                    "name": "FromRemote",
                    "email": "remote@test.com",
                    "version": 1,
                },
                "at": None,
            }
        ]
        transport = InMemoryPeerTransport(remote_changes)
        pulled, pushed = eng.pull_then_push(transport)
        assert pulled == 1

    def test_ensure_state_creates_new(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="new_peer", schema=schema)
        st = eng._ensure_state()
        assert st.peer_id == "new_peer"
        assert st.last_pulled_change_id == 0
        assert st.last_pushed_change_id == 0


# ============================================================================
# SyncEngine with SyncConfig Tests
# ============================================================================


class TestSyncEngineWithConfig:
    def test_should_sync_table_database_scope(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(scope=SyncScope.DATABASE)
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        assert eng._should_sync_table("sync_test_users") is True

    def test_should_sync_table_disabled(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(scope=SyncScope.DATABASE, enabled=False)
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        assert eng._should_sync_table("sync_test_users") is False

    def test_should_sync_table_tables_scope(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={"sync_test_users": TableSyncRule(enabled=True)},
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        assert eng._should_sync_table("sync_test_users") is True
        assert eng._should_sync_table("sync_test_orders") is False

    def test_get_fields_to_sync_default(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="p", schema=schema)
        fields = eng._get_fields_to_sync("sync_test_users")
        assert "name" in fields
        assert "email" in fields

    def test_get_fields_exclude(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "sync_test_users": TableSyncRule(
                    enabled=True, exclude_columns=["email"]
                )
            },
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        fields = eng._get_fields_to_sync("sync_test_users")
        assert "name" in fields
        assert "email" not in fields

    def test_get_fields_include_only(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={
                "sync_test_users": TableSyncRule(
                    enabled=True, include_only_columns=["name"]
                )
            },
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        fields = eng._get_fields_to_sync("sync_test_users")
        assert fields == ["name"]

    def test_get_fields_unknown_table(self, session_and_schema):
        session, schema = session_and_schema
        eng = SyncEngine(session=session, peer_id="p", schema=schema)
        assert eng._get_fields_to_sync("nonexistent") == []

    def test_should_sync_row_with_filter(self, session_and_schema):
        session, schema = session_and_schema
        filter_expr = FilterExpression(
            conditions=[
                FilterCondition(field="name", operator=FilterOperator.EQ, value="Alice")
            ],
        )
        config = SyncConfig(
            scope=SyncScope.FILTERED,
            tables={"sync_test_users": TableSyncRule(enabled=True, filter=filter_expr)},
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        assert eng._should_sync_row("sync_test_users", {"name": "Alice"}) is True
        assert eng._should_sync_row("sync_test_users", {"name": "Bob"}) is False

    def test_should_sync_row_no_filter(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={"sync_test_users": TableSyncRule(enabled=True)},
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )
        assert eng._should_sync_row("sync_test_users", {"name": "anyone"}) is True

    def test_apply_ignores_non_synced_table(self, session_and_schema):
        session, schema = session_and_schema
        config = SyncConfig(
            scope=SyncScope.TABLES,
            tables={"sync_test_users": TableSyncRule(enabled=True)},
        )
        eng = SyncEngine(
            session=session, peer_id="p", schema=schema, sync_config=config
        )

        changes = [
            {
                "id": 10,
                "table": "sync_test_orders",
                "pk": 777,
                "op": "I",
                "version": 1,
                "data": {"id": 777, "product": "Widget", "amount": 10, "version": 1},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        # Order should NOT have been created since table is not in sync config
        order = session.exec(select(SyncOrder).where(SyncOrder.id == 777)).first()
        assert order is None
