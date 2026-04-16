"""WireGuard-backed PeerTransport — direct P2P sync over an encrypted tunnel."""

from __future__ import annotations

from typing import Iterable, List, Optional

from data_shuttle_bridge.p2p.nat import remove_upnp_forward
from data_shuttle_bridge.p2p.wireguard import (
    tunnel_up,
    tunnel_down,
    wait_for_peer,
)
from data_shuttle_bridge.sql.transport import HttpPeerTransport, PeerTransport
from data_shuttle_bridge.sql.typing_ import ChangePayload


class WireGuardPeerTransport(PeerTransport):
    """PeerTransport that syncs over a WireGuard tunnel.

    Wraps :class:`HttpPeerTransport`, pointing it at the peer's virtual IP.
    Optionally manages the tunnel lifecycle (bring up on enter, down on exit).
    """

    def __init__(
        self,
        peer_virtual_ip: str,
        sync_port: int = 5000,
        manage_tunnel: bool = False,
        wg_config_path: Optional[str] = None,
        cleanup_upnp: bool = True,
        health_check_timeout: int = 30,
    ):
        base_url = f"http://{peer_virtual_ip}:{sync_port}"
        self._http = HttpPeerTransport(base_url)
        self._peer_ip = peer_virtual_ip
        self._manage_tunnel = manage_tunnel
        self._wg_config_path = wg_config_path
        self._cleanup_upnp = cleanup_upnp
        self._health_timeout = health_check_timeout
        self._tunnel_active = False

    # -- PeerTransport interface -------------------------------------------

    def get_changes_since(
        self,
        since_id: int,
        limit: int = 1000,
        exclude_node_id: Optional[str] = None,
    ) -> List[ChangePayload]:
        return self._http.get_changes_since(
            since_id, limit=limit, exclude_node_id=exclude_node_id
        )

    def apply_changes(self, changes: Iterable[ChangePayload]) -> None:
        self._http.apply_changes(changes)

    def ack(self, last_seen_change_id: int) -> None:
        self._http.ack(last_seen_change_id)

    # -- Tunnel management -------------------------------------------------

    def bring_up(self) -> None:
        """Bring the WireGuard tunnel up and wait for the peer."""
        if not self._wg_config_path:
            raise RuntimeError("wg_config_path required to manage tunnel")
        tunnel_up(self._wg_config_path)
        self._tunnel_active = True
        if not wait_for_peer(self._peer_ip, timeout=self._health_timeout):
            raise RuntimeError(
                f"Peer {self._peer_ip} not reachable after {self._health_timeout}s"
            )

    def bring_down(self) -> None:
        """Bring the WireGuard tunnel down and clean up."""
        if self._wg_config_path and self._tunnel_active:
            tunnel_down(self._wg_config_path)
            self._tunnel_active = False
        if self._cleanup_upnp:
            remove_upnp_forward()

    # -- Context manager ---------------------------------------------------

    def __enter__(self) -> "WireGuardPeerTransport":
        if self._manage_tunnel:
            self.bring_up()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._manage_tunnel:
            self.bring_down()

    # -- Async context manager (for future use) ----------------------------

    async def __aenter__(self) -> "WireGuardPeerTransport":
        if self._manage_tunnel:
            self.bring_up()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._manage_tunnel:
            self.bring_down()
