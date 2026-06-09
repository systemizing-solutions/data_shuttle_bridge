"""WireGuard tunnel strategy.

Manages a WireGuard tunnel via ``wg-quick`` and exposes the peer's
virtual-IP as the sync URL.
"""

from __future__ import annotations

import shutil
from typing import Optional

from data_shuttle_bridge.p2p.nat import remove_upnp_forward
from data_shuttle_bridge.p2p.tunnel import TunnelStrategy, register_tunnel_strategy
from data_shuttle_bridge.p2p.wireguard import (
    tunnel_up,
    tunnel_down,
    wait_for_peer,
)

_INSTALL_HINTS = {
    "darwin": ["brew", "install", "wireguard-tools"],
    "linux": ["sudo", "apt-get", "install", "-y", "wireguard"],
    "windows": None,  # no silent installer — direct to download page
}

_DOWNLOAD_URL = "https://www.wireguard.com/install/"


@register_tunnel_strategy
class WireGuardTunnelStrategy(TunnelStrategy):
    """Tunnel strategy backed by WireGuard (``wg-quick``).

    Because the peer's virtual IP and sync port are known ahead of time,
    :attr:`url` is available before :meth:`start` is called.  This allows
    :class:`~data_shuttle_bridge.p2p.tunnel.TunnelPeerTransport` to work
    even when ``manage_tunnel=False`` (the tunnel was brought up externally
    via the CLI).
    """

    name = "wireguard"
    signup_url = _DOWNLOAD_URL

    def __init__(
        self,
        peer_virtual_ip: str,
        sync_port: int = 5000,
        wg_config_path: Optional[str] = None,
        cleanup_upnp: bool = True,
        health_check_timeout: int = 30,
    ) -> None:
        self._peer_ip = peer_virtual_ip
        self._sync_port = sync_port
        self._wg_config_path = wg_config_path
        self._cleanup_upnp = cleanup_upnp
        self._health_timeout = health_check_timeout
        self._active = False

    # -- TunnelStrategy interface ------------------------------------------

    @property
    def url(self) -> str:
        """Always known — the peer's virtual IP on the WireGuard network."""
        return f"http://{self._peer_ip}:{self._sync_port}"

    def is_installed(self) -> bool:
        return shutil.which("wg") is not None

    def install(self) -> None:
        import platform as _platform

        plat = _platform.system().lower()
        if plat == "darwin":
            cmd = _INSTALL_HINTS["darwin"]
        elif plat in ("linux", "linux2"):
            cmd = _INSTALL_HINTS["linux"]
        else:
            raise RuntimeError(
                f"Auto-install not supported on {plat}. "
                f"Download WireGuard from: {_DOWNLOAD_URL}"
            )
        assert cmd is not None
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        SubprocessTunnelStrategy._run_install_cmd(cmd, "wireguard-tools")

    def start(self) -> str:
        self.ensure_ready()
        if not self._wg_config_path:
            raise RuntimeError("wg_config_path required to manage tunnel")
        tunnel_up(self._wg_config_path)
        self._active = True
        if not wait_for_peer(self._peer_ip, timeout=self._health_timeout):
            raise RuntimeError(
                f"Peer {self._peer_ip} not reachable after {self._health_timeout}s"
            )
        return self.url

    def stop(self) -> None:
        if self._wg_config_path and self._active:
            tunnel_down(self._wg_config_path)
            self._active = False
        if self._cleanup_upnp:
            remove_upnp_forward()

    @property
    def is_active(self) -> bool:
        return self._active
