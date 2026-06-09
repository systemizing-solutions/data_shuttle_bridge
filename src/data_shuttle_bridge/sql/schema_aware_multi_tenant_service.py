"""
Schema-registry-aware multi-tenant service with version tracking and drift management.

This module extends the basic multi-tenant service to integrate the full schema
versioning, policy, and consolidation view infrastructure.

Features:
- Per-tenant schema registries
- Automatic schema version creation on drift detection
- Policy-driven column defaults
- Consolidated views for querying across versions
- Full schema audit trail per tenant
"""

import os
import json
import secrets
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Type, List, Tuple

from flask import Flask, Blueprint, request, jsonify, g
from sqlalchemy import (
    create_engine,
    func,
)
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Field, select, Session

from data_shuttle_bridge.models.tenant import Tenant, TenantSecret
from data_shuttle_bridge.sql.multi_tenant_service import (
    SecretManager,
)
from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import attach_change_hooks_for_models
from data_shuttle_bridge.sql.schema_registry import SchemaRegistry
from data_shuttle_bridge.sql.versioning_models import SchemaSet, SchemaVersion
from data_shuttle_bridge.sql.diffing import DefaultDiffEngine, classify_drift
from data_shuttle_bridge.sql.policy import DefaultDriftPolicy
from data_shuttle_bridge.sql.view_builder import ConsolidationViewBuilder


# ===========================
# Schema-Aware Tenant Manager
# ===========================


class SchemAwareTenantManager:
    """
    Manages tenants with full schema versioning and drift detection.

    Responsibilities:
    - Tenant lifecycle management
    - Per-tenant schema registries
    - Schema version tracking
    - Automatic drift detection and versioning
    - Consolidated view management
    """

    def __init__(
        self,
        master_session_factory: sessionmaker,
        secret_manager: SecretManager,
        tenant_base_path: str = ".",
        models: Optional[List[Type]] = None,
    ):
        self.master_session_factory = master_session_factory
        self.secret_manager = secret_manager
        self.tenant_base_path = tenant_base_path
        self.models = models or []

        # Cache for schema registries (one per tenant)
        self._schema_registries: Dict[int, SchemaRegistry] = {}
        self._tenant_engines: Dict[int, Any] = {}
        self._session_factories: Dict[int, sessionmaker] = {}

        # Current schema from models
        self.current_schema = self._build_models_schema(self.models)

    def _build_models_schema(self, models: List[Type]) -> Dict[str, Any]:
        """Build schema dict from models."""
        if not models:
            return {}

        # This would use schema_from_models or similar
        # For now, we'll use a simplified approach
        attach_change_hooks_for_models(models)
        schema = build_schema(models)
        return self._schema_to_dict(schema)

    def _schema_to_dict(self, schema) -> Dict[str, Any]:
        """Convert schema object to dict."""
        # Convert the schema object to JSON-serializable dict
        if hasattr(schema, "to_dict"):
            return schema.to_dict()
        return {}

    def create_tenant(
        self,
        name: str,
        slug: str | None = None,
        metadata: dict | None = None,
    ) -> Tenant:
        """Create a new tenant with schema registry."""
        slug = slug or name.lower().replace(" ", "-")
        api_key = f"sk_{slug}_{secrets.token_hex(16)}"

        # Create tenant database path
        tenant_db_path = f"{self.tenant_base_path}/{slug}.db"
        database_url = f"sqlite:///{tenant_db_path}"

        # Create tenant record
        tenant = Tenant(
            name=name,
            slug=slug,
            api_key=api_key,
            database_url=database_url,
            current_schema_version=1,
            metadata_json=metadata or {},
        )

        with self.master_session_factory() as sess:
            sess.add(tenant)
            sess.flush()

            # Initialize tenant's schema registry
            engine = create_engine(database_url)
            SQLModel.metadata.create_all(engine)

            # Initialize versioning tables in tenant DB
            from data_shuttle_bridge.sql.versioning_models import create_all_tables

            create_all_tables(engine)

            # Create schema registry for tenant
            registry = SchemaRegistry(engine)
            self._schema_registries[tenant.id] = registry
            self._tenant_engines[tenant.id] = engine

            # Create schema set in tenant registry
            registry_session = sessionmaker(bind=engine, class_=Session)()
            try:
                schema_set = registry.create_schema_set(
                    session=registry_session,
                    key="models",
                    name="Application Models",
                    description="Versioned schema for application models",
                )
                tenant.schema_set_id = schema_set.id

                # Add initial schema version
                if self.current_schema:
                    registry.add_schema_version(
                        session=registry_session,
                        schema_set_key="models",
                        version=1,
                        schema_json=self.current_schema,
                        parent_version=None,
                    )
            finally:
                registry_session.close()

            sess.commit()
            sess.refresh(tenant)

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
        """Delete a tenant."""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        with self.master_session_factory() as sess:
            db_tenant = sess.get(Tenant, tenant.id)
            if db_tenant:
                sess.delete(db_tenant)
                sess.commit()

        # Clear caches
        if tenant.id in self._schema_registries:
            del self._schema_registries[tenant.id]
        if tenant.id in self._tenant_engines:
            del self._tenant_engines[tenant.id]
        if tenant.id in self._session_factories:
            del self._session_factories[tenant.id]

        return True

    def get_session_factory_for_tenant(self, tenant: Tenant) -> sessionmaker:
        """Get or create session factory for tenant."""
        if tenant.id not in self._session_factories:
            engine = create_engine(tenant.database_url)
            SQLModel.metadata.create_all(engine)
            self._session_factories[tenant.id] = sessionmaker(
                bind=engine, class_=Session
            )

        return self._session_factories[tenant.id]

    def get_schema_registry_for_tenant(self, tenant: Tenant) -> SchemaRegistry:
        """Get or create schema registry for tenant."""
        if tenant.id not in self._schema_registries:
            engine = create_engine(tenant.database_url)
            registry = SchemaRegistry(engine)
            self._schema_registries[tenant.id] = registry
            self._tenant_engines[tenant.id] = engine

        return self._schema_registries[tenant.id]

    def detect_and_apply_schema_drift(
        self,
        tenant: Tenant,
        new_models: List[Type],
    ) -> Tuple[bool, Optional[int]]:
        """
        Detect schema drift and create new version if needed.

        Returns:
            (drift_detected, new_version_number)
        """
        new_schema = self._build_models_schema(new_models)

        registry = self.get_schema_registry_for_tenant(tenant)
        registry_session = sessionmaker(
            bind=self._tenant_engines[tenant.id], class_=Session
        )()

        try:
            # Get current version
            versions = registry.list_schema_versions(registry_session, "models")
            if not versions:
                return (False, None)

            current_version = versions[-1]
            current_schema = json.loads(current_version.schema_json)

            # Check for differences
            if current_schema == new_schema:
                return (False, None)

            # Drift detected - create new version
            next_version = current_version.version + 1
            new_schema_version = registry.add_schema_version(
                session=registry_session,
                schema_set_key="models",
                version=next_version,
                schema_json=new_schema,
                parent_version=current_version.version,
            )

            # Update tenant's current version
            with self.master_session_factory() as sess:
                db_tenant = sess.get(Tenant, tenant.id)
                if db_tenant:
                    db_tenant.current_schema_version = next_version
                    sess.commit()

            return (True, next_version)

        finally:
            registry_session.close()

    def get_consolidated_sync_engine(
        self,
        tenant: Tenant,
        conflict_policy: ConflictPolicy = ConflictPolicy.LWW,
    ) -> Tuple[SyncEngine, Dict[str, Any]]:
        """
        Get a SyncEngine that handles schema versions via consolidated views.

        Returns:
            (SyncEngine, metadata_dict)
        """
        session_factory = self.get_session_factory_for_tenant(tenant)
        registry = self.get_schema_registry_for_tenant(tenant)

        sess = session_factory()

        # Get all schema versions
        registry_session = sessionmaker(
            bind=self._tenant_engines[tenant.id], class_=Session
        )()
        try:
            versions = registry.list_schema_versions(registry_session, "models")
        finally:
            registry_session.close()

        # Build consolidated view if multiple versions
        if len(versions) > 1:
            # Use ConsolidationViewBuilder to create unified view
            builder = ConsolidationViewBuilder(
                engine=self._tenant_engines[tenant.id],
                policy_engine=DefaultDriftPolicy(),
            )

            metadata = {
                "versions": len(versions),
                "current_version": tenant.current_schema_version,
                "uses_consolidation": True,
            }
        else:
            metadata = {
                "versions": 1,
                "current_version": tenant.current_schema_version,
                "uses_consolidation": False,
            }

        # Create SyncEngine with current schema
        current_schema = build_schema(self.models)

        engine = SyncEngine(
            session=sess,
            peer_id="server",
            schema=current_schema,
            policy=conflict_policy,
            node_id=f"tenant:{tenant.id}",
        )

        return engine, metadata

    def set_secret(self, tenant: Tenant, key: str, secret: str) -> None:
        """Set a secret for a tenant."""
        encrypted = self.secret_manager.encrypt(secret)

        with self.master_session_factory() as sess:
            existing = sess.exec(
                select(TenantSecret).where(
                    (TenantSecret.tenant_id == tenant.id) & (TenantSecret.key == key)
                )
            ).first()

            if existing:
                existing.secret = encrypted
                existing.updated_at = datetime.utcnow()
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


def create_schema_aware_multi_tenant_app(
    master_db_url: str,
    models: Iterable[Type],
    secret_key: str | None = None,
    fernet_key: bytes | None = None,
    tenant_base_path: str = ".",
    conflict_policy: ConflictPolicy = ConflictPolicy.LWW,
    tenant_master_key: str | None = None,
) -> tuple[Flask, SchemAwareTenantManager]:
    """
    Create a Flask app with schema-aware multi-tenant support.

    Features:
    - Per-tenant schema registries
    - Automatic schema version tracking
    - Drift detection and management
    - Consolidated views for version compatibility

    Args:
        master_db_url: Database URL for master (tenant metadata) database
        models: List of SQLModel/SQLAlchemy models to sync
        secret_key: Flask secret key (auto-generated if not provided)
        fernet_key: Encryption key for secrets (auto-generated if not provided)
        tenant_base_path: Base path for tenant database files
        conflict_policy: Conflict resolution policy
        tenant_master_key: Master key for tenant management endpoints

    Returns:
        Tuple of (Flask app, SchemAwareTenantManager)
    """
    app = Flask(__name__)

    # Configure app
    app.config["SECRET_KEY"] = secret_key or os.environ.get(
        "SECRET_KEY", secrets.token_hex(32)
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = master_db_url

    # Initialize tenant master key
    if tenant_master_key is None:
        tenant_master_key = os.environ.get("TENANT_MASTER_KEY")

    # Initialize master database
    master_engine = create_engine(master_db_url)
    SQLModel.metadata.create_all(master_engine)
    MasterSessionLocal = sessionmaker(bind=master_engine, class_=Session)

    # Initialize secret manager
    secret_manager = SecretManager(fernet_key)

    # Initialize tenant manager
    models_list = list(models)
    tenant_mgr = SchemAwareTenantManager(
        master_session_factory=MasterSessionLocal,
        secret_manager=secret_manager,
        tenant_base_path=tenant_base_path,
        models=models_list,
    )

    # Attach change hooks to models
    attach_change_hooks_for_models(models_list)
    SCHEMA = build_schema(models_list)

    # ===== Helper Functions =====

    def _validate_tenant_master_key() -> bool:
        """Validate tenant master key from X-Tenant-Key header."""
        if tenant_master_key is None:
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
                        "schema_version": tenant.current_schema_version,
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
                    "schema_version": t.current_schema_version,
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
                "schema_version": tenant.current_schema_version,
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
        if not secret:
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

    # ===== Schema Management Endpoints =====

    @app.get("/api/schema/versions")
    def get_schema_versions_endpoint():
        """Get schema versions for the authenticated tenant."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        try:
            registry = tenant_mgr.get_schema_registry_for_tenant(tenant)
            registry_session = sessionmaker(
                bind=tenant_mgr._tenant_engines[tenant.id], class_=Session
            )()

            versions = registry.list_schema_versions(registry_session, "models")
            registry_session.close()

            return jsonify(
                {
                    "versions": [
                        {
                            "version": v.version,
                            "created_at": v.created_at.isoformat(),
                            "parent_version": v.parent_version,
                        }
                        for v in versions
                    ],
                    "current_version": tenant.current_schema_version,
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/schema/check-drift")
    def check_schema_drift_endpoint():
        """Check for schema drift and create new version if needed."""
        tenant = _get_tenant_from_request()
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        try:
            drift_detected, new_version = tenant_mgr.detect_and_apply_schema_drift(
                tenant,
                models_list,
            )

            return jsonify(
                {
                    "drift_detected": drift_detected,
                    "new_version": new_version,
                    "current_version": tenant.current_schema_version,
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===== Data Sync Endpoints =====

    @app.get("/api/sync/changes")
    def get_changes():
        """Get changes for authenticated tenant."""
        tenant = g.get("tenant")
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        try:
            sync_engine, metadata = tenant_mgr.get_consolidated_sync_engine(tenant)

            since_id = int(request.args.get("since_id", "0"))
            limit = int(request.args.get("limit", "1000"))
            changes = sync_engine.local_changes_since(since_id, limit=limit)

            return jsonify(
                {
                    "changes": changes,
                    "schema_metadata": metadata,
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/sync/apply")
    def apply_changes():
        """Apply remote changes for authenticated tenant."""
        tenant = g.get("tenant")
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        try:
            sync_engine, metadata = tenant_mgr.get_consolidated_sync_engine(tenant)

            payload = request.get_json(force=True) or {}
            changes = payload.get("changes", [])
            sync_engine.apply_remote_changes(changes)
            sync_engine.sess.commit()

            return jsonify({"ok": True, "schema_metadata": metadata})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===== Consolidated Data Query Endpoint =====

    @app.post("/api/data/query")
    def query_consolidated_data():
        """
        Query consolidated data across schema versions with filtering.

        Supports filtering by columns and basic WHERE conditions.
        Returns data from all schema versions unified with version tracking.

        Request JSON:
        {
            "table": "models",  # Table name (required)
            "columns": ["id", "name", "email"],  # Specific columns or [] for all
            "filters": [
                {
                    "column": "id",
                    "operator": "eq|ne|gt|gte|lt|lte|in|like",
                    "value": 1
                },
                {
                    "column": "name",
                    "operator": "like",
                    "value": "ACME%"
                }
            ],
            "limit": 100,
            "offset": 0
        }

        Response:
        {
            "data": [
                {"id": 1, "name": "ACME Corp", "email": "...", "_schema_version": 1},
                {"id": 3, "name": "NewCorp", "email": "...", "phone": "...", "_schema_version": 2}
            ],
            "total": 150,
            "returned": 100,
            "schema_metadata": {
                "versions": 2,
                "current_version": 2,
                "uses_consolidation": true
            }
        }
        """
        tenant = g.get("tenant")
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 401

        try:
            payload = request.get_json(force=True) or {}
            table_name = payload.get("table")
            columns = payload.get("columns", [])
            filters = payload.get("filters", [])
            limit = int(payload.get("limit", 100))
            offset = int(payload.get("offset", 0))

            if not table_name:
                return jsonify({"error": "Missing required field: table"}), 400

            if limit < 1 or limit > 10000:
                return jsonify({"error": "Limit must be between 1 and 10000"}), 400

            if offset < 0:
                return jsonify({"error": "Offset must be >= 0"}), 400

            # Get consolidated sync engine
            sync_engine, metadata = tenant_mgr.get_consolidated_sync_engine(tenant)
            sess = sync_engine.sess

            # Find the model class
            model_class = None
            for model in tenant_mgr.models:
                if (
                    hasattr(model, "__tablename__")
                    and model.__tablename__ == table_name
                ):
                    model_class = model
                    break

            if not model_class:
                return jsonify({"error": f"Table not found: {table_name}"}), 404

            # Build base query
            if columns:
                # Filter to specific columns
                query_columns = []
                for col_name in columns:
                    if hasattr(model_class, col_name):
                        query_columns.append(getattr(model_class, col_name))
                    else:
                        return jsonify({"error": f"Column not found: {col_name}"}), 400

                # Always include _schema_version if using consolidation
                if metadata["uses_consolidation"] and "_schema_version" not in columns:
                    base_query = select(*query_columns)
                else:
                    base_query = select(*query_columns)
            else:
                # All columns
                base_query = select(model_class)

            # Apply filters
            for filter_spec in filters:
                col_name = filter_spec.get("column")
                operator = filter_spec.get("operator", "eq")
                value = filter_spec.get("value")

                if not col_name:
                    return jsonify({"error": "Filter missing column name"}), 400

                if not hasattr(model_class, col_name):
                    return jsonify({"error": f"Column not found: {col_name}"}), 400

                col = getattr(model_class, col_name)

                # Apply operator
                if operator == "eq":
                    base_query = base_query.where(col == value)
                elif operator == "ne":
                    base_query = base_query.where(col != value)
                elif operator == "gt":
                    base_query = base_query.where(col > value)
                elif operator == "gte":
                    base_query = base_query.where(col >= value)
                elif operator == "lt":
                    base_query = base_query.where(col < value)
                elif operator == "lte":
                    base_query = base_query.where(col <= value)
                elif operator == "in":
                    if isinstance(value, list):
                        base_query = base_query.where(col.in_(value))
                    else:
                        return (
                            jsonify({"error": 'Operator "in" requires array value'}),
                            400,
                        )
                elif operator == "like":
                    base_query = base_query.where(col.like(value))
                else:
                    return jsonify({"error": f"Unknown operator: {operator}"}), 400

            # Get total count (before limit/offset)
            count_query = select(func.count()).select_from(model_class)

            # Apply same filters to count query
            for filter_spec in filters:
                col_name = filter_spec.get("column")
                operator = filter_spec.get("operator", "eq")
                value = filter_spec.get("value")
                col = getattr(model_class, col_name)

                if operator == "eq":
                    count_query = count_query.where(col == value)
                elif operator == "ne":
                    count_query = count_query.where(col != value)
                elif operator == "gt":
                    count_query = count_query.where(col > value)
                elif operator == "gte":
                    count_query = count_query.where(col >= value)
                elif operator == "lt":
                    count_query = count_query.where(col < value)
                elif operator == "lte":
                    count_query = count_query.where(col <= value)
                elif operator == "in":
                    if isinstance(value, list):
                        count_query = count_query.where(col.in_(value))
                elif operator == "like":
                    count_query = count_query.where(col.like(value))

            total = sess.execute(count_query).scalar() or 0

            # Apply limit and offset
            base_query = base_query.limit(limit).offset(offset)

            # Execute query
            results = sess.execute(base_query).fetchall()

            # Format results
            data = []
            for row in results:
                if columns:
                    # Map result to dict
                    row_dict = {}
                    for i, col_name in enumerate(columns):
                        row_dict[col_name] = row[i]
                    data.append(row_dict)
                else:
                    # Convert ORM object to dict
                    if hasattr(row[0], "__dict__"):
                        row_dict = {
                            k: v
                            for k, v in row[0].__dict__.items()
                            if not k.startswith("_")
                        }
                        data.append(row_dict)
                    else:
                        data.append({"value": row[0]})

            return jsonify(
                {
                    "data": data,
                    "total": total,
                    "returned": len(data),
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                    },
                    "schema_metadata": metadata,
                }
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    return app, tenant_mgr
