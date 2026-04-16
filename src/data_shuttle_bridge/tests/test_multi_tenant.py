"""Tests for multi-tenant service, tenancy, and blueprints."""

import os
import json
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, Column, Integer, String, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlmodel import SQLModel, Session as SMSession, Field, select

from data_shuttle_bridge.sql.multi_tenant_service import (
    Tenant,
    TenantSecret,
    SecretManager,
    TenantManager,
)
from data_shuttle_bridge.sql.tenancy import (
    ChangeLogMT,
    SyncStateMT,
    SyncEngineMT,
    attach_change_hooks_mt_for_models,
)
from data_shuttle_bridge.sql.mixins import SyncRowSAMixin
from data_shuttle_bridge.sql.ids import set_id_generator
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.sync import ConflictPolicy


# ============================================================================
# Test Models
# ============================================================================

SABase = declarative_base()


class MTUser(SABase, SyncRowSAMixin):
    __tablename__ = "mt_test_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


# ============================================================================
# SecretManager Tests
# ============================================================================


class TestSecretManager:
    def test_encrypt_decrypt(self):
        sm = SecretManager()
        plaintext = "super_secret_value"
        encrypted = sm.encrypt(plaintext)
        assert encrypted != plaintext
        decrypted = sm.decrypt(encrypted)
        assert decrypted == plaintext

    def test_different_encryptions(self):
        sm = SecretManager()
        e1 = sm.encrypt("same_value")
        e2 = sm.encrypt("same_value")
        # Fernet produces different ciphertexts for same plaintext
        assert e1 != e2

    def test_with_explicit_key(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        sm = SecretManager(fernet_key=key)
        encrypted = sm.encrypt("test")
        assert sm.decrypt(encrypted) == "test"

    def test_with_env_key(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"FERNET_KEY": key}):
            sm = SecretManager()
            encrypted = sm.encrypt("env_test")
            assert sm.decrypt(encrypted) == "env_test"


# ============================================================================
# TenantManager Tests
# ============================================================================


class TestTenantManager:
    @pytest.fixture
    def tenant_setup(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)

        sm = SecretManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = TenantManager(
                master_session_factory=SessionFactory,
                secret_manager=sm,
                tenant_base_path=tmpdir,
            )
            yield mgr, SessionFactory, tmpdir

    def test_create_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Test Corp")
        assert tenant.name == "Test Corp"
        assert tenant.slug == "test-corp"
        assert tenant.api_key is not None
        assert tenant.is_active is True

    def test_create_tenant_custom_slug(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("My Company", slug="my-co")
        assert tenant.slug == "my-co"

    def test_create_tenant_duplicate_name_raises(self, tenant_setup):
        mgr, _, _ = tenant_setup
        mgr.create_tenant("Unique Corp")
        with pytest.raises(ValueError, match="already exists"):
            mgr.create_tenant("Unique Corp")

    def test_create_tenant_duplicate_slug_raises(self, tenant_setup):
        mgr, _, _ = tenant_setup
        mgr.create_tenant("Company A", slug="same-slug")
        with pytest.raises(ValueError, match="already in use"):
            mgr.create_tenant("Company B", slug="same-slug")

    def test_get_tenant_by_api_key(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("API Test")
        found = mgr.get_tenant_by_api_key(tenant.api_key)
        assert found is not None
        assert found.name == "API Test"

    def test_get_tenant_by_api_key_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr.get_tenant_by_api_key("nonexistent_key") is None

    def test_get_tenant_by_id(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("ID Test")
        found = mgr.get_tenant(tenant.id)
        assert found is not None

    def test_get_tenant_by_slug(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Slug Test")
        found = mgr.get_tenant(tenant.slug)
        assert found is not None

    def test_list_tenants(self, tenant_setup):
        mgr, _, _ = tenant_setup
        mgr.create_tenant("Tenant 1")
        mgr.create_tenant("Tenant 2")
        tenants = mgr.list_tenants()
        assert len(tenants) >= 2

    def test_delete_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("To Delete")
        assert mgr.delete_tenant(tenant.slug) is True
        assert mgr.get_tenant(tenant.slug) is None

    def test_delete_nonexistent_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr.delete_tenant("nonexistent") is False

    def test_set_and_get_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Secret Test")
        mgr.set_secret(tenant, "db_password", "s3cret!")
        decrypted = mgr.get_secret(tenant, "db_password")
        assert decrypted == "s3cret!"

    def test_update_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Update Secret")
        mgr.set_secret(tenant, "key1", "value1")
        mgr.set_secret(tenant, "key1", "value2")
        assert mgr.get_secret(tenant, "key1") == "value2"

    def test_get_secret_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("No Secret")
        assert mgr.get_secret(tenant, "nope") is None

    def test_delete_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Del Secret")
        mgr.set_secret(tenant, "temp", "val")
        assert mgr.delete_secret(tenant, "temp") is True
        assert mgr.get_secret(tenant, "temp") is None

    def test_delete_secret_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("No Del")
        assert mgr.delete_secret(tenant, "nope") is False

    def test_list_secrets(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("List Secrets")
        mgr.set_secret(tenant, "key_a", "val_a")
        mgr.set_secret(tenant, "key_b", "val_b")
        keys = mgr.list_secrets(tenant)
        assert "key_a" in keys
        assert "key_b" in keys

    def test_get_session_factory(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Session Test")
        factory = mgr.get_session_factory_for_tenant(tenant)
        assert callable(factory)
        # Calling again returns cached
        factory2 = mgr.get_session_factory_for_tenant(tenant)
        assert factory is factory2


# ============================================================================
# ChangeLogMT / SyncStateMT Tests
# ============================================================================


class TestChangeLogMT:
    def test_create(self):
        log = ChangeLogMT(tenant="acme", table="users", pk=1, op="I", version=1)
        assert log.tenant == "acme"
        assert log.op == "I"

    def test_all_fields(self):
        log = ChangeLogMT(
            tenant="acme",
            table="users",
            pk=42,
            op="U",
            version=3,
            summary={"version": 3},
        )
        assert log.table == "users"
        assert log.pk == 42
        assert log.version == 3
        assert log.summary == {"version": 3}

    def test_summary_defaults_to_none(self):
        log = ChangeLogMT(tenant="t", table="t", pk=1, op="I", version=1)
        assert log.summary is None


class TestSyncStateMT:
    def test_create(self):
        state = SyncStateMT(
            tenant="acme",
            peer_id="peer1",
            last_pushed_change_id=0,
            last_pulled_change_id=0,
        )
        assert state.tenant == "acme"
        assert state.peer_id == "peer1"

    def test_default_watermarks(self):
        state = SyncStateMT(
            tenant="t",
            peer_id="p",
            last_pushed_change_id=0,
            last_pulled_change_id=0,
        )
        assert state.last_pushed_change_id == 0
        assert state.last_pulled_change_id == 0


# ============================================================================
# attach_change_hooks_mt_for_models Tests
# ============================================================================


class TestAttachChangeHooksMT:
    def test_raises_for_unmapped_model(self):
        class NotAModel:
            pass

        with pytest.raises(ValueError, match="has no table mapping"):
            attach_change_hooks_mt_for_models([NotAModel], lambda: "t")


# ============================================================================
# SyncEngineMT Tests
# ============================================================================

_mt_hooks_registered = False


class TestSyncEngineMT:
    @pytest.fixture
    def mt_setup(self):
        global _mt_hooks_registered
        set_id_generator("mt_node")
        engine = create_engine("sqlite:///:memory:")
        SABase.metadata.create_all(engine)
        SQLModel.metadata.create_all(engine)

        SessionFactory = sessionmaker(bind=engine, class_=SMSession)
        session = SessionFactory()

        schema = build_schema([MTUser])

        # Guard against hook stacking across fixtures
        if not _mt_hooks_registered:
            current_tenant = lambda: "test_tenant"
            attach_change_hooks_mt_for_models([MTUser], current_tenant)
            _mt_hooks_registered = True

        yield session, schema

        session.close()
        engine.dispose()

    def test_local_changes(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="MT User", email="mt@test.com")
        session.add(user)
        session.commit()

        changes = eng.local_changes_since(0)
        assert len(changes) >= 1
        assert changes[0]["table"] == "mt_test_users"

    def test_apply_remote_insert(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        changes = [
            {
                "id": 1,
                "table": "mt_test_users",
                "pk": 777777,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 777777,
                    "name": "Remote MT",
                    "email": "remote@mt.com",
                    "version": 1,
                },
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        user = session.exec(select(MTUser).where(MTUser.id == 777777)).first()
        assert user is not None
        assert user.name == "Remote MT"

    def test_apply_remote_delete(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="Del MT", email="del@mt.com")
        session.add(user)
        session.commit()
        uid = user.id

        changes = [
            {
                "id": 2,
                "table": "mt_test_users",
                "pk": uid,
                "op": "D",
                "version": 2,
                "data": None,
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()
        assert session.exec(select(MTUser).where(MTUser.id == uid)).first() is None

    def test_apply_remote_update(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="Original", email="orig@mt.com")
        session.add(user)
        session.commit()
        uid = user.id

        changes = [
            {
                "id": 3,
                "table": "mt_test_users",
                "pk": uid,
                "op": "U",
                "version": 5,
                "data": {"name": "Updated MT"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(MTUser).where(MTUser.id == uid)).first()
        assert updated.name == "Updated MT"

    def test_version_strict_rejects(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session,
            tenant="test_tenant",
            peer_id="p1",
            schema=schema,
            policy=ConflictPolicy.VERSION,
        )

        user = MTUser(name="Keep", email="keep@mt.com")
        session.add(user)
        session.commit()
        uid = user.id

        changes = [
            {
                "id": 4,
                "table": "mt_test_users",
                "pk": uid,
                "op": "U",
                "version": 0,
                "data": {"name": "ShouldNotChange"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        kept = session.exec(select(MTUser).where(MTUser.id == uid)).first()
        assert kept.name == "Keep"

    def test_ensure_state(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        st = eng._ensure_state()
        assert st.tenant == "test_tenant"
        assert st.last_pulled_change_id == 0

    def test_compute_order(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        assert "mt_test_users" in eng._order

    def test_pull_then_push(self, mt_setup):
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="Push", email="push@mt.com")
        session.add(user)
        session.commit()

        from data_shuttle_bridge.sql.transport import InMemoryPeerTransport

        transport = InMemoryPeerTransport()
        pulled, pushed = eng.pull_then_push(transport)
        assert pulled == 0
        assert pushed >= 1

    def test_ensure_state_idempotent(self, mt_setup):
        """Second call to _ensure_state returns the same record."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        st1 = eng._ensure_state()
        st2 = eng._ensure_state()
        assert st1.tenant == st2.tenant
        assert st1.peer_id == st2.peer_id
        assert st1.last_pushed_change_id == st2.last_pushed_change_id

    def test_serialize_change_delete(self, mt_setup):
        """_serialize_change for a delete op should have data=None."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        del_log = ChangeLogMT(
            tenant="test_tenant",
            table="mt_test_users",
            pk=999990,
            op="D",
            version=1,
        )
        session.add(del_log)
        session.commit()

        cp = eng._serialize_change(del_log)
        assert cp["op"] == "D"
        assert cp["data"] is None

    def test_serialize_change_missing_object(self, mt_setup):
        """_serialize_change when the referenced object no longer exists."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        fake_log = ChangeLogMT(
            tenant="test_tenant",
            table="mt_test_users",
            pk=999999,
            op="I",
            version=1,
        )
        session.add(fake_log)
        session.commit()

        cp = eng._serialize_change(fake_log)
        assert cp["op"] == "I"
        assert cp["data"] is None

    def test_apply_one_delete_nonexistent(self, mt_setup):
        """Deleting a non-existent row is a no-op (no error)."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        changes = [
            {
                "id": 10,
                "table": "mt_test_users",
                "pk": 888888,
                "op": "D",
                "version": 1,
                "data": None,
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

    def test_apply_one_insert_no_data(self, mt_setup):
        """Insert with data=None and NOT NULL columns raises IntegrityError on commit."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        changes = [
            {
                "id": 11,
                "table": "mt_test_users",
                "pk": 111111,
                "op": "I",
                "version": 3,
                "data": None,
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        with pytest.raises(SAIntegrityError):
            session.commit()
        session.rollback()

    def test_lww_allows_lower_version_update(self, mt_setup):
        """LWW policy allows update even when incoming version is lower."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session,
            tenant="test_tenant",
            peer_id="p1",
            schema=schema,
            policy=ConflictPolicy.LWW,
        )

        user = MTUser(name="Original", email="orig@test.com")
        session.add(user)
        session.commit()
        uid = user.id

        changes = [
            {
                "id": 20,
                "table": "mt_test_users",
                "pk": uid,
                "op": "U",
                "version": 0,
                "data": {"name": "LWW Updated"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(MTUser).where(MTUser.id == uid)).first()
        assert updated.name == "LWW Updated"

    def test_version_max_preserved_on_update(self, mt_setup):
        """Version is set to max(current, incoming) after update."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session,
            tenant="test_tenant",
            peer_id="p1",
            schema=schema,
            policy=ConflictPolicy.LWW,
        )

        user = MTUser(name="Versioned", email="v@test.com")
        session.add(user)
        session.commit()
        uid = user.id
        current_version = user.version
        higher_version = current_version + 10

        changes = [
            {
                "id": 40,
                "table": "mt_test_users",
                "pk": uid,
                "op": "U",
                "version": higher_version,
                "data": {"name": "Higher"},
                "at": None,
            }
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        updated = session.exec(select(MTUser).where(MTUser.id == uid)).first()
        # before_update hook bumps version +1, so final is higher_version + 1
        assert updated.version >= higher_version

    def test_pull_then_push_with_remote_changes(self, mt_setup):
        """pull_then_push pulls remote changes and applies them locally."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        from data_shuttle_bridge.sql.transport import InMemoryPeerTransport

        remote_changes = [
            {
                "id": 1,
                "table": "mt_test_users",
                "pk": 555555,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 555555,
                    "name": "Remote",
                    "email": "r@t.com",
                    "version": 1,
                },
                "at": None,
            }
        ]
        transport = InMemoryPeerTransport(changes=remote_changes)
        pulled, pushed = eng.pull_then_push(transport)
        assert pulled == 1

        obj = session.exec(select(MTUser).where(MTUser.id == 555555)).first()
        assert obj is not None
        assert obj.name == "Remote"

    def test_pull_then_push_updates_watermarks(self, mt_setup):
        """pull_then_push correctly updates SyncStateMT watermarks."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="WM", email="wm@test.com")
        session.add(user)
        session.commit()

        from data_shuttle_bridge.sql.transport import InMemoryPeerTransport

        transport = InMemoryPeerTransport()
        eng.pull_then_push(transport)

        st = eng._ensure_state()
        assert st.last_pushed_change_id > 0

    def test_local_changes_since_with_limit(self, mt_setup):
        """local_changes_since respects the limit parameter."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        for i in range(5):
            user = MTUser(name=f"User{i}", email=f"u{i}@test.com")
            session.add(user)
            session.commit()

        changes = eng.local_changes_since(0, limit=2)
        assert len(changes) == 2

    def test_apply_remote_changes_multiple_inserts(self, mt_setup):
        """apply_remote_changes handles multiple inserts in a batch."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )
        changes = [
            {
                "id": 30,
                "table": "mt_test_users",
                "pk": 300001,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 300001,
                    "name": "First",
                    "email": "a@t.com",
                    "version": 1,
                },
                "at": None,
            },
            {
                "id": 31,
                "table": "mt_test_users",
                "pk": 300002,
                "op": "I",
                "version": 1,
                "data": {
                    "id": 300002,
                    "name": "Second",
                    "email": "b@t.com",
                    "version": 1,
                },
                "at": None,
            },
        ]
        eng.apply_remote_changes(changes)
        session.commit()

        assert (
            session.exec(select(MTUser).where(MTUser.id == 300001)).first() is not None
        )
        assert (
            session.exec(select(MTUser).where(MTUser.id == 300002)).first() is not None
        )

    def test_local_changes_filtered_by_tenant(self, mt_setup):
        """local_changes_since only returns changes for the engine's tenant."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        # Insert a changelog entry for a different tenant directly
        other_log = ChangeLogMT(
            tenant="other_tenant",
            table="mt_test_users",
            pk=1,
            op="I",
            version=1,
        )
        session.add(other_log)
        session.commit()

        changes = eng.local_changes_since(0)
        for c in changes:
            # All returned changes should be from the engine's tenant
            log = session.get(ChangeLogMT, c["id"])
            assert log.tenant == "test_tenant"

    def test_serialize_change_insert_with_object(self, mt_setup):
        """_serialize_change for an insert returns the object's data."""
        session, schema = mt_setup
        eng = SyncEngineMT(
            session=session, tenant="test_tenant", peer_id="p1", schema=schema
        )

        user = MTUser(name="Serialize Me", email="ser@test.com")
        session.add(user)
        session.commit()

        log = ChangeLogMT(
            tenant="test_tenant",
            table="mt_test_users",
            pk=user.id,
            op="I",
            version=1,
        )
        session.add(log)
        session.commit()

        cp = eng._serialize_change(log)
        assert cp["data"] is not None
        assert cp["data"]["name"] == "Serialize Me"
        assert cp["data"]["email"] == "ser@test.com"


# ============================================================================
# Flask Blueprint Tests — DB-per-Tenant
# ============================================================================


DBPerTenantBase = declarative_base()


class DBTUser(DBPerTenantBase, SyncRowSAMixin):
    __tablename__ = "dbt_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


class TestDBPerTenantBlueprint:
    @pytest.fixture
    def app_setup(self):
        from flask import Flask
        from data_shuttle_bridge.sql.tenancy import tenant_sync_blueprint_db_per_tenant

        set_id_generator("dbt_node")
        engine = create_engine("sqlite:///:memory:")
        DBPerTenantBase.metadata.create_all(engine)
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)

        bp = tenant_sync_blueprint_db_per_tenant(
            session_factory_for_tenant=lambda tenant: SessionFactory(),
            models=[DBTUser],
        )
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        yield app, SessionFactory, engine

        engine.dispose()

    def test_get_changes(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.get("/sync/changes?since_id=0&tenant=acme")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "changes" in data

    def test_get_changes_default_tenant(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.get("/sync/changes")
            assert resp.status_code == 200
            assert "changes" in resp.get_json()

    def test_apply(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.post(
                "/sync/apply?tenant=acme",
                data=json.dumps({"changes": []}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_apply_empty_body(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.post(
                "/sync/apply?tenant=acme",
                data=json.dumps({}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_ack(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.post("/sync/ack?tenant=acme")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True


# ============================================================================
# Flask Blueprint Tests — Row-Level Tenancy
# ============================================================================


RowLevelBase = declarative_base()


class RLUser(RowLevelBase, SyncRowSAMixin):
    __tablename__ = "rl_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


class TestRowLevelBlueprint:
    @pytest.fixture
    def app_setup(self):
        from flask import Flask
        from data_shuttle_bridge.sql.tenancy import tenant_sync_blueprint_row_level

        set_id_generator("rl_node")
        engine = create_engine("sqlite:///:memory:")
        RowLevelBase.metadata.create_all(engine)
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)

        _current_tenant = "rl_tenant"

        bp = tenant_sync_blueprint_row_level(
            session_factory=lambda: SessionFactory(),
            models=[RLUser],
            tenant_resolver=lambda: _current_tenant,
        )
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        yield app, SessionFactory, engine

        engine.dispose()

    def test_get_changes(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.get("/sync/changes?since_id=0")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "changes" in data

    def test_apply(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.post(
                "/sync/apply",
                data=json.dumps({"changes": []}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_ack(self, app_setup):
        app, _, _ = app_setup
        with app.test_client() as c:
            resp = c.post("/sync/ack")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_apply_insert_and_read_back(self, app_setup):
        app, SessionFactory, _ = app_setup
        with app.test_client() as c:
            changes = [
                {
                    "id": 1,
                    "table": "rl_users",
                    "pk": 600001,
                    "op": "I",
                    "version": 1,
                    "data": {
                        "id": 600001,
                        "name": "RL User",
                        "email": "rl@test.com",
                        "version": 1,
                    },
                    "at": None,
                }
            ]
            resp = c.post(
                "/sync/apply",
                data=json.dumps({"changes": changes}),
                content_type="application/json",
            )
            assert resp.status_code == 200

            session = SessionFactory()
            user = session.exec(select(RLUser).where(RLUser.id == 600001)).first()
            assert user is not None
            assert user.name == "RL User"
            session.close()
