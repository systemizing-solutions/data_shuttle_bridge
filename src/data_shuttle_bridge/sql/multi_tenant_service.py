"""
Multi-tenant service for data_shuttle_bridge

Provides:
- Tenant lifecycle management (create, list, delete)
- Per-tenant database connections and sessions
- API key generation and validation
- Encrypted secrets storage per tenant
- Flask integration with multi-tenant sync blueprints
"""

import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Type

from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer as Serializer
from flask import Flask, Blueprint, request, jsonify, g
from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    JSON,
    DateTime,
    event,
    create_engine,
)
from sqlalchemy.orm import sessionmaker, relationship
from sqlmodel import SQLModel, Field, select, Relationship, Session

from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import attach_change_hooks_for_models
from data_shuttle_bridge.models.tenant import Tenant, TenantSecret


# ===========================
# Encryption & Secrets
# ===========================


class SecretManager:
    """Manages encryption/decryption of tenant secrets."""

    def __init__(self, fernet_key: bytes | None = None):
        """
        Initialize with a Fernet key.

        Args:
            fernet_key: Fernet key bytes. If None, generates or loads from env.
        """
        if fernet_key is None:
            fernet_key = os.environ.get("FERNET_KEY")
            if fernet_key:
                fernet_key = (
                    fernet_key.encode() if isinstance(fernet_key, str) else fernet_key
                )
            else:
                fernet_key = Fernet.generate_key()

        self.cipher_suite = Fernet(fernet_key)

    def encrypt(self, secret: str) -> str:
        """Encrypt a secret string."""
        encrypted = self.cipher_suite.encrypt(secret.encode("utf-8"))
        return encrypted.decode("utf-8")

    def decrypt(self, encrypted_secret: str) -> str:
        """Decrypt a secret string."""
        decrypted = self.cipher_suite.decrypt(encrypted_secret.encode("utf-8"))
        return decrypted.decode("utf-8")


# ===========================
# Tenant Manager
# ===========================


class TenantManager:
    """Manages tenant lifecycle, databases, and sessions."""

    def __init__(
        self,
        master_session_factory: Callable[[], Session],
        secret_manager: SecretManager,
        tenant_base_path: str = ".",
    ):
        """
        Initialize the tenant manager.

        Args:
            master_session_factory: Factory function returning a Session to the master database
            secret_manager: SecretManager instance for encrypting/decrypting secrets
            tenant_base_path: Base path for tenant database files
        """
        self.master_session_factory = master_session_factory
        self.secret_manager = secret_manager
        self.tenant_base_path = tenant_base_path

        # Cache for per-tenant engines and session factories
        self._engines: Dict[str, Any] = {}
        self._session_factories: Dict[str, Callable[[], Session]] = {}

    def create_tenant(
        self,
        name: str,
        slug: str | None = None,
        metadata: dict | None = None,
    ) -> Tenant:
        """
        Create a new tenant.

        Args:
            name: Human-readable tenant name
            slug: URL-safe slug (auto-generated if not provided)
            metadata: Optional metadata JSON

        Returns:
            Newly created Tenant

        Raises:
            ValueError: If name or slug already exists
        """
        if slug is None:
            slug = name.lower().replace(" ", "-").replace("_", "-")

        with self.master_session_factory() as sess:
            # Check for duplicates
            existing = sess.exec(select(Tenant).where(Tenant.name == name)).first()
            if existing:
                raise ValueError(f"Tenant '{name}' already exists")

            existing = sess.exec(select(Tenant).where(Tenant.slug == slug)).first()
            if existing:
                raise ValueError(f"Slug '{slug}' already in use")

            # Generate API key
            api_key = self._generate_api_key(name)

            # Create database file path
            database_url = f"sqlite:///{self.tenant_base_path}/{slug}.db"

            # Create tenant record
            tenant = Tenant(
                name=name,
                slug=slug,
                api_key=api_key,
                database_url=database_url,
                metadata_json=metadata or {},
            )

            sess.add(tenant)
            sess.commit()
            sess.refresh(tenant)

            # Initialize tenant database
            self._init_tenant_db(tenant)

            return tenant

    def get_tenant_by_api_key(self, api_key: str) -> Tenant | None:
        """Get tenant by API key."""
        with self.master_session_factory() as sess:
            return sess.exec(
                select(Tenant).where(
                    (Tenant.api_key == api_key) & (Tenant.is_active == True)
                )
            ).first()

    def get_tenant(self, tenant_id: int | str) -> Tenant | None:
        """Get tenant by ID or slug."""
        with self.master_session_factory() as sess:
            if isinstance(tenant_id, int):
                return sess.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
            else:
                return sess.exec(select(Tenant).where(Tenant.slug == tenant_id)).first()

    def list_tenants(self, active_only: bool = True) -> list[Tenant]:
        """List all tenants."""
        with self.master_session_factory() as sess:
            query = select(Tenant)
            if active_only:
                query = query.where(Tenant.is_active == True)
            return sess.exec(query).all()

    def delete_tenant(self, tenant_id: int | str) -> bool:
        """Delete a tenant and its database."""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        with self.master_session_factory() as sess:
            db_tenant = sess.get(Tenant, tenant.id)
            if db_tenant:
                sess.delete(db_tenant)
                sess.commit()

        # Clear caches
        slug = tenant.slug
        if slug in self._engines:
            del self._engines[slug]
        if slug in self._session_factories:
            del self._session_factories[slug]

        # TODO: Delete database file if needed
        return True

    def get_session_factory_for_tenant(self, tenant: Tenant) -> Callable[[], Session]:
        """
        Get or create a session factory for a tenant.

        Args:
            tenant: Tenant instance

        Returns:
            Callable that returns a Session for the tenant's database
        """
        if tenant.slug not in self._session_factories:
            engine = self._get_engine_for_tenant(tenant)
            SessionLocal = sessionmaker(bind=engine, class_=Session)
            self._session_factories[tenant.slug] = SessionLocal

        return self._session_factories[tenant.slug]

    def _get_engine_for_tenant(self, tenant: Tenant) -> Any:
        """Get or create SQLAlchemy engine for a tenant."""
        if tenant.slug not in self._engines:
            self._engines[tenant.slug] = create_engine(
                tenant.database_url,
                connect_args={"check_same_thread": False},
            )

        return self._engines[tenant.slug]

    def _init_tenant_db(self, tenant: Tenant):
        """Initialize a tenant's database with empty schema."""
        engine = self._get_engine_for_tenant(tenant)
        SQLModel.metadata.create_all(engine)

    def _generate_api_key(self, tenant_name: str) -> str:
        """Generate a unique API key for a tenant."""
        s = Serializer(os.environ.get("SECRET_KEY", secrets.token_hex(32)))
        # Append random suffix to ensure uniqueness
        suffix = secrets.token_hex(8)
        return f"{s.dumps({'tenant': tenant_name})}.{suffix}"

    # ===== Secrets Management =====

    def set_secret(self, tenant: Tenant, key: str, secret: str):
        """Set/update a secret for a tenant."""
        with self.master_session_factory() as sess:
            # Get tenant from session
            db_tenant = sess.get(Tenant, tenant.id)
            if not db_tenant:
                raise ValueError("Tenant not found")

            # Find existing secret
            existing = sess.exec(
                select(TenantSecret).where(
                    (TenantSecret.tenant_id == tenant.id) & (TenantSecret.key == key)
                )
            ).first()

            encrypted = self.secret_manager.encrypt(secret)

            if existing:
                existing.secret = encrypted
                existing.updated_at = datetime.utcnow()
                sess.add(existing)
            else:
                new_secret = TenantSecret(
                    tenant_id=tenant.id,
                    key=key,
                    secret=encrypted,
                )
                sess.add(new_secret)

            sess.commit()

    def get_secret(self, tenant: Tenant, key: str) -> str | None:
        """Get a secret for a tenant."""
        with self.master_session_factory() as sess:
            db_secret = sess.exec(
                select(TenantSecret).where(
                    (TenantSecret.tenant_id == tenant.id) & (TenantSecret.key == key)
                )
            ).first()

            if not db_secret:
                return None

            return self.secret_manager.decrypt(db_secret.secret)

    def delete_secret(self, tenant: Tenant, key: str) -> bool:
        """Delete a secret for a tenant."""
        with self.master_session_factory() as sess:
            db_secret = sess.exec(
                select(TenantSecret).where(
                    (TenantSecret.tenant_id == tenant.id) & (TenantSecret.key == key)
                )
            ).first()

            if db_secret:
                sess.delete(db_secret)
                sess.commit()
                return True
            return False

    def list_secrets(self, tenant: Tenant) -> list[str]:
        """List all secret keys for a tenant."""
        with self.master_session_factory() as sess:
            secrets = sess.exec(
                select(TenantSecret).where(TenantSecret.tenant_id == tenant.id)
            ).all()
            return [s.key for s in secrets]


# ===========================
# Flask Integration
# ===========================


def create_multi_tenant_app(
    master_db_url: str,
    models: Iterable[Type],
    secret_key: str | None = None,
    fernet_key: bytes | None = None,
    tenant_base_path: str = ".",
    conflict_policy: ConflictPolicy = ConflictPolicy.LWW,
    tenant_master_key: str | None = None,
) -> tuple[Flask, TenantManager]:
    """
    Create a Flask app with multi-tenant support.

    Args:
        master_db_url: Database URL for master (tenant metadata) database
        models: List of SQLModel/SQLAlchemy models to sync
        secret_key: Flask secret key (auto-generated if not provided)
        fernet_key: Encryption key for secrets (auto-generated if not provided)
        tenant_base_path: Base path for tenant database files
        conflict_policy: Conflict resolution policy
        tenant_master_key: Master key for tenant management endpoints (from env var if not provided)

    Returns:
        Tuple of (Flask app, TenantManager)
    """
    app = Flask(__name__)

    # Configure app
    app.config["SECRET_KEY"] = secret_key or os.environ.get(
        "SECRET_KEY", secrets.token_hex(32)
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = master_db_url

    # Initialize tenant master key for management endpoints
    if tenant_master_key is None:
        tenant_master_key = os.environ.get("TENANT_MASTER_KEY")

    # Initialize master database
    master_engine = create_engine(master_db_url)
    SQLModel.metadata.create_all(master_engine)
    MasterSessionLocal = sessionmaker(bind=master_engine, class_=Session)

    # Initialize secret manager
    secret_manager = SecretManager(fernet_key)

    # Initialize tenant manager
    tenant_mgr = TenantManager(
        master_session_factory=MasterSessionLocal,
        secret_manager=secret_manager,
        tenant_base_path=tenant_base_path,
    )

    # Attach change hooks to models
    models = list(models)
    attach_change_hooks_for_models(models)
    SCHEMA = build_schema(models)

    # ===== Helper Functions =====

    def _validate_tenant_master_key() -> bool:
        """Validate tenant master key from X-Tenant-Key header."""
        if tenant_master_key is None:
            # No master key configured, allow management access
            return True

        provided_key = request.headers.get("X-Tenant-Key")
        if provided_key is None:
            return False

        return provided_key == tenant_master_key

    def _get_tenant_from_request() -> Tenant | None:
        """Extract tenant from request (API key in header or query param)."""
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not api_key:
            return None

        tenant = tenant_mgr.get_tenant_by_api_key(api_key)
        return tenant

    def _require_tenant() -> Tenant:
        """Get tenant from request or abort with 401."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return tenant

    @app.before_request
    def before_request():
        """Populate g.tenant if API key is provided."""
        g.tenant = _get_tenant_from_request()

    # ===== Tenant Management Endpoints =====

    @app.post("/api/tenants")
    def create_tenant_endpoint():
        """Create a new tenant (requires X-Tenant-Key header)."""
        if not _validate_tenant_master_key():
            return jsonify({"error": "Invalid or missing X-Tenant-Key"}), 401

        data = request.get_json() or {}
        name = data.get("name")
        slug = data.get("slug")
        metadata = data.get("metadata", {})

        if not name:
            return jsonify({"error": "name is required"}), 400

        try:
            tenant = tenant_mgr.create_tenant(name, slug, metadata)
            return (
                jsonify(
                    {
                        "id": tenant.id,
                        "name": tenant.name,
                        "slug": tenant.slug,
                        "api_key": tenant.api_key,
                        "database_url": tenant.database_url,
                        "created_at": tenant.created_at.isoformat(),
                    }
                ),
                201,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/tenants")
    def list_tenants_endpoint():
        """List all tenants (requires X-Tenant-Key header)."""
        if not _validate_tenant_master_key():
            return jsonify({"error": "Invalid or missing X-Tenant-Key"}), 401

        tenants = tenant_mgr.list_tenants()
        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "slug": t.slug,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tenants
            ]
        )

    @app.get("/api/tenants/<tenant_id>")
    def get_tenant_endpoint(tenant_id):
        """Get tenant info by ID or slug (requires X-Tenant-Key header)."""
        if not _validate_tenant_master_key():
            return jsonify({"error": "Invalid or missing X-Tenant-Key"}), 401

        tenant = tenant_mgr.get_tenant(tenant_id)
        if not tenant:
            return jsonify({"error": "Tenant not found"}), 404

        return jsonify(
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "created_at": tenant.created_at.isoformat(),
            }
        )

    @app.delete("/api/tenants/<tenant_id>")
    def delete_tenant_endpoint(tenant_id):
        """Delete a tenant (requires X-Tenant-Key header)."""
        if not _validate_tenant_master_key():
            return jsonify({"error": "Invalid or missing X-Tenant-Key"}), 401

        success = tenant_mgr.delete_tenant(tenant_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"error": "Tenant not found"}), 404

    # ===== Secrets Management Endpoints =====

    @app.post("/api/secrets")
    def set_secret_endpoint():
        """Set a secret for the authenticated tenant."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        data = request.get_json() or {}
        key = data.get("key")
        secret = data.get("secret")

        if not key or not secret:
            return jsonify({"error": "key and secret are required"}), 400

        try:
            tenant_mgr.set_secret(tenant, key, secret)
            return jsonify({"message": "Secret stored successfully"}), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/secrets/<key>")
    def get_secret_endpoint(key):
        """Get a secret for the authenticated tenant."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        secret = tenant_mgr.get_secret(tenant, key)
        if secret is None:
            return jsonify({"error": "Secret not found"}), 404

        return jsonify({"secret": secret})

    @app.delete("/api/secrets/<key>")
    def delete_secret_endpoint(key):
        """Delete a secret for the authenticated tenant."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        success = tenant_mgr.delete_secret(tenant, key)
        if success:
            return jsonify({"ok": True})
        return jsonify({"error": "Secret not found"}), 404

    @app.get("/api/secrets")
    def list_secrets_endpoint():
        """List all secret keys for the authenticated tenant."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        keys = tenant_mgr.list_secrets(tenant)
        return jsonify({"secrets": keys})

    # ===== Data Sync Endpoints =====

    def _get_tenant_session_factory():
        """Get session factory for the authenticated tenant."""
        tenant = g.get("tenant")
        if not tenant:
            return None
        return tenant_mgr.get_session_factory_for_tenant(tenant)

    @app.get("/api/sync/changes")
    def get_changes():
        """Get changes for authenticated tenant."""
        tenant = g.get("tenant")
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        session_factory = tenant_mgr.get_session_factory_for_tenant(tenant)
        with session_factory() as sess:
            engine = SyncEngine(
                session=sess,
                peer_id=f"api-client",
                schema=SCHEMA,
                policy=conflict_policy,
                node_id=f"tenant:{tenant.id}",
            )
            since_id = int(request.args.get("since_id", "0"))
            limit = int(request.args.get("limit", "1000"))
            changes = engine.local_changes_since(since_id, limit=limit)
            return jsonify({"changes": changes})

    @app.post("/api/sync/apply")
    def apply_changes():
        """Apply remote changes for authenticated tenant."""
        tenant = g.get("tenant")
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        session_factory = tenant_mgr.get_session_factory_for_tenant(tenant)
        with session_factory() as sess:
            engine = SyncEngine(
                session=sess,
                peer_id=f"api-client",
                schema=SCHEMA,
                policy=conflict_policy,
                node_id=f"tenant:{tenant.id}",
            )
            payload = request.get_json(force=True) or {}
            changes = payload.get("changes", [])
            engine.apply_remote_changes(changes)
            sess.commit()
            return jsonify({"ok": True})

    return app, tenant_mgr
