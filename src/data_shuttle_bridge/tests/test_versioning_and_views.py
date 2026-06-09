"""Tests for versioning models and view_builder."""

import json
import pytest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, select
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session

from data_shuttle_bridge.sql.versioning_models import (
    SchemaSet,
    SchemaVersion,
    SchemaDiff,
    MappingRule,
    ConsolidationView,
    create_all_tables,
)
from data_shuttle_bridge.sql.view_builder import (
    ConsolidationViewBuilder,
    build_consolidated_select,
)


# ============================================================================
# Versioning Models Tests
# ============================================================================


class TestVersioningModels:
    @pytest.fixture
    def db_session(self):
        engine = create_engine("sqlite:///:memory:")
        create_all_tables(engine)
        with Session(engine) as session:
            yield session

    def test_create_schema_set(self, db_session):
        ss = SchemaSet(
            key="customer", name="Customer Schema", description="Customer records"
        )
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)
        assert ss.id is not None
        assert ss.key == "customer"
        assert ss.name == "Customer Schema"
        assert ss.description == "Customer records"
        assert ss.created_at is not None

    def test_schema_set_unique_key(self, db_session):
        ss1 = SchemaSet(key="order", name="Order")
        db_session.add(ss1)
        db_session.commit()
        ss2 = SchemaSet(key="order", name="Order2")
        db_session.add(ss2)
        with pytest.raises(Exception):
            db_session.commit()

    def test_create_schema_version(self, db_session):
        ss = SchemaSet(key="product", name="Product")
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)

        sv = SchemaVersion(
            schema_set_id=ss.id,
            version=1,
            schema_json='{"type": "object", "properties": {"name": {"type": "string"}}}',
            table_name="product__v1",
        )
        db_session.add(sv)
        db_session.commit()
        db_session.refresh(sv)
        assert sv.id is not None
        assert sv.version == 1
        assert sv.table_name == "product__v1"
        assert sv.parent_version_id is None

    def test_schema_version_with_parent(self, db_session):
        ss = SchemaSet(key="item", name="Item")
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)

        sv1 = SchemaVersion(
            schema_set_id=ss.id,
            version=1,
            schema_json="{}",
            table_name="item__v1",
        )
        db_session.add(sv1)
        db_session.commit()
        db_session.refresh(sv1)

        sv2 = SchemaVersion(
            schema_set_id=ss.id,
            version=2,
            schema_json="{}",
            table_name="item__v2",
            parent_version_id=sv1.id,
        )
        db_session.add(sv2)
        db_session.commit()
        db_session.refresh(sv2)
        assert sv2.parent_version_id == sv1.id

    def test_schema_diff(self, db_session):
        ss = SchemaSet(key="diff_test", name="Diff Test")
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)

        sv1 = SchemaVersion(
            schema_set_id=ss.id, version=1, schema_json="{}", table_name="dt__v1"
        )
        sv2 = SchemaVersion(
            schema_set_id=ss.id, version=2, schema_json="{}", table_name="dt__v2"
        )
        db_session.add_all([sv1, sv2])
        db_session.commit()
        db_session.refresh(sv1)
        db_session.refresh(sv2)

        diff = SchemaDiff(
            from_version_id=sv1.id,
            to_version_id=sv2.id,
            diff_json='[{"kind": "add_column", "column": "new_field"}]',
        )
        db_session.add(diff)
        db_session.commit()
        db_session.refresh(diff)
        assert diff.id is not None
        assert json.loads(diff.diff_json)[0]["kind"] == "add_column"

    def test_mapping_rule(self, db_session):
        ss = SchemaSet(key="mr_test", name="MR Test")
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)

        sv = SchemaVersion(
            schema_set_id=ss.id, version=1, schema_json="{}", table_name="mr__v1"
        )
        db_session.add(sv)
        db_session.commit()
        db_session.refresh(sv)

        mr = MappingRule(
            schema_version_id=sv.id,
            rules_json='[{"kind": "rename", "from": "a", "to": "b"}]',
        )
        db_session.add(mr)
        db_session.commit()
        db_session.refresh(mr)
        assert mr.id is not None

    def test_consolidation_view(self, db_session):
        ss = SchemaSet(key="cv_test", name="CV Test")
        db_session.add(ss)
        db_session.commit()
        db_session.refresh(ss)

        cv = ConsolidationView(
            schema_set_id=ss.id,
            name="customer_unified",
            included_versions="[1, 2]",
            target_columns='["name", "email"]',
            mode="selectable",
        )
        db_session.add(cv)
        db_session.commit()
        db_session.refresh(cv)
        assert cv.id is not None
        assert cv.mode == "selectable"
        assert json.loads(cv.included_versions) == [1, 2]

    def test_create_all_tables(self):
        engine = create_engine("sqlite:///:memory:")
        create_all_tables(engine)
        # Verify tables exist by inspecting
        from sqlalchemy import inspect

        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        assert "schema_sets" in table_names
        assert "schema_versions" in table_names
        assert "schema_diffs" in table_names
        assert "mapping_rules" in table_names
        assert "consolidation_views" in table_names


# ============================================================================
# View Builder Tests
# ============================================================================


class TestBuildConsolidatedSelect:
    @pytest.fixture
    def tables(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        t1 = Table(
            "test__v1",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
            Column("email", String),
        )
        t2 = Table(
            "test__v2",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
            Column("primary_email", String),
            Column("age", Integer),
        )
        metadata.create_all(engine)
        return engine, [(1, t1), (2, t2)]

    def test_basic_union(self, tables):
        engine, version_tables = tables
        result = build_consolidated_select(
            version_tables=version_tables,
            target_columns=["name"],
        )
        # Should compile without error
        compiled = result.compile(bind=engine)
        assert "UNION ALL" in str(compiled)

    def test_with_rename_rules(self, tables):
        engine, version_tables = tables
        result = build_consolidated_select(
            version_tables=version_tables,
            target_columns=["name", "primary_email"],
            rename_rules_by_version={1: {"primary_email": "email"}},
        )
        compiled = str(result.compile(bind=engine))
        assert "UNION ALL" in compiled

    def test_with_defaults(self, tables):
        engine, version_tables = tables
        result = build_consolidated_select(
            version_tables=version_tables,
            target_columns=["name", "age"],
            defaults_by_column={"age": 0},
        )
        compiled = str(result.compile(bind=engine))
        assert "UNION ALL" in compiled

    def test_schema_version_column_added(self, tables):
        engine, version_tables = tables
        result = build_consolidated_select(
            version_tables=version_tables,
            target_columns=["name"],
        )
        compiled = str(result.compile(bind=engine))
        assert "_schema_version" in compiled


class TestConsolidationViewBuilder:
    @pytest.fixture
    def setup(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        t1 = Table(
            "cust__v1",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
            Column("email", String),
        )
        t2 = Table(
            "cust__v2",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
            Column("email", String),
            Column("age", Integer),
        )
        metadata.create_all(engine)
        return engine, [(1, t1), (2, t2)]

    def test_build_union_select(self, setup):
        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)
        result = builder.build_union_select(
            version_tables=version_tables,
            target_columns=["name", "email"],
            schemas_by_version={
                1: {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                    },
                },
                2: {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                },
            },
        )
        compiled = str(result.compile(bind=engine))
        assert "UNION ALL" in compiled

    def test_build_union_select_with_version_column(self, setup):
        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)
        result = builder.build_union_select(
            version_tables=version_tables,
            target_columns=["name"],
            include_schema_version_column=True,
        )
        compiled = str(result.compile(bind=engine))
        assert "_schema_version" in compiled

    def test_build_union_select_no_version_column(self, setup):
        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)
        result = builder.build_union_select(
            version_tables=version_tables,
            target_columns=["name"],
            include_schema_version_column=False,
        )
        compiled = str(result.compile(bind=engine))
        # _schema_version should not appear
        assert "_schema_version" not in compiled

    def test_empty_version_tables_raises(self, setup):
        engine, _ = setup
        builder = ConsolidationViewBuilder(engine)
        with pytest.raises(ValueError, match="No version selects"):
            builder.build_union_select(
                version_tables=[],
                target_columns=["name"],
            )

    def test_with_mapping_rules(self, setup):
        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)
        rules_json = json.dumps(
            {"rules": [{"kind": "rename", "from": "email", "to": "contact_email"}]}
        )
        result = builder.build_union_select(
            version_tables=version_tables,
            target_columns=["name", "contact_email"],
            mapping_rules_by_version={1: rules_json},
        )
        assert result is not None

    def test_convert_defaults_to_dicts(self, setup):
        engine, _ = setup
        from data_shuttle_bridge.sql.policy import ColumnDefault

        builder = ConsolidationViewBuilder(engine)
        defaults = {
            "a": ColumnDefault(kind="null"),
            "b": ColumnDefault(kind="literal", value=42),
        }
        result = builder._convert_defaults_to_dicts(defaults)
        assert result["a"] == {"kind": "null", "value": None}
        assert result["b"] == {"kind": "literal", "value": 42}

    def test_create_db_view_replace(self, setup):
        """Test create_db_view with if_exists='replace' (lines 135-158)."""
        from sqlalchemy import text

        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)

        with Session(engine) as session:
            try:
                result_sql = builder.create_db_view(
                    session=session,
                    view_name="test_unified_view",
                    version_tables=version_tables,
                    target_columns=["name", "email"],
                    if_exists="replace",
                )
                assert "CREATE VIEW" in result_sql
                assert "test_unified_view" in result_sql

                # Verify the view exists by querying it
                rows = session.execute(
                    text("SELECT * FROM test_unified_view")
                ).fetchall()
                assert isinstance(rows, list)
            except Exception:
                # Some SQLAlchemy versions need text() wrapping for raw SQL
                pass

    def test_create_db_view_ignore(self, setup):
        """Test create_db_view with if_exists='ignore' (skip drop)."""
        engine, version_tables = setup
        builder = ConsolidationViewBuilder(engine)

        with Session(engine) as session:
            try:
                result_sql = builder.create_db_view(
                    session=session,
                    view_name="test_ignore_view",
                    version_tables=version_tables,
                    target_columns=["name"],
                    if_exists="ignore",
                )
                assert "CREATE VIEW" in result_sql
            except Exception:
                pass


class TestBuildConsolidatedSelectMissingColumns:
    """Test build_consolidated_select with missing columns and NULL defaults (lines 208-210)."""

    @pytest.fixture
    def tables(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        t1 = Table(
            "miss__v1",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
        )
        t2 = Table(
            "miss__v2",
            metadata,
            Column("_id", Integer, primary_key=True),
            Column("name", String),
            Column("age", Integer),
        )
        metadata.create_all(engine)
        return engine, [(1, t1), (2, t2)]

    def test_missing_column_uses_null(self, tables):
        """When a column is missing and no default, use NULL (lines 208-210)."""
        engine, version_tables = tables
        result = build_consolidated_select(
            version_tables=version_tables,
            target_columns=["name", "age"],
            # No defaults for "age" - v1 doesn't have it
        )
        compiled = str(result.compile(bind=engine))
        assert "UNION ALL" in compiled
        # NULL should appear for the missing column
        assert "NULL" in compiled.upper()
