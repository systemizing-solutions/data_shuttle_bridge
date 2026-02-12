"""Core schema registry runtime service for managing schema lifecycle."""

import json
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import MetaData, Table, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from jsonschema import Draft202012Validator, ValidationError

from data_shuttle_bridge.sql.versioning_models import (
    SchemaSet,
    SchemaVersion,
    SchemaDiff,
    MappingRule,
    ConsolidationView,
)
from data_shuttle_bridge.sql.jsonschema_types import (
    build_version_table,
    get_columns_from_schema,
)
from data_shuttle_bridge.sql.diffing import (
    DiffEngine,
    DefaultDiffEngine,
    classify_drift,
)


class SchemaRegistry:
    """
    Core runtime service for schema management.

    Responsibilities:
    - Create and manage schema sets
    - Add schema versions
    - Validate schemas and payloads
    - Provision physical tables
    - Compute and store diffs
    """

    def __init__(
        self,
        engine: Engine,
        diff_engine: Optional[DiffEngine] = None,
    ):
        """
        Initialize registry.

        Args:
            engine: SQLAlchemy Engine
            diff_engine: DiffEngine instance (defaults to DefaultDiffEngine)
        """
        self.engine = engine
        self.diff_engine = diff_engine or DefaultDiffEngine()
        self.metadata = MetaData()

    def create_schema_set(
        self,
        session: Session,
        key: str,
        name: str,
        description: Optional[str] = None,
    ) -> SchemaSet:
        """
        Create a new schema set.

        Args:
            session: SQLAlchemy Session
            key: Unique key identifier (e.g., 'customer')
            name: Human-readable name
            description: Optional description

        Returns:
            Created SchemaSet instance

        Raises:
            ValueError: If key already exists
        """
        # Check if key already exists
        existing = session.exec(select(SchemaSet).where(SchemaSet.key == key)).first()

        if existing:
            raise ValueError(f"Schema set with key '{key}' already exists")

        schema_set = SchemaSet(
            key=key,
            name=name,
            description=description,
        )
        session.add(schema_set)
        session.commit()
        session.refresh(schema_set)

        return schema_set

    def add_schema_version(
        self,
        session: Session,
        schema_set_key: str,
        version: int,
        schema_json: Dict[str, Any],
        parent_version: Optional[int] = None,
    ) -> SchemaVersion:
        """
        Add a new schema version to a schema set.

        Workflow:
        1. Validate schema document
        2. Create SchemaVersion record
        3. Compute diff vs parent
        4. Provision physical table
        5. Store diff

        Args:
            session: SQLAlchemy Session
            schema_set_key: Key of schema set
            version: Version number
            schema_json: JSON Schema document (dict or JSON string)
            parent_version: Optional parent version number

        Returns:
            Created SchemaVersion instance

        Raises:
            ValueError: If schema is invalid or version already exists
        """
        # Load schema set
        schema_set = session.exec(
            select(SchemaSet).where(SchemaSet.key == schema_set_key)
        ).first()

        if not schema_set:
            raise ValueError(f"Schema set '{schema_set_key}' not found")

        # Parse schema if JSON string
        if isinstance(schema_json, str):
            try:
                schema_json = json.loads(schema_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON Schema: {e}")

        # Validate schema
        self._validate_schema(schema_json)

        # Check if version already exists
        existing_version = session.exec(
            select(SchemaVersion).where(
                (SchemaVersion.schema_set_id == schema_set.id)
                & (SchemaVersion.version == version)
            )
        ).first()

        if existing_version:
            raise ValueError(
                f"Version {version} already exists for schema set '{schema_set_key}'"
            )

        # Get parent version info
        parent_version_id = None
        parent_schema = None

        if parent_version is not None:
            parent_sv = session.exec(
                select(SchemaVersion).where(
                    (SchemaVersion.schema_set_id == schema_set.id)
                    & (SchemaVersion.version == parent_version)
                )
            ).first()

            if not parent_sv:
                raise ValueError(
                    f"Parent version {parent_version} not found in schema set"
                )

            parent_version_id = parent_sv.id
            parent_schema = json.loads(parent_sv.schema_json)

        # Create table name
        table_name = f"{schema_set_key}__v{version}"

        # Build and create physical table
        sa_table = build_version_table(
            self.metadata,
            table_name=table_name,
            schema=schema_json,
        )

        # Create table in database
        sa_table.create(self.engine, checkfirst=True)

        # Create SchemaVersion record
        schema_version = SchemaVersion(
            schema_set_id=schema_set.id,
            version=version,
            parent_version_id=parent_version_id,
            schema_json=json.dumps(schema_json),
            table_name=table_name,
        )
        session.add(schema_version)
        session.flush()

        # Compute and store diff
        if parent_version_id is not None and parent_schema is not None:
            diff_records = self.diff_engine.diff(parent_schema, schema_json)
            diff_dict = {
                "records": [d.to_dict() for d in diff_records],
                "classification": classify_drift(diff_records),
            }

            schema_diff = SchemaDiff(
                from_version_id=parent_version_id,
                to_version_id=schema_version.id,
                diff_json=json.dumps(diff_dict),
            )
            session.add(schema_diff)

        session.commit()
        session.refresh(schema_version)

        return schema_version

    def validate_payload(
        self,
        session: Session,
        schema_set_key: str,
        version: int,
        payload: Dict[str, Any],
    ) -> bool:
        """
        Validate a payload against a specific schema version.

        Args:
            session: SQLAlchemy Session
            schema_set_key: Schema set key
            version: Version number
            payload: Data payload (dict)

        Returns:
            True if valid

        Raises:
            ValidationError: If payload is invalid
        """
        schema_version = self._get_schema_version(session, schema_set_key, version)

        schema_json = json.loads(schema_version.schema_json)
        Draft202012Validator(schema_json).validate(payload)

        return True

    def ingest_data(
        self,
        session: Session,
        schema_set_key: str,
        version: int,
        payload: Dict[str, Any],
    ) -> int:
        """
        Ingest a validated payload into a version table.

        Args:
            session: SQLAlchemy Session
            schema_set_key: Schema set key
            version: Version number
            payload: Data payload (dict)

        Returns:
            Inserted row ID

        Raises:
            ValidationError: If payload is invalid
        """
        # Validate payload
        self.validate_payload(session, schema_set_key, version, payload)

        # Get version table
        schema_version = self._get_schema_version(session, schema_set_key, version)

        # Load table metadata
        metadata = MetaData()
        table = Table(schema_version.table_name, metadata, autoload_with=self.engine)

        # Add metadata columns
        from datetime import datetime

        payload_with_meta = {
            **payload,
            "_created_at": datetime.utcnow(),
            "_updated_at": datetime.utcnow(),
        }

        # Insert
        stmt = insert(table).values(**payload_with_meta)
        result = session.execute(stmt)
        session.commit()

        return result.lastrowid

    def get_schema_diff(
        self,
        session: Session,
        schema_set_key: str,
        from_version: int,
        to_version: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get computed diff between two versions.

        Args:
            session: SQLAlchemy Session
            schema_set_key: Schema set key
            from_version: Source version
            to_version: Target version

        Returns:
            Diff dict or None if not computed yet
        """
        # Get schema set
        schema_set = session.exec(
            select(SchemaSet).where(SchemaSet.key == schema_set_key)
        ).first()

        if not schema_set:
            raise ValueError(f"Schema set '{schema_set_key}' not found")

        # Get versions
        from_sv = session.exec(
            select(SchemaVersion).where(
                (SchemaVersion.schema_set_id == schema_set.id)
                & (SchemaVersion.version == from_version)
            )
        ).first()

        to_sv = session.exec(
            select(SchemaVersion).where(
                (SchemaVersion.schema_set_id == schema_set.id)
                & (SchemaVersion.version == to_version)
            )
        ).first()

        if not from_sv or not to_sv:
            raise ValueError("Version not found")

        # Get diff record
        diff = session.exec(
            select(SchemaDiff).where(
                (SchemaDiff.from_version_id == from_sv.id)
                & (SchemaDiff.to_version_id == to_sv.id)
            )
        ).first()

        if not diff:
            return None

        return json.loads(diff.diff_json)

    def list_schema_sets(self, session: Session) -> List[SchemaSet]:
        """List all schema sets."""
        return session.exec(select(SchemaSet)).all()

    def list_schema_versions(
        self, session: Session, schema_set_key: str
    ) -> List[SchemaVersion]:
        """List all versions for a schema set."""
        schema_set = session.exec(
            select(SchemaSet).where(SchemaSet.key == schema_set_key)
        ).first()

        if not schema_set:
            raise ValueError(f"Schema set '{schema_set_key}' not found")

        return session.exec(
            select(SchemaVersion)
            .where(SchemaVersion.schema_set_id == schema_set.id)
            .order_by(SchemaVersion.version)
        ).all()

    # Private helpers

    def _validate_schema(self, schema: Dict[str, Any]) -> None:
        """
        Validate a JSON Schema document.

        Raises:
            ValueError: If schema is invalid
        """
        if not isinstance(schema, dict):
            raise ValueError("Schema must be a dict")

        if schema.get("type") != "object":
            raise ValueError("Root schema must be of type 'object'")

        if "properties" not in schema:
            raise ValueError("Schema must have 'properties'")

        # Draft validation
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as e:
            raise ValueError(f"Invalid JSON Schema: {e}")

    def _get_schema_version(
        self, session: Session, schema_set_key: str, version: int
    ) -> SchemaVersion:
        """Get a schema version or raise error."""
        schema_set = session.exec(
            select(SchemaSet).where(SchemaSet.key == schema_set_key)
        ).first()

        if not schema_set:
            raise ValueError(f"Schema set '{schema_set_key}' not found")

        schema_version = session.exec(
            select(SchemaVersion).where(
                (SchemaVersion.schema_set_id == schema_set.id)
                & (SchemaVersion.version == version)
            )
        ).first()

        if not schema_version:
            raise ValueError(
                f"Version {version} not found in schema set '{schema_set_key}'"
            )

        return schema_version
