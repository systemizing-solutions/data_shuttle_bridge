"""Repository management using fsspec."""

import json
import os
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone
import uuid

import fsspec
from fsspec.spec import AbstractFileSystem

from data_shuttle_bridge.models.file_backup import FileEntry, Snapshot
from data_shuttle_bridge.file_backup.pipeline.chunking import (
    ChunkingStrategy,
    FixedSizeChunker,
)


class Repository:
    """Manages a backup repository using fsspec."""

    def __init__(
        self,
        url: str,
        chunker: Optional[ChunkingStrategy] = None,
    ):
        """
        Initialize a repository.

        Args:
            url: fsspec URL (e.g., 'file:///path', 's3://bucket/prefix', 'memory://repo').
            chunker: ChunkingStrategy instance. Defaults to FixedSizeChunker.
        """
        self.url = url
        self.fs, self.root = fsspec.core.url_to_fs(url)
        self.chunker = chunker or FixedSizeChunker()

        # Normalize root path for the filesystem
        if not self.root.endswith("/"):
            self.root = self.root + "/"

    def _ensure_initialized(self) -> None:
        """Check if repository is initialized; raise if not."""
        config_path = self.root + "config.json"
        if not self.fs.exists(config_path):
            raise RuntimeError(
                f"Repository not initialized at {self.url}. Run init first."
            )

    def init(self) -> None:
        """Initialize a new repository."""
        # Create directories
        for subdir in ["objects", "snapshots", "indexes"]:
            self.fs.makedirs(self.root + subdir, exist_ok=True)

        # Write config
        config = {
            "version": "1",
            "chunk_size": (
                self.chunker.chunk_size
                if hasattr(self.chunker, "chunk_size")
                else 4194304
            ),
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }
        config_path = self.root + "config.json"
        with self.fs.open(config_path, "w") as f:
            json.dump(config, f, indent=2)

    def exists(self) -> bool:
        """Check if repository exists and is initialized."""
        return self.fs.exists(self.root + "config.json")

    def put_blob(self, data: bytes, hash_hex: str) -> str:
        """
        Store a blob by hash.

        Args:
            data: Raw bytes to store.
            hash_hex: SHA256 hash (hex string).

        Returns:
            Path to the stored blob.
        """
        # Use first two chars as subdirectories
        obj_path = self.root + f"objects/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}"

        # Skip if already exists
        if self.fs.exists(obj_path):
            return obj_path

        # Ensure parent directory exists
        self.fs.makedirs(
            self.root + f"objects/{hash_hex[:2]}/{hash_hex[2:4]}", exist_ok=True
        )

        # Write blob
        with self.fs.open(obj_path, "wb") as f:
            f.write(data)

        return obj_path

    def get_blob(self, hash_hex: str) -> bytes:
        """
        Retrieve a blob by hash.

        Args:
            hash_hex: SHA256 hash (hex string).

        Returns:
            Raw bytes of the blob.

        Raises:
            FileNotFoundError: If blob does not exist.
        """
        obj_path = self.root + f"objects/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}"
        if not self.fs.exists(obj_path):
            raise FileNotFoundError(f"Blob {hash_hex} not found")
        with self.fs.open(obj_path, "rb") as f:
            return f.read()

    def write_snapshot(self, snapshot: Snapshot) -> str:
        """
        Write a snapshot manifest.

        Args:
            snapshot: Snapshot instance.

        Returns:
            Path to the written snapshot.
        """
        self._ensure_initialized()

        # Generate filename from timestamp and ID
        timestamp = int(snapshot.created_at)
        snapshot_id = snapshot.snapshot_id[:8]
        filename = f"{timestamp}_{snapshot_id}.json"
        snap_path = self.root + f"snapshots/{filename}"

        # Write manifest
        with self.fs.open(snap_path, "w") as f:
            json.dump(snapshot.to_dict(), f, indent=2)

        return snap_path

    def list_snapshots(self) -> list[Snapshot]:
        """
        List all snapshots, sorted by created_at descending.

        Returns:
            List of Snapshot instances.
        """
        self._ensure_initialized()

        snapshots = []
        snap_dir = self.root + "snapshots"

        if not self.fs.exists(snap_dir):
            return []

        for item in self.fs.listdir(snap_dir, detail=False):
            # item may be full path, extract just filename
            filename = item.split("/")[-1] if "/" in item else item
            if filename.endswith(".json"):
                snap_path = snap_dir + "/" + filename
                try:
                    with self.fs.open(snap_path, "r") as f:
                        data = json.load(f)
                    snapshots.append(Snapshot.from_dict(data))
                except Exception as e:
                    print(f"Warning: Could not load snapshot {snap_path}: {e}")

        # Sort by created_at descending
        snapshots.sort(key=lambda s: s.created_at, reverse=True)
        return snapshots

    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[Snapshot]:
        """
        Retrieve a specific snapshot by ID.

        Args:
            snapshot_id: Snapshot ID (full or prefix).

        Returns:
            Snapshot instance or None if not found.
        """
        snapshots = self.list_snapshots()
        for snap in snapshots:
            if snap.snapshot_id.startswith(snapshot_id):
                return snap
        return None

    def write_index(self, snapshot_id: str, index: dict[str, list[str]]) -> str:
        """
        Write a file->blob index for a snapshot.

        Args:
            snapshot_id: Snapshot ID.
            index: Dict mapping file paths to blob hashes.

        Returns:
            Path to the written index.
        """
        self._ensure_initialized()
        idx_path = self.root + f"indexes/{snapshot_id}.json"
        self.fs.makedirs(self.root + "indexes", exist_ok=True)
        with self.fs.open(idx_path, "w") as f:
            json.dump(index, f, indent=2)
        return idx_path
