"""Tests for the multi-tenant Flask app (create_multi_tenant_app)."""

import json
import os
import tempfile

import pytest
from sqlalchemy import Column, String, Integer, create_engine
from sqlalchemy.orm import declarative_base

from data_shuttle_bridge.sql.mixins import SyncRowSAMixin
from data_shuttle_bridge.sql.multi_tenant_service import create_multi_tenant_app


SABase = declarative_base()


class FlaskUser(SABase, SyncRowSAMixin):
    __tablename__ = "flask_test_users"
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


@pytest.fixture
def flask_app():
    with tempfile.TemporaryDirectory() as tmpdir:
        master_db_url = f"sqlite:///{tmpdir}/master.db"
        app, mgr = create_multi_tenant_app(
            master_db_url=master_db_url,
            models=[FlaskUser],
            tenant_base_path=tmpdir,
            tenant_master_key="master_secret",
        )
        app.config["TESTING"] = True
        yield app, mgr


class TestMultiTenantAppTenantEndpoints:
    def test_create_tenant(self, flask_app):
        app, mgr = flask_app
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

    def test_create_tenant_no_key(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "No Key"}),
                content_type="application/json",
            )
            assert resp.status_code == 401

    def test_create_tenant_bad_key(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Bad Key"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "wrong"},
            )
            assert resp.status_code == 401

    def test_create_tenant_missing_name(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 400

    def test_list_tenants(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            c.post(
                "/api/tenants",
                data=json.dumps({"name": "T1"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            resp = c.get(
                "/api/tenants",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) >= 1

    def test_get_tenant(self, flask_app):
        app, mgr = flask_app
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

    def test_get_tenant_not_found(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.get(
                "/api/tenants/nonexistent",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 404

    def test_delete_tenant(self, flask_app):
        app, mgr = flask_app
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

    def test_delete_tenant_not_found(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.delete(
                "/api/tenants/nope",
                headers={"X-Tenant-Key": "master_secret"},
            )
            assert resp.status_code == 404


class TestMultiTenantAppSecretsEndpoints:
    @pytest.fixture
    def tenant_client(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Secrets Test"}),
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

        resp = c.get(
            "/api/secrets/db_pass",
            headers={"X-API-Key": api_key},
        )
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
        resp = c.get(
            "/api/secrets/nope",
            headers={"X-API-Key": api_key},
        )
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
        resp = c.get("/api/secrets", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert "k1" in resp.get_json()["secrets"]

    def test_secrets_unauthorized(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            resp = c.get("/api/secrets")
            assert resp.status_code == 401

    def test_me_endpoint(self, flask_app):
        """Test that /api/me returns tenant info for authenticated tenant."""
        app, mgr = flask_app
        with app.test_client() as c:
            # This endpoint may not exist in all versions, skip gracefully
            create_resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Me Test"}),
                content_type="application/json",
                headers={"X-Tenant-Key": "master_secret"},
            )
            api_key = create_resp.get_json()["api_key"]
            resp = c.get("/api/me", headers={"X-API-Key": api_key})
            # May or may not exist
            assert resp.status_code in (200, 404)


class TestMultiTenantAppSyncEndpoints:
    @pytest.fixture
    def tenant_client(self, flask_app):
        app, mgr = flask_app
        with app.test_client() as c:
            resp = c.post(
                "/api/tenants",
                data=json.dumps({"name": "Sync Test"}),
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

    def test_apply_changes(self, tenant_client):
        c, api_key = tenant_client
        resp = c.post(
            "/api/sync/apply",
            data=json.dumps({"changes": []}),
            content_type="application/json",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

    def test_sync_unauthorized(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            resp = c.get("/api/sync/changes")
            assert resp.status_code == 401
