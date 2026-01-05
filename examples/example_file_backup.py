"""
Example usage of the fsspec-restic-lite backup tool.

This script demonstrates the basic workflow: init, backup, list, and restore.
You must run this from the project root directory:
  python example_file_backup.py
"""

import tempfile
import os
import sys

# Ensure data_shuttle_bridge is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from data_shuttle_bridge.file_backup.runtime import (
    init_repo,
    run_backup,
    list_snapshots,
    run_restore,
)


def main():
    """Demonstrate backup workflow."""

    # Use in-memory filesystem for this example (suitable for testing/demo)
    repo_url = "memory://example_repo"

    # Create some temporary files to back up
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source directory with files
        source_dir = os.path.join(tmpdir, "source")
        os.makedirs(source_dir)

        # Create some test files
        with open(os.path.join(source_dir, "doc1.txt"), "w") as f:
            f.write("Important document 1\n" * 100)

        with open(os.path.join(source_dir, "doc2.txt"), "w") as f:
            f.write("Important document 2\n" * 50)

        # Create a subdirectory with more files
        subdir = os.path.join(source_dir, "subdir")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "nested.txt"), "w") as f:
            f.write("Nested document\n" * 75)

        print("=" * 60)
        print("File BACKUP EXAMPLE")
        print("=" * 60)
        print(f"\nSource directory: {source_dir}")
        print(f"Repository URL: {repo_url}\n")

        # Step 1: Initialize repository
        print("[1] Initializing repository...")
        init_repo(repo_url)
        print("    [OK] Repository initialized\n")

        # Step 2: Create a backup
        print("[2] Creating first backup...")
        snapshot_id_1 = run_backup(repo_url, [source_dir])
        print()

        # Modify a file and back up again
        print("[3] Modifying files and creating second backup...")
        with open(os.path.join(source_dir, "doc1.txt"), "a") as f:
            f.write("Additional content added later\n" * 10)
        snapshot_id_2 = run_backup(repo_url, [source_dir])
        print()

        # Step 4: List snapshots
        print("[4] Listing all snapshots...")
        list_snapshots(repo_url)
        print()

        # Step 5: Restore to a different location
        restore_dir = os.path.join(tmpdir, "restore")
        print(f"[5] Restoring latest snapshot to {restore_dir}...")
        run_restore(repo_url, restore_dir)
        print()

        # Verify restored files
        print("[6] Verifying restored files...")
        restored_doc1 = os.path.join(restore_dir, "doc1.txt")
        with open(restored_doc1, "r") as f:
            content = f.read()
            lines = content.count("\n")
            print(f"    [OK] doc1.txt: {lines} lines")

        restored_nested = os.path.join(restore_dir, "subdir", "nested.txt")
        with open(restored_nested, "r") as f:
            content = f.read()
            print(f"    [OK] subdir/nested.txt: restored successfully")

        print("\n" + "=" * 60)
        print("Example complete! Check README.md for more details.")
        print("=" * 60)


if __name__ == "__main__":
    main()
