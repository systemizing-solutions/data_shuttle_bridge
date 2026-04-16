"""File backup data models."""

from dataclasses import dataclass
from typing import Any


@dataclass
class FileEntry:
    """Represents a file in a snapshot."""

    path: str
    size: int
    mtime_ns: int
    mode: int
    blobs: list[dict[str, Any]]  # Each blob: {hash, size}


@dataclass
class Snapshot:
    """Represents a snapshot manifest."""

    snapshot_id: str
    created_at: float
    hostname: str
    sources: list[str]
    files: list[FileEntry]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "hostname": self.hostname,
            "sources": self.sources,
            "files": [
                {
                    "path": f.path,
                    "size": f.size,
                    "mtime_ns": f.mtime_ns,
                    "mode": f.mode,
                    "blobs": f.blobs,
                }
                for f in self.files
            ],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Snapshot":
        """Create from dictionary."""
        files = [
            FileEntry(
                path=f["path"],
                size=f["size"],
                mtime_ns=f["mtime_ns"],
                mode=f["mode"],
                blobs=f["blobs"],
            )
            for f in data["files"]
        ]
        return Snapshot(
            snapshot_id=data["snapshot_id"],
            created_at=data["created_at"],
            hostname=data["hostname"],
            sources=data["sources"],
            files=files,
        )
