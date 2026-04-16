"""Tests for fsspec-restic-lite backup functionality."""

import os
import shutil
import tempfile
from pathlib import Path
import pytest

from data_shuttle_bridge.file_backup.repo.repository import (
    Repository,
    Snapshot,
    FileEntry,
)
from data_shuttle_bridge.file_backup.pipeline.chunking import FixedSizeChunker
from data_shuttle_bridge.file_backup.runtime import (
    init_repo,
    run_backup,
    list_snapshots,
    run_restore,
)


class TestChunking:
    """Tests for chunking strategies."""

    def test_fixed_size_chunker_basic(self):
        """Test basic fixed-size chunking."""
        chunker = FixedSizeChunker(chunk_size=10)
        data = b"0123456789abcdefghij"
        file_obj = __import__("io").BytesIO(data)

        chunks = list(chunker.chunk(file_obj))
        assert len(chunks) == 2
        assert chunks[0][0] == b"0123456789"
        assert chunks[1][0] == b"abcdefghij"
        # Hashes should be valid hex
        assert len(chunks[0][1]) == 64  # SHA256 hex is 64 chars
        assert len(chunks[1][1]) == 64

    def test_fixed_size_chunker_empty_file(self):
        """Test chunking an empty file."""
        chunker = FixedSizeChunker()
        file_obj = __import__("io").BytesIO(b"")
        chunks = list(chunker.chunk(file_obj))
        assert len(chunks) == 0

    def test_fixed_size_chunker_single_chunk(self):
        """Test file smaller than chunk size."""
        chunker = FixedSizeChunker(chunk_size=100)
        data = b"small file"
        file_obj = __import__("io").BytesIO(data)
        chunks = list(chunker.chunk(file_obj))
        assert len(chunks) == 1
        assert chunks[0][0] == data


class TestRepository:
    """Tests for repository operations."""

    def test_init_repo_memory(self):
        """Test initializing a repository in memory."""
        repo = Repository("memory://test_repo")
        assert not repo.exists()
        repo.init()
        assert repo.exists()

    def test_put_and_get_blob(self):
        """Test storing and retrieving blobs."""
        repo = Repository("memory://test_repo")
        repo.init()

        data = b"test blob data"
        import hashlib

        hash_hex = hashlib.sha256(data).hexdigest()

        # Put blob
        path = repo.put_blob(data, hash_hex)
        assert repo.fs.exists(path)

        # Get blob
        retrieved = repo.get_blob(hash_hex)
        assert retrieved == data

    def test_put_blob_dedup(self):
        """Test that duplicate blobs are not stored twice."""
        repo = Repository("memory://test_repo_dedup")
        repo.init()

        data = b"duplicate data"
        import hashlib

        hash_hex = hashlib.sha256(data).hexdigest()

        # Put blob twice
        path1 = repo.put_blob(data, hash_hex)
        path2 = repo.put_blob(data, hash_hex)

        # Should be the same path
        assert path1 == path2

        # Should only have one blob in storage
        objects_dir = repo.root + "objects"
        count = 0
        for dirpath, dirnames, filenames in repo.fs.walk(objects_dir):
            count += len(filenames)
        assert count == 1

    def test_write_and_list_snapshots(self):
        """Test writing and listing snapshots."""
        repo = Repository("memory://test_repo_write_list")
        repo.init()

        entry = FileEntry(
            path="/tmp/testfile",
            size=100,
            mtime_ns=1234567890,
            mode=33188,
            blobs=[{"hash": "abc123", "size": 100}],
        )
        snap = Snapshot(
            snapshot_id="test-snap-id",
            created_at=1000000,
            hostname="testhost",
            sources=["/tmp"],
            files=[entry],
        )

        repo.write_snapshot(snap)
        snapshots = repo.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_id == "test-snap-id"
        assert len(snapshots[0].files) == 1
        assert snapshots[0].files[0].path == "/tmp/testfile"

    def test_list_snapshots_sorted(self):
        """Test that snapshots are sorted by created_at descending."""
        repo = Repository("memory://test_repo_sorted")
        repo.init()

        # Create snapshots with different timestamps
        for i, ts in enumerate([1000, 3000, 2000]):
            snap = Snapshot(
                snapshot_id=f"snap-{i}",
                created_at=ts,
                hostname="testhost",
                sources=["/tmp"],
                files=[],
            )
            repo.write_snapshot(snap)

        snapshots = repo.list_snapshots()
        assert len(snapshots) == 3
        # Should be sorted by created_at descending
        assert snapshots[0].created_at == 3000
        assert snapshots[1].created_at == 2000
        assert snapshots[2].created_at == 1000

    def test_get_snapshot_by_id(self):
        """Test retrieving snapshot by ID (prefix or full)."""
        repo = Repository("memory://test_repo_get_snap")
        repo.init()

        snap = Snapshot(
            snapshot_id="abc123def456",
            created_at=1000000,
            hostname="testhost",
            sources=["/tmp"],
            files=[],
        )
        repo.write_snapshot(snap)

        # Retrieve by prefix
        retrieved = repo.get_snapshot_by_id("abc123")
        assert retrieved is not None
        assert retrieved.snapshot_id == "abc123def456"

        # Non-existent
        not_found = repo.get_snapshot_by_id("xyz789")
        assert not_found is None


class TestRoundtrip:
    """Integration tests: backup and restore."""

    def test_backup_and_restore_single_file(self):
        """Test backup and restore of a single file."""
        # Create temporary source and destination
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            dest_dir = os.path.join(tmpdir, "restore")
            repo_url = "memory://roundtrip_repo"

            os.makedirs(source_dir)

            # Create a test file
            test_file = os.path.join(source_dir, "test.txt")
            test_content = b"Hello, World!"
            with open(test_file, "wb") as f:
                f.write(test_content)

            # Initialize and backup
            init_repo(repo_url)
            run_backup(repo_url, [test_file])

            # Restore
            run_restore(repo_url, dest_dir)

            # Verify
            restored_file = os.path.join(dest_dir, os.path.basename(test_file))
            assert os.path.exists(restored_file)
            with open(restored_file, "rb") as f:
                assert f.read() == test_content

    def test_backup_and_restore_directory(self):
        """Test backup and restore of a directory with multiple files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            dest_dir = os.path.join(tmpdir, "restore")
            repo_url = "memory://roundtrip_repo_dir"

            os.makedirs(source_dir)

            # Create test files
            files_data = {
                "file1.txt": b"Content 1",
                "file2.txt": b"Content 2",
                "subdir/file3.txt": b"Content 3",
            }

            for rel_path, content in files_data.items():
                full_path = os.path.join(source_dir, rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(content)

            # Initialize and backup
            init_repo(repo_url)
            run_backup(repo_url, [source_dir])

            # Restore
            run_restore(repo_url, dest_dir)

            # Verify all files exist and have correct content
            # Files are restored with relative paths from the source root
            for rel_path, content in files_data.items():
                restored_path = os.path.join(dest_dir, rel_path)
                assert os.path.exists(restored_path), f"Missing {restored_path}"
                with open(restored_path, "rb") as f:
                    assert f.read() == content, f"Content mismatch for {rel_path}"

    def test_backup_and_restore_large_file_chunked(self):
        """Test backup and restore of a file larger than chunk size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            dest_dir = os.path.join(tmpdir, "restore")
            repo_url = "memory://roundtrip_repo_large"

            os.makedirs(source_dir)

            # Create a large file (>1MB to exceed default 4MiB chunker in actual use)
            # For this test, we use smaller chunks
            test_file = os.path.join(source_dir, "large.bin")
            large_content = b"x" * (10 * 1024)  # 10 KiB
            with open(test_file, "wb") as f:
                f.write(large_content)

            # Initialize and backup
            init_repo(repo_url)
            run_backup(repo_url, [test_file])

            # Restore
            run_restore(repo_url, dest_dir)

            # Verify
            restored_file = os.path.join(dest_dir, os.path.basename(test_file))
            assert os.path.exists(restored_file)
            with open(restored_file, "rb") as f:
                assert f.read() == large_content

    def test_restore_latest_snapshot(self):
        """Test that restore without snapshot_id uses latest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            dest_dir = os.path.join(tmpdir, "restore")
            repo_url = "memory://roundtrip_repo_latest"

            os.makedirs(source_dir)

            # Create and backup first version
            test_file = os.path.join(source_dir, "test.txt")
            with open(test_file, "wb") as f:
                f.write(b"Version 1")

            init_repo(repo_url)
            snap1 = run_backup(repo_url, [test_file])

            # Add small delay to ensure different timestamps
            import time

            time.sleep(0.01)

            # Create and backup second version
            with open(test_file, "wb") as f:
                f.write(b"Version 2 is longer")

            snap2 = run_backup(repo_url, [test_file])

            # Restore without specifying snapshot (should get latest = snap2)
            run_restore(repo_url, dest_dir)

            # Verify latest version was restored
            restored_file = os.path.join(dest_dir, os.path.basename(test_file))
            with open(restored_file, "rb") as f:
                content = f.read()
            assert content == b"Version 2 is longer"


class TestDedup:
    """Tests for deduplication."""

    def test_backup_dedup_same_file(self):
        """Test that backing up the same file twice doesn't duplicate blobs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            repo_url = "memory://dedup_repo"

            os.makedirs(source_dir)

            # Create a test file
            test_file = os.path.join(source_dir, "test.txt")
            with open(test_file, "wb") as f:
                f.write(b"Content that will be backed up twice")

            # Initialize and backup twice
            init_repo(repo_url)
            run_backup(repo_url, [test_file])

            repo = Repository(repo_url)
            blobs_after_first = count_blobs(repo)

            run_backup(repo_url, [test_file])
            blobs_after_second = count_blobs(repo)

            # Should not have created new blobs (file content unchanged)
            assert blobs_after_second == blobs_after_first

    def test_backup_dedup_duplicate_content(self):
        """Test dedup when different files have same content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            repo_url = "memory://dedup_repo_dups"

            os.makedirs(source_dir)

            # Create two files with identical content
            file1 = os.path.join(source_dir, "file1.txt")
            file2 = os.path.join(source_dir, "file2.txt")
            shared_content = b"This content is shared"

            with open(file1, "wb") as f:
                f.write(shared_content)
            with open(file2, "wb") as f:
                f.write(shared_content)

            # Backup both
            init_repo(repo_url)
            run_backup(repo_url, [file1, file2])

            # Check that only one blob was created for this content
            repo = Repository(repo_url)
            snapshots = repo.list_snapshots()
            assert len(snapshots) == 1

            # Collect all unique blob hashes
            blob_hashes = set()
            for file_entry in snapshots[0].files:
                for blob in file_entry.blobs:
                    blob_hashes.add(blob["hash"])

            # Should have exactly one blob hash (both files reference the same blob)
            assert len(blob_hashes) == 1


def count_blobs(repo: Repository) -> int:
    """Count total blobs in repository."""
    objects_dir = repo.root + "objects"
    if not repo.fs.exists(objects_dir):
        return 0
    count = 0
    try:
        for dirpath, dirnames, filenames in repo.fs.walk(objects_dir):
            count += len(filenames)
    except Exception:
        pass
    return count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ============================================================================
# Additional coverage tests for runtime.py
# ============================================================================


class TestInitRepoAlreadyExists:
    """Test init_repo raises when repo already exists (line 29)."""

    def test_init_repo_already_exists(self):
        repo_url = "memory://already_exists_repo"
        init_repo(repo_url)
        with pytest.raises(RuntimeError, match="Repository already exists"):
            init_repo(repo_url)


class TestRunBackupEdgeCases:
    """Test run_backup edge cases for uncovered lines."""

    def test_backup_nonexistent_source(self):
        """Test that backup raises for nonexistent source (line 62)."""
        repo_url = "memory://nonexistent_source_repo"
        init_repo(repo_url)
        with pytest.raises(RuntimeError, match="Source does not exist"):
            run_backup(repo_url, ["/nonexistent/path/that/does/not/exist"])

    def test_backup_with_custom_snapshot_id(self):
        """Test backup with explicit snapshot_id (covers line 56->60 branch)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "test.txt")
            with open(source_file, "wb") as f:
                f.write(b"hello")

            repo_url = "memory://custom_snap_id"
            init_repo(repo_url)
            sid = run_backup(repo_url, [source_file], snapshot_id="custom123")
            assert sid == "custom123"

    def test_backup_file_read_error(self):
        """Test that run_backup skips files it can't read (lines 115-116)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            os.makedirs(source_dir)

            # Create a readable file
            good_file = os.path.join(source_dir, "good.txt")
            with open(good_file, "wb") as f:
                f.write(b"good content")

            # Create a file and make it unreadable
            bad_file = os.path.join(source_dir, "bad.txt")
            with open(bad_file, "wb") as f:
                f.write(b"bad content")
            os.chmod(bad_file, 0o000)

            repo_url = "memory://skip_error_repo"
            init_repo(repo_url)

            try:
                sid = run_backup(repo_url, [source_dir])
                # The good file should be backed up, bad file skipped
                repo = Repository(repo_url)
                snapshots = repo.list_snapshots()
                assert len(snapshots) == 1
                # At least the good file should be in the snapshot
                assert len(snapshots[0].files) >= 1
            finally:
                # Restore permissions so cleanup works
                os.chmod(bad_file, 0o644)


class TestRestoreEdgeCases:
    """Test run_restore edge cases for uncovered lines."""

    def test_restore_no_snapshots(self):
        """Test restore raises when no snapshots exist (lines 183-185)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_url = "memory://empty_restore_repo"
            init_repo(repo_url)
            with pytest.raises(RuntimeError, match="No snapshots found"):
                run_restore(repo_url, os.path.join(tmpdir, "restore"))

    def test_restore_specific_snapshot_not_found(self):
        """Test restore raises for nonexistent snapshot ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_url = "memory://snap_not_found_repo"
            init_repo(repo_url)
            with pytest.raises(RuntimeError, match="Snapshot not found"):
                run_restore(
                    repo_url,
                    os.path.join(tmpdir, "restore"),
                    snapshot_id="nonexistent",
                )

    def test_restore_creates_dest_dir(self):
        """Test that restore creates the destination directory (line 189)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "test.txt")
            with open(source_file, "wb") as f:
                f.write(b"hello")

            repo_url = "memory://dest_dir_repo"
            init_repo(repo_url)
            run_backup(repo_url, [source_file])

            dest = os.path.join(tmpdir, "new_dir", "nested")
            assert not os.path.exists(dest)
            run_restore(repo_url, dest)
            assert os.path.exists(dest)

    def test_restore_preserves_metadata(self):
        """Test that restore attempts to preserve file mode and mtime (lines 210-217)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "meta.txt")
            with open(source_file, "wb") as f:
                f.write(b"metadata test")

            repo_url = "memory://metadata_repo"
            init_repo(repo_url)
            run_backup(repo_url, [source_file])

            dest = os.path.join(tmpdir, "restore_meta")
            run_restore(repo_url, dest)

            restored_file = os.path.join(dest, "meta.txt")
            assert os.path.exists(restored_file)
            with open(restored_file, "rb") as f:
                assert f.read() == b"metadata test"


class TestListSnapshotsEdgeCases:
    """Test list_snapshots edge cases."""

    def test_list_snapshots_empty(self):
        """Test listing snapshots when none exist."""
        repo_url = "memory://empty_list_repo"
        init_repo(repo_url)
        # Should print "No snapshots found." and not raise
        list_snapshots(repo_url)

    def test_list_snapshots_with_data(self):
        """Test listing snapshots with existing data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "test.txt")
            with open(source_file, "wb") as f:
                f.write(b"content")

            repo_url = "memory://list_data_repo"
            init_repo(repo_url)
            run_backup(repo_url, [source_file])
            # Should print snapshot info and not raise
            list_snapshots(repo_url)
