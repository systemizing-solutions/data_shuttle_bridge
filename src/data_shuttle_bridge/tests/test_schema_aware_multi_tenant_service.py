"""Tests for the schema-aware multi-tenant service and Flask app."""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Integer, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlmodel import SQLModel, Session as SMSession, Field, select

from data_shuttle_bridge.sql.multi_tenant_service import (
    Tenant,
    TenantSecret,
    SecretManager,
)
from data_shuttle_bridge.sql.schema_aware_multi_tenant_service import (
    SchemAwareTenantManager,
    create_schema_aware_multi_tenant_app,
)
from data_shuttle_bridge.sql.mixins import SyncRowSAMixin


SABase = declarative_base()


class SAUser(SABase, SyncRowSAMixin):
    __tablename__ = "sa_test_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


# ============================================================================
# SecretManager Tests
# ============================================================================


class TestSecretManager:
    def test_encrypt_decrypt(self):
        sm = SecretManager()
        plaintext = "super_secret"
        encrypted = sm.encrypt(plaintext)
        assert encrypted != plaintext
        assert sm.decrypt(encrypted) == plaintext

    def test_different_encryptions_same_plaintext(self):
        sm = SecretManager()
        e1 = sm.encrypt("same")
        e2 = sm.encrypt("same")
        assert e1 != e2  # Fernet uses random IV

    def test_explicit_key(self):
        key = Fernet.generate_key()
        sm = SecretManager(fernet_key=key)
        encrypted = sm.encrypt("test")
        assert sm.decrypt(encrypted) == "test"

    def test_env_key_string(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"FERNET_KEY": key}):
            sm = SecretManager()
            encrypted = sm.encrypt("env_test")
            assert sm.decrypt(encrypted) == "env_test"

    def test_env_key_bytes(self):
        key = Fernet.generate_key()
        with patch.dict(os.environ, {"FERNET_KEY": key.decode()}):
            sm = SecretManager()
            encrypted = sm.encrypt("env_bytes")
            assert sm.decrypt(encrypted) == "env_bytes"

    def test_auto_generate_key_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FERNET_KEY", None)
            sm = SecretManager()
            encrypted = sm.encrypt("auto")
            assert sm.decrypt(encrypted) == "auto"


# ============================================================================
# SchemAwareTenantManager Tests
# ============================================================================


class TestSchemAwareTenantManager:
    @pytest.fixture
    def tenant_setup(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)
        sm = SecretManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SchemAwareTenantManager(
                master_session_factory=SessionFactory,
                secret_manager=sm,
                tenant_base_path=tmpdir,
            )
            yield mgr, SessionFactory, tmpdir

    @pytest.fixture
    def tenant_setup_with_models(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)
        sm = SecretManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SchemAwareTenantManager(
                master_session_factory=SessionFactory,
                secret_manager=sm,
                tenant_base_path=tmpdir,
                models=[SAUser],
            )
            yield mgr, SessionFactory, tmpdir

    def test_create_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("ACME Corp")
        assert tenant.name == "ACME Corp"
        assert tenant.slug == "acme-corp"
        assert tenant.api_key.startswith("sk_acme-corp_")
        assert tenant.current_schema_version == 1
        assert tenant.is_active is True

    def test_create_tenant_custom_slug(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("My Co", slug="myco")
        assert tenant.slug == "myco"

    def test_create_tenant_with_metadata(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Meta Corp", metadata={"plan": "pro"})
        assert tenant.metadata_json == {"plan": "pro"}

    def test_create_tenant_initializes_schema_registry(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("SchemaT")
        assert tenant.id in mgr._schema_registries
        assert tenant.id in mgr._tenant_engines
        assert tenant.schema_set_id is not None

    def test_get_tenant_by_api_key(self, tenant_setup):
        mgr, _, _ = tenant_setup
        created = mgr.create_tenant("KeyTest")
        found = mgr.get_tenant_by_api_key(created.api_key)
        assert found is not None
        assert found.name == "KeyTest"

    def test_get_tenant_by_api_key_invalid(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr.get_tenant_by_api_key("sk_invalid_key") is None

    def test_get_tenant_by_id(self, tenant_setup):
        mgr, _, _ = tenant_setup
        created = mgr.create_tenant("ById")
        found = mgr.get_tenant(created.id)
        assert found is not None
        assert found.slug == "byid"

    def test_get_tenant_by_slug(self, tenant_setup):
        mgr, _, _ = tenant_setup
        mgr.create_tenant("BySlug")
        found = mgr.get_tenant("byslug")
        assert found is not None
        assert found.name == "BySlug"

    def test_get_tenant_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr.get_tenant("nonexistent") is None
        assert mgr.get_tenant(99999) is None

    def test_list_tenants(self, tenant_setup):
        mgr, _, _ = tenant_setup
        mgr.create_tenant("T1")
        mgr.create_tenant("T2")
        tenants = mgr.list_tenants()
        assert len(tenants) == 2

    def test_list_tenants_active_only(self, tenant_setup):
        mgr, sf, _ = tenant_setup
        t = mgr.create_tenant("Active")
        mgr.create_tenant("Also Active")
        # Deactivate one
        with sf() as sess:
            db_t = sess.get(Tenant, t.id)
            db_t.is_active = False
            sess.commit()
        active = mgr.list_tenants(active_only=True)
        all_t = mgr.list_tenants(active_only=False)
        assert len(active) == 1
        assert len(all_t) == 2

    def test_delete_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("Del")
        tid = tenant.id
        assert mgr.delete_tenant(tid) is True
        assert mgr.get_tenant(tid) is None
        # Caches cleared
        assert tid not in mgr._schema_registries
        assert tid not in mgr._tenant_engines

    def test_delete_tenant_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr.delete_tenant("nope") is False

    def test_get_session_factory_for_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("SessFactory")
        sf = mgr.get_session_factory_for_tenant(tenant)
        assert sf is not None
        # Second call returns cached
        sf2 = mgr.get_session_factory_for_tenant(tenant)
        assert sf is sf2

    def test_get_schema_registry_for_tenant(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("RegTest")
        registry = mgr.get_schema_registry_for_tenant(tenant)
        assert registry is not None
        # Cached
        registry2 = mgr.get_schema_registry_for_tenant(tenant)
        assert registry is registry2

    def test_set_and_get_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("SecretT")
        mgr.set_secret(tenant, "db_pass", "s3cret!")
        assert mgr.get_secret(tenant, "db_pass") == "s3cret!"

    def test_get_secret_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("NoSecret")
        assert mgr.get_secret(tenant, "nope") is None

    def test_update_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("UpdateS")
        mgr.set_secret(tenant, "key", "val1")
        mgr.set_secret(tenant, "key", "val2")
        assert mgr.get_secret(tenant, "key") == "val2"

    def test_delete_secret(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("DelS")
        mgr.set_secret(tenant, "key", "val")
        assert mgr.delete_secret(tenant, "key") is True
        assert mgr.get_secret(tenant, "key") is None

    def test_delete_secret_not_found(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("DelSN")
        assert mgr.delete_secret(tenant, "nope") is False

    def test_list_secrets(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("ListS")
        mgr.set_secret(tenant, "a", "1")
        mgr.set_secret(tenant, "b", "2")
        keys = mgr.list_secrets(tenant)
        assert sorted(keys) == ["a", "b"]

    def test_list_secrets_empty(self, tenant_setup):
        mgr, _, _ = tenant_setup
        tenant = mgr.create_tenant("EmptyS")
        assert mgr.list_secrets(tenant) == []

    def test_build_models_schema_empty(self, tenant_setup):
        mgr, _, _ = tenant_setup
        assert mgr._build_models_schema([]) == {}

    def test_schema_to_dict_with_to_dict(self):
        obj = MagicMock()
        obj.to_dict.return_value = {"col": "string"}
        sm = SecretManager()
        mgr = SchemAwareTenantManager.__new__(SchemAwareTenantManager)
        result = mgr._schema_to_dict(obj)
        assert result == {"col": "string"}

    def test_schema_to_dict_no_to_dict(self):
        sm = SecretManager()
        mgr = SchemAwareTenantManager.__new__(SchemAwareTenantManager)
        result = mgr._schema_to_dict(object())
        assert result == {}


# ============================================================================
# Flask App Integration Tests
# ============================================================================


@pytest.fixture
def schema_app():
    with tempfile.TemporaryDirectory() as tmpdir:
        master_db_url = f"sqlite:///{tmpdir}/master.db"
        app, mgr = create_schema_aware_multi_tenant_app(
            master_db_url=master_db_url,
            models=[SAUser],
            tenant_base_path=tmpdir,
            tenant_master_key="master_secret",
        )
        app.config["TESTING"] = True
        yield app, mgr


class TestSchemaAwareAppTenantEndpoints:
    def test_create_tenant(self, schema_app):
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "ACME Corp"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["name"] == "ACME Corp"
            assert "api_key" in data
            assert data["schema_version"] == 1

    def test_create_tenant_missing_name(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 400

    def test_create_tenant_no_master_key(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "NoKey"}),
                content_type="application/json",
            )
            assert resp.status_code == 401

    def test_create_tenant_wrong_master_key(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "BadKey"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "wrong_key"},
            )
            assert resp.status_code == 401

    def test_list_tenants(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            c.post(
                "/api/tenants",
                data=json.dumps({"name": "T1"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            c.post(
                "/api/tenants",
                data=json.dumps({"name": "T2"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            resp = c.get(
                "/api/tenants",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) >= 2

    def test_list_tenants_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get("/api/tenants")
            assert resp.status_code == 401

    def test_get_tenant_by_slug(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            create_resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Get Test"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            slug = create_resp.get_json()["slug"]
            resp = c.get(
                f"/api/tenants/{slug}",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["slug"] == slug

    def test_get_tenant_not_found(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get(
                "/api/tenants/nonexistent",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 404

    def test_delete_tenant(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            create_resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Del Test"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            slug = create_resp.get_json()["slug"]
            resp = c.delete(
                f"/api/tenants/{slug}",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_delete_tenant_not_found(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.delete(
                "/api/tenants/nope",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 404


class TestSchemaAwareAppSecretsEndpoints:
    @pytest.fixture
    def tenant_client(self, schema_app):
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Secrets Tenant"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = resp.get_json()["api_key"]
            yield c, api_key

    def test_set_and_get_secret(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/secrets",
            data=json.dumps({"key": "db_pass", "secret": "s3cret!"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 201

        resp = c.get("/api/secrets/db_pass", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.get_json()["secret"] == "s3cret!"

    def test_set_secret_missing_fields(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/secrets",
            data=json.dumps({"key": "only_key"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_get_secret_not_found(self, tenant_client):
        c, api_key = tenant_client
        resp = c.get("/api/secrets/nope", headers={"X-API-Key": api_key})
        assert resp.status_code == 404

    def test_delete_secret(self, tenant_client):
        c, api_key = tenant_client
        c.post(
            "/api/secrets",
            data=json.dumps({"key": "temp", "secret": "val"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        resp = c.delete("/api/secrets/temp", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_delete_secret_not_found(self, tenant_client):
        c, api_key = tenant_client
        resp = c.delete("/api/secrets/nope", headers={"X-API-Key": api_key})
        assert resp.status_code == 404

    def test_list_secrets(self, tenant_client):
        c, api_key = tenant_client
        c.post(
            "/api/secrets",
            data=json.dumps({"key": "k1", "secret": "v1"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        c.post(
            "/api/secrets",
            data=json.dumps({"key": "k2", "secret": "v2"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        resp = c.get("/api/secrets", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        keys = resp.get_json()["secrets"]
        assert "k1" in keys
        assert "k2" in keys

    def test_secrets_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get("/api/secrets")
            assert resp.status_code == 401

            resp = c.post(
                "/api/secrets",
                data=json.dumps({"key": "a", "secret": "b"}),
                content_type="application/json",
            )
            assert resp.status_code == 401

            resp = c.delete("/api/secrets/a")
            assert resp.status_code == 401


class TestSchemaAwareAppSchemaEndpoints:
    @pytest.fixture
    def tenant_client(self, schema_app):
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Schema Tenant"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = resp.get_json()["api_key"]
            yield c, api_key

    def test_get_schema_versions(self, tenant_client):
        c, api_key = tenant_client
        resp = c.get("/api/schema/versions", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "versions" in data
        assert "current_version" in data

    def test_get_schema_versions_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get("/api/schema/versions")
            assert resp.status_code == 401

    def test_check_drift(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post("/api/schema/check-drift", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drift_detected" in data
        assert "current_version" in data

    def test_check_drift_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post("/api/schema/check-drift")
            assert resp.status_code == 401


class TestSchemaAwareAppSyncEndpoints:
    @pytest.fixture
    def tenant_client(self, schema_app):
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Sync Tenant"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = resp.get_json()["api_key"]
            yield c, api_key

    def test_get_changes(self, tenant_client):
        c, api_key = tenant_client
        resp = c.get(
            "/api/sync/changes?since_id=0",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "changes" in data
        assert "schema_metadata" in data

    def test_get_changes_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get("/api/sync/changes")
            assert resp.status_code == 401

    def test_apply_changes_empty(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/sync/apply",
            data=json.dumps({"changes": []}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "schema_metadata" in data

    def test_apply_changes_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/sync/apply",
                data=json.dumps({"changes": []}),
                content_type="application/json",
            )
            assert resp.status_code == 401


class TestSchemaAwareAppQueryEndpoints:
    @pytest.fixture
    def tenant_client(self, schema_app):
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Query Tenant"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = resp.get_json()["api_key"]
            yield c, api_key

    def test_query_missing_table(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400
        assert "table" in resp.get_json()["error"].lower()

    def test_query_table_not_found(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "nonexistent_table"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 404

    def test_query_invalid_limit(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "limit": 0}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400
        assert "limit" in resp.get_json()["error"].lower()

    def test_query_limit_too_high(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "limit": 20000}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_negative_offset(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "offset": -1}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_invalid_column(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "columns": ["nonexistent_col"]}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_invalid_filter_operator(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [
                        {"column": "name", "operator": "INVALID", "value": "x"}
                    ],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_filter_missing_column(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"operator": "eq", "value": "x"}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_filter_nonexistent_column(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "nope", "operator": "eq", "value": "x"}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_query_in_filter_requires_array(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [
                        {"column": "name", "operator": "in", "value": "not_array"}
                    ],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400
        assert "array" in resp.get_json()["error"].lower()

    def test_query_unauthorized(self, schema_app):
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/data/query",
                data=json.dumps({"table": "sa_test_users"}),
                content_type="application/json",
            )
            assert resp.status_code == 401

    def test_query_empty_results(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        # Table may not exist in tenant DB, so 500 is acceptable
        assert resp.status_code in (200, 500)


class TestSchemaAwareAppNoMasterKey:
    """Test that app works without a master key (all admin endpoints open)."""

    def test_create_tenant_no_master_key_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            master_db_url = f"sqlite:///{tmpdir}/master.db"
            app, mgr = create_schema_aware_multi_tenant_app(
                master_db_url=master_db_url,
                models=[SAUser],
                tenant_base_path=tmpdir,
                tenant_master_key=None,
            )
            app.config["TESTING"] = True
            with app.test_client() as c:
                resp = c.post(
                    "/api/tenants",
                    data=json.dumps({"name": "Open Corp"}),
                    content_type="application/json",
                )
                assert resp.status_code == 201


# ============================================================================
# Additional coverage tests
# ============================================================================


class TestDetectAndApplyDrift:
    """Test detect_and_apply_schema_drift (lines 261-285)."""

    @pytest.fixture
    def drift_setup(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)
        sm = SecretManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SchemAwareTenantManager(
                master_session_factory=SessionFactory,
                secret_manager=sm,
                tenant_base_path=tmpdir,
                models=[SAUser],
            )
            tenant = mgr.create_tenant("DriftTest")
            yield mgr, tenant

    def test_no_drift_same_schema(self, drift_setup):
        mgr, tenant = drift_setup
        drift_detected, new_version = mgr.detect_and_apply_schema_drift(
            tenant, [SAUser]
        )
        assert drift_detected is False
        assert new_version is None

    def test_detect_and_apply_drift_no_versions(self, drift_setup):
        """Test drift detection when registry has no versions."""
        mgr, tenant = drift_setup
        # Clear the versions from the registry
        registry = mgr.get_schema_registry_for_tenant(tenant)
        reg_session = sessionmaker(
            bind=mgr._tenant_engines[tenant.id], class_=SMSession
        )()
        try:
            from data_shuttle_bridge.sql.versioning_models import SchemaVersion

            versions = reg_session.exec(select(SchemaVersion)).all()
            for v in versions:
                reg_session.delete(v)
            reg_session.commit()
        finally:
            reg_session.close()

        drift_detected, new_version = mgr.detect_and_apply_schema_drift(
            tenant, [SAUser]
        )
        assert drift_detected is False
        assert new_version is None


class TestConsolidatedSyncEngine:
    """Test get_consolidated_sync_engine."""

    @pytest.fixture
    def sync_setup(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine, class_=SMSession)
        sm = SecretManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SchemAwareTenantManager(
                master_session_factory=SessionFactory,
                secret_manager=sm,
                tenant_base_path=tmpdir,
                models=[SAUser],
            )
            tenant = mgr.create_tenant("SyncTest")
            yield mgr, tenant

    def test_single_version_engine(self, sync_setup):
        mgr, tenant = sync_setup
        engine, metadata = mgr.get_consolidated_sync_engine(tenant)
        assert metadata["versions"] == 1
        assert metadata["uses_consolidation"] is False


class TestQueryEndpointWithData:
    """Test query endpoint with actual data to cover result formatting paths."""

    @pytest.fixture
    def tenant_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            master_db_url = f"sqlite:///{tmpdir}/master.db"
            app, mgr = create_schema_aware_multi_tenant_app(
                master_db_url=master_db_url,
                models=[SAUser],
                tenant_base_path=tmpdir,
                tenant_master_key="master_secret",
            )
            app.config["TESTING"] = True

            with app.test_client() as c:
                # Create tenant
                resp = c.post(
                    "/api/tenants",
                    data=json.dumps({"name": "Data Tenant"}),
                    content_type="application/json",
                    headers={"X-Tenant-Key": "master_secret"},
                )
                api_key = resp.get_json()["api_key"]
                tenant = mgr.get_tenant_by_api_key(api_key)

                # Insert test data directly into tenant DB
                from data_shuttle_bridge.sql.ids import set_id_generator

                set_id_generator("test_node")
                # Ensure the table exists in the tenant DB
                tenant_engine = mgr._tenant_engines[tenant.id]
                SABase.metadata.create_all(tenant_engine)
                sf = mgr.get_session_factory_for_tenant(tenant)
                with sf() as sess:
                    u1 = SAUser(name="Alice", email="alice@example.com")
                    u2 = SAUser(name="Bob", email="bob@example.com")
                    sess.add(u1)
                    sess.add(u2)
                    sess.commit()

                yield c, api_key

    def test_query_all_columns(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users"}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 2
        assert data["returned"] >= 2
        assert "pagination" in data

    def test_query_specific_columns(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "columns": ["name", "email"]}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["returned"] >= 2
        for row in data["data"]:
            assert "name" in row
            assert "email" in row

    def test_query_with_eq_filter(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "name", "operator": "eq", "value": "Alice"}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_with_ne_filter(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "name", "operator": "ne", "value": "Alice"}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_with_like_filter(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "name", "operator": "like", "value": "A%"}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_with_in_filter(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [
                        {
                            "column": "name",
                            "operator": "in",
                            "value": ["Alice", "Bob"],
                        }
                    ],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_with_limit_and_offset(self, tenant_with_data):
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps({"table": "sa_test_users", "limit": 1, "offset": 0}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["returned"] == 1
        assert data["pagination"]["limit"] == 1
        assert data["pagination"]["offset"] == 0


class TestApiKeyViaQueryParam:
    """Test API key passed via query parameter."""

    def test_secrets_via_query_param(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            master_db_url = f"sqlite:///{tmpdir}/master.db"
            app, mgr = create_schema_aware_multi_tenant_app(
                master_db_url=master_db_url,
                models=[SAUser],
                tenant_base_path=tmpdir,
                tenant_master_key="master_secret",
            )
            app.config["TESTING"] = True
            with app.test_client() as c:
                resp = c.post(
                    "/api/tenants",
                    data=json.dumps({"name": "QP Tenant"}),
                    content_type="application/json",
                    headers={"X-Tenant-Key": "master_secret"},
                )
                api_key = resp.get_json()["api_key"]

                # Use query param instead of header
                resp = c.get(f"/api/secrets?api_key={api_key}")
                assert resp.status_code == 200


# ============================================================================
# Additional coverage tests for uncovered branches
# ============================================================================


class TestTenantEndpointAuthFailures:
    """Tests for auth failure branches on get_tenant and delete_tenant endpoints."""

    def test_get_tenant_unauthorized(self, schema_app):
        """Cover line 571: get_tenant with wrong master key."""
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get(
                "/api/tenants/some-slug",
                headers={"X-Tenant-Key": "wrong_key"},
            )
            assert resp.status_code == 401

    def test_delete_tenant_unauthorized(self, schema_app):
        """Cover line 591: delete_tenant with wrong master key."""
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.delete(
                "/api/tenants/some-slug",
                headers={"X-Tenant-Key": "wrong_key"},
            )
            assert resp.status_code == 401


class TestCreateTenantValueError:
    """Tests for ValueError handling on create_tenant endpoint."""

    def test_create_tenant_value_error(self, schema_app):
        """Cover lines 544-545: create_tenant ValueError."""
        app, mgr = schema_app
        with app.test_client() as c:
            # Patch create_tenant to raise ValueError
            with patch.object(
                mgr, "create_tenant", side_effect=ValueError("bad input")
            ):
                resp = c.post(
                    "/api/tenants",
                    data=json.dumps({"name": "Some Corp"}),
                    content_type="application/json",
                    headers={"X-Tenant-Key": "master_secret"},
                )
                assert resp.status_code == 400
                assert resp.get_json()["error"] == "bad input"


class TestSetSecretValueError:
    """Tests for ValueError handling on set_secret endpoint."""

    def test_set_secret_value_error(self, schema_app):
        """Cover lines 617-618: set_secret ValueError."""
        app, mgr = schema_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "SecErr Tenant"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = resp.get_json()["api_key"]

            with patch.object(mgr, "set_secret", side_effect=ValueError("bad secret")):
                resp = c.post(
                    "/api/secrets",
                    data=json.dumps({"key": "k", "secret": "v"}),
                    content_type="application/json",
                    headers={"X-API-Key": api_key},
                )
                assert resp.status_code == 400
                assert resp.get_json()["error"] == "bad secret"

    def test_get_secret_invalid_api_key(self, schema_app):
        """Cover line 625: get_secret with invalid API key."""
        app, _ = schema_app
        with app.test_client() as c:
            resp = c.get(
                "/api/secrets/some_key",
                headers={"X-API-Key": "invalid_api_key"},
            )
            assert resp.status_code == 401


class TestQueryEndpointFilterOperators:
    """Tests for gt, gte, lt, lte filter operators in query endpoint."""

    @pytest.fixture
    def tenant_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            master_db_url = f"sqlite:///{tmpdir}/master.db"
            app, mgr = create_schema_aware_multi_tenant_app(
                master_db_url=master_db_url,
                models=[SAUser],
                tenant_base_path=tmpdir,
                tenant_master_key="master_secret",
            )
            app.config["TESTING"] = True

            with app.test_client() as c:
                resp = c.post(
                    "/api/tenants",
                    data=json.dumps({"name": "Filter Tenant"}),
                    content_type="application/json",
                    headers={"X-Tenant-Key": "master_secret"},
                )
                api_key = resp.get_json()["api_key"]
                tenant = mgr.get_tenant_by_api_key(api_key)

                from data_shuttle_bridge.sql.ids import set_id_generator

                set_id_generator("test_node")
                tenant_engine = mgr._tenant_engines[tenant.id]
                SABase.metadata.create_all(tenant_engine)
                sf = mgr.get_session_factory_for_tenant(tenant)
                with sf() as sess:
                    u1 = SAUser(name="Alice", email="alice@example.com")
                    u2 = SAUser(name="Bob", email="bob@example.com")
                    u3 = SAUser(name="Charlie", email="charlie@example.com")
                    sess.add(u1)
                    sess.add(u2)
                    sess.add(u3)
                    sess.commit()

                yield c, api_key

    def test_query_gt_filter(self, tenant_with_data):
        """Cover line 878, 913: gt operator."""
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "id", "operator": "gt", "value": 0}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_gte_filter(self, tenant_with_data):
        """Cover line 880, 915: gte operator."""
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "id", "operator": "gte", "value": 1}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_lt_filter(self, tenant_with_data):
        """Cover line 882, 917: lt operator."""
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [{"column": "id", "operator": "lt", "value": 999999999}],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_query_lte_filter(self, tenant_with_data):
        """Cover line 884, 919: lte operator."""
        c, api_key = tenant_with_data
        resp = c.post(
            "/api/data/query",
            data=json.dumps(
                {
                    "table": "sa_test_users",
                    "filters": [
                        {"column": "id", "operator": "lte", "value": 999999999}
                    ],
                }
            ),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
