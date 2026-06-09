from __future__ import annotations

from typing import Iterable, List

from data_shuttle_bridge.sql.typing_ import ChangePayload


class PeerTransport:
    def get_changes_since(
        self, since_id: int, limit: int = 1000, exclude_node_id: str | None = None
    ) -> List[ChangePayload]:
        raise NotImplementedError

    def apply_changes(self, changes: Iterable[ChangePayload]) -> None:
        raise NotImplementedError

    def ack(self, last_seen_change_id: int) -> None:
        pass


class InMemoryPeerTransport(PeerTransport):
    def __init__(self, changes: list[ChangePayload] | None = None):
        self._changes = changes or []

    def get_changes_since(
        self, since_id: int, limit: int = 1000, exclude_node_id: str | None = None
    ) -> list[ChangePayload]:
        return [c for c in self._changes if c["id"] > since_id][:limit]

    def apply_changes(self, changes: Iterable[ChangePayload]) -> None:
        self._changes.extend(list(changes))


class HttpPeerTransport(PeerTransport):
    def __init__(self, base_url: str, session=None):
        import requests

        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def get_changes_since(
        self,
        since_id: int,
        limit: int = 1000,
        exclude_node_id: str | None = None,
    ):
        params = {"since_id": since_id, "limit": limit}
        if exclude_node_id:
            params["exclude_node_id"] = exclude_node_id
        r = self._session.get(
            f"{self.base_url}/sync/changes",
            params=params,
        )
        r.raise_for_status()
        return r.json()["changes"]

    def apply_changes(self, changes):
        r = self._session.post(
            f"{self.base_url}/sync/apply", json={"changes": list(changes)}
        )
        r.raise_for_status()

    def ack(self, last_seen_change_id: int) -> None:
        self._session.post(
            f"{self.base_url}/sync/ack", json={"last_seen": last_seen_change_id}
        )
