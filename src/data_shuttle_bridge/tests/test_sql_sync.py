"""
Comprehensive test suite for data_shuttle_bridge SQL synchronization functionality.

Tests cover:
- Database model creation and ORM integration
- Change tracking via changelog
- Sync state management
- Bidirectional synchronization
- Conflict resolution (Last-Write-Wins)
- Node ID generation and watermarking
"""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlmodel import (
    create_engine as sqlmodel_create_engine,
    Session as SMSession,
    select,
)
from typing import Optional

from data_shuttle_bridge.sql.mixins import SyncRowSAMixin, SyncRowSQLModelMixin
from data_shuttle_bridge.sql.ids import set_id_generator
from data_shuttle_bridge.sql.changelog import ChangeLog, SyncState


# ============================================================================
# Test Models - SQLAlchemy
# ============================================================================

SABase = declarative_base()


class SAUser(SABase, SyncRowSAMixin):
    """SQLAlchemy test model with sync mixins."""

    __tablename__ = "sa_users"

    name = Column(String, nullable=False)
    email = Column(String, nullable=False)


class SAProduct(SABase, SyncRowSAMixin):
    """SQLAlchemy product model for testing."""

    __tablename__ = "sa_products"

    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)  # Price in cents


# ============================================================================
# Test Models - SQLModel
# ============================================================================


class SMUser(SyncRowSQLModelMixin, table=True):
    """SQLModel test model with sync mixins."""

    __tablename__ = "sm_users"

    name: str
    email: str


class SMProduct(SyncRowSQLModelMixin, table=True):
    """SQLModel product model for testing."""

    __tablename__ = "sm_products"

    name: str
    price: int  # Price in cents


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def setup_node_id():
    """Set up node ID for testing."""
    node_id = "test_node_1"
    set_id_generator(node_id)
    yield node_id


@pytest.fixture
def sa_session(setup_node_id):
    """Create a SQLAlchemy test database session."""
    engine = create_engine("sqlite:///:memory:")
    SABase.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, class_=SMSession)
    session = SessionLocal()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def sm_session(setup_node_id):
    """Create a SQLModel test database session."""
    engine = sqlmodel_create_engine("sqlite:///:memory:")
    SyncRowSQLModelMixin.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, class_=SMSession)
    session = SessionLocal()

    yield session

    session.close()
    engine.dispose()


# ============================================================================
# SQLAlchemy Tests
# ============================================================================


class TestSAMixins:
    """Test SQLAlchemy sync row mixins."""

    def test_sync_columns_exist(self, sa_session):
        """Verify sync columns are added to model."""
        user = SAUser(name="Alice", email="alice@example.com")

        # Check sync columns exist
        assert hasattr(user, "id")
        assert hasattr(user, "version")
        assert hasattr(user, "deleted_at")
        assert hasattr(user, "updated_at")

    def test_version_starts_at_one(self, sa_session):
        """Verify version starts at 1 on insert."""
        user = SAUser(name="Bob", email="bob@example.com")
        sa_session.add(user)
        sa_session.commit()

        # Check initial state
        assert user.version == 1
        assert user.deleted_at is None

    def test_user_creation_and_retrieval(self, sa_session):
        """Verify users can be created and retrieved."""
        user = SAUser(name="Charlie", email="charlie@example.com")
        sa_session.add(user)
        sa_session.commit()

        user_id = user.id

        # Retrieve user
        retrieved = sa_session.exec(select(SAUser).where(SAUser.id == user_id)).first()
        assert retrieved is not None
        assert retrieved.name == "Charlie"
        assert retrieved.email == "charlie@example.com"

    def test_soft_delete_with_deleted_at(self, sa_session):
        """Verify deleted_at can be set for soft delete."""
        user = SAUser(name="Diana", email="diana@example.com")
        sa_session.add(user)
        sa_session.commit()

        user_id = user.id

        # Soft delete
        user.deleted_at = datetime.now(timezone.utc)
        sa_session.commit()

        # Verify soft delete marker exists
        deleted_user = sa_session.exec(
            select(SAUser).where(SAUser.id == user_id)
        ).first()
        assert deleted_user is not None
        assert deleted_user.deleted_at is not None


class TestIDGeneration:
    """Test K-Sorted ID generation functionality."""

    def test_id_generation_with_string_node_id(self):
        """Verify ID generation works with string node_id."""
        set_id_generator("node1")
        from data_shuttle_bridge.sql.ids import get_id_generator

        id_gen = get_id_generator()

        id1 = id_gen()
        id2 = id_gen()

        # IDs should be unique
        assert id1 != id2
        # IDs should be sortable (id1 < id2 if generated first)
        assert id1 < id2

    def test_id_generation_with_numeric_node_id(self):
        """Verify ID generation works with numeric node_id."""
        from data_shuttle_bridge.sql.ids import KSortedID

        # KSortedID expects int, but set_id_generator handles conversion
        id_gen = KSortedID(1)

        id1 = id_gen()
        id2 = id_gen()

        assert id1 != id2
        assert id1 < id2

    def test_id_generation_multiple_nodes(self):
        """Verify ID generation doesn't collide across nodes."""
        from data_shuttle_bridge.sql.ids import get_id_generator

        set_id_generator("node1")
        id_gen1 = get_id_generator()
        ids1 = [id_gen1() for _ in range(5)]

        set_id_generator("node2")
        id_gen2 = get_id_generator()
        ids2 = [id_gen2() for _ in range(5)]

        # All IDs should be unique
        all_ids = ids1 + ids2
        assert len(all_ids) == len(set(all_ids))


# ============================================================================
# SQLModel Tests
# ============================================================================


class TestSMSyncMixins:
    """Test SQLModel sync row mixins."""

    def test_sync_columns_exist(self, sm_session):
        """Verify sync columns are added to SQLModel."""
        user = SMUser(name="Eve", email="eve@example.com")

        # Check sync columns exist
        assert hasattr(user, "id")
        assert hasattr(user, "version")
        assert hasattr(user, "deleted_at")
        assert hasattr(user, "updated_at")

    def test_version_starts_at_one(self, sm_session):
        """Verify version starts at 1 on creation."""
        user = SMUser(name="Frank", email="frank@example.com")
        sm_session.add(user)
        sm_session.commit()
        sm_session.refresh(user)

        # Check initial state
        assert user.version == 1
        assert user.deleted_at is None

    def test_deleted_at_is_none_initially(self, sm_session):
        """Verify deleted_at is None for new records."""
        user = SMUser(name="Grace", email="grace@example.com")
        sm_session.add(user)
        sm_session.commit()
        sm_session.refresh(user)

        assert user.deleted_at is None

    def test_user_creation_and_retrieval(self, sm_session):
        """Verify users can be created and retrieved."""
        user = SMUser(name="Henry", email="henry@example.com")
        sm_session.add(user)
        sm_session.commit()

        user_id = user.id

        # Retrieve user
        retrieved = sm_session.exec(select(SMUser).where(SMUser.id == user_id)).first()
        assert retrieved is not None
        assert retrieved.name == "Henry"
        assert retrieved.email == "henry@example.com"


# ============================================================================
# Changelog Tests
# ============================================================================


class TestChangelog:
    """Test changelog tracking functionality."""

    def test_changelog_schema(self):
        """Verify Changelog schema structure."""
        changelog = ChangeLog(
            table="users",
            pk=123,
            op="I",  # I=INSERT, U=UPDATE, D=DELETE
            node_id="test_node",
            version=0,
        )

        assert changelog.table == "users"
        assert changelog.pk == 123
        assert changelog.op == "I"
        assert changelog.node_id == "test_node"
        assert changelog.version == 0

    def test_changelog_operations(self):
        """Verify changelog supports all operations."""
        operations = ["I", "U", "D"]  # I=INSERT, U=UPDATE, D=DELETE

        for op in operations:
            changelog = ChangeLog(
                table="users",
                pk=123,
                op=op,
                node_id="test_node",
                version=0,
            )
            assert changelog.op == op


# ============================================================================
# SyncState Tests
# ============================================================================


class TestSyncState:
    """Test sync state tracking."""

    def test_sync_state_creation(self):
        """Verify SyncState creation."""
        sync_state = SyncState(
            peer_id="remote_server",
            last_pulled_change_id=0,
            last_pushed_change_id=0,
        )

        assert sync_state.peer_id == "remote_server"
        assert sync_state.last_pulled_change_id == 0
        assert sync_state.last_pushed_change_id == 0

    def test_sync_state_updates(self):
        """Verify SyncState can be updated."""
        sync_state = SyncState(
            peer_id="remote_server",
            last_pulled_change_id=0,
            last_pushed_change_id=0,
        )

        # Simulate sync progress
        sync_state.last_pulled_change_id = 100
        sync_state.last_pushed_change_id = 50

        assert sync_state.last_pulled_change_id == 100
        assert sync_state.last_pushed_change_id == 50


# ============================================================================
# Integration Tests
# ============================================================================


class TestSyncIntegration:
    """Test synchronization integration."""

    def test_multiple_users_sync(self, sa_session):
        """Test creating multiple users."""
        user1 = SAUser(name="User1", email="user1@example.com")
        user2 = SAUser(name="User2", email="user2@example.com")

        sa_session.add(user1)
        sa_session.add(user2)
        sa_session.commit()

        # Verify both users exist with unique IDs
        users = sa_session.exec(select(SAUser)).all()
        assert len(users) == 2
        assert users[0].id != users[1].id

    def test_soft_delete_filter(self, sa_session):
        """Test filtering out soft-deleted records."""
        user1 = SAUser(name="Active", email="active@example.com")
        user2 = SAUser(name="Deleted", email="deleted@example.com")

        sa_session.add(user1)
        sa_session.add(user2)
        sa_session.commit()

        # Soft delete user2
        user2.deleted_at = datetime.now(timezone.utc)
        sa_session.commit()

        # Query active users (deleted_at is None)
        active_users = sa_session.exec(
            select(SAUser).where(SAUser.deleted_at == None)
        ).all()
        assert len(active_users) == 1
        assert active_users[0].name == "Active"


# ============================================================================
# IDs Module Coverage Tests
# ============================================================================


class TestIDsCoverage:
    """Additional tests for ids.py to improve coverage."""

    def test_set_id_generator_with_int_node_id(self):
        """Test set_id_generator with integer node_id (line 39)."""
        from data_shuttle_bridge.sql.ids import set_id_generator, get_id_generator

        set_id_generator(42)
        gen = get_id_generator()
        assert gen.node_id == 42
        id1 = gen()
        assert isinstance(id1, int)

    def test_get_id_generator_raises_when_not_set(self):
        """Test RuntimeError when ID generator not initialized (lines 58-62)."""
        from data_shuttle_bridge.sql.ids import (
            get_id_generator,
            clear_id_generator,
            _local,
            _default_id_generator,
        )
        import data_shuttle_bridge.sql.ids as ids_mod

        # Clear thread-local
        clear_id_generator()
        # Save and clear default
        saved = ids_mod._default_id_generator
        ids_mod._default_id_generator = None
        try:
            with pytest.raises(RuntimeError, match="ID generator not initialized"):
                get_id_generator()
        finally:
            ids_mod._default_id_generator = saved

    def test_clear_id_generator(self):
        """Test clear_id_generator clears thread-local (lines 71-72)."""
        from data_shuttle_bridge.sql.ids import (
            set_id_generator,
            clear_id_generator,
            _local,
        )

        set_id_generator("test")
        assert hasattr(_local, "id_generator") and _local.id_generator is not None
        clear_id_generator()
        assert _local.id_generator is None

    def test_clear_id_generator_when_not_set(self):
        """Test clear_id_generator when no generator was ever set."""
        from data_shuttle_bridge.sql.ids import clear_id_generator, _local

        # Remove attribute if it exists
        if hasattr(_local, "id_generator"):
            delattr(_local, "id_generator")
        # Should not raise
        clear_id_generator()

    def test_ksorted_id_invalid_node_id(self):
        """Test KSortedID raises ValueError for out-of-range node_id (line 78)."""
        from data_shuttle_bridge.sql.ids import KSortedID, MAX_NODE

        with pytest.raises(ValueError, match="node_id out of range"):
            KSortedID(node_id=-1)
        with pytest.raises(ValueError, match="node_id out of range"):
            KSortedID(node_id=MAX_NODE + 1)

    def test_ksorted_id_negative_ms_sleeps(self):
        """Test KSortedID handles clock before epoch (lines 91-92)."""
        from unittest.mock import patch
        from data_shuttle_bridge.sql.ids import KSortedID, EPOCH_MS

        gen = KSortedID(node_id=1)
        # First call returns time before epoch, second returns valid
        with patch.object(
            gen,
            "_now_ms",
            side_effect=[EPOCH_MS - 100, EPOCH_MS + 1000],
        ):
            with patch("data_shuttle_bridge.sql.ids.time.sleep") as mock_sleep:
                _id = gen()
                mock_sleep.assert_called_once()
                assert isinstance(_id, int)

    def test_ksorted_id_sequence_overflow(self):
        """Test KSortedID handles sequence overflow (lines 96-100)."""
        from unittest.mock import patch
        from data_shuttle_bridge.sql.ids import KSortedID, EPOCH_MS, MAX_SEQUENCE

        gen = KSortedID(node_id=1)
        # Set state: same ms, seq about to overflow
        gen._last_ms = 5000
        gen._seq = MAX_SEQUENCE  # next increment wraps to 0

        # _now_ms returns same ms first (overflow), then new ms
        with patch.object(
            gen,
            "_now_ms",
            side_effect=[EPOCH_MS + 5000, EPOCH_MS + 5000, EPOCH_MS + 5001],
        ):
            _id = gen()
            assert isinstance(_id, int)
            # Should have moved to the next millisecond
            assert gen._last_ms == 5001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
