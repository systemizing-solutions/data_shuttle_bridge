"""WireGuard keypair generation, config rendering, and tunnel lifecycle."""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)

from data_shuttle_bridge.models.p2p import WireGuardIdentity, WireGuardPeerConfig

DEFAULT_WG_CONFIG_DIR = os.path.join(
    os.path.expanduser("~"), ".localfirst_sync", "wireguard"
)


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def _generate_keypair_wg_cli() -> Tuple[str, str]:
    """Generate a keypair using the ``wg`` CLI tool."""
    private = subprocess.check_output(["wg", "genkey"], stderr=subprocess.DEVNULL)
    private_b64 = private.strip()
    public = subprocess.check_output(
        ["wg", "pubkey"], input=private_b64, stderr=subprocess.DEVNULL
    )
    return private_b64.decode(), public.strip().decode()


def _generate_keypair_cryptography() -> Tuple[str, str]:
    """Generate a keypair using the ``cryptography`` library (Curve25519)."""
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes_raw()
    pub_bytes = priv.public_key().public_bytes_raw()
    return (
        base64.b64encode(priv_bytes).decode(),
        base64.b64encode(pub_bytes).decode(),
    )


def generate_keypair() -> Tuple[str, str]:
    """Return ``(private_key_b64, public_key_b64)``.

    Tries the ``wg`` CLI first; falls back to the ``cryptography`` library.
    """
    if shutil.which("wg"):
        return _generate_keypair_wg_cli()
    return _generate_keypair_cryptography()


def load_or_create_keypair(
    config_dir: str = DEFAULT_WG_CONFIG_DIR,
) -> WireGuardIdentity:
    """Load an existing identity or create a new one."""
    identity_path = os.path.join(config_dir, "identity.json")
    if os.path.exists(identity_path):
        return WireGuardIdentity.load(identity_path)
    priv, pub = generate_keypair()
    identity = WireGuardIdentity(private_key=priv, public_key=pub)
    identity.save(identity_path)
    return identity


# ---------------------------------------------------------------------------
# Config rendering
# ---------------------------------------------------------------------------


def generate_wg_config(
    local: WireGuardPeerConfig,
    remote: WireGuardPeerConfig,
) -> str:
    """Render a ``wg-quick`` compatible INI config string."""
    lines = [
        "[Interface]",
        f"PrivateKey = {local.private_key}",
        f"Address = {local.virtual_ip}/24",
        f"ListenPort = {local.listen_port}",
    ]
    if local.dns:
        lines.append(f"DNS = {local.dns}")
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {remote.public_key}")
    if local.preshared_key:
        lines.append(f"PresharedKey = {local.preshared_key}")
    lines.append(f"AllowedIPs = {remote.allowed_ips}")
    if remote.endpoint:
        lines.append(f"Endpoint = {remote.endpoint}")
    if local.persistent_keepalive:
        lines.append(f"PersistentKeepalive = {local.persistent_keepalive}")
    lines.append("")
    return "\n".join(lines)


def write_wg_config(
    local: WireGuardPeerConfig,
    remote: WireGuardPeerConfig,
    config_dir: str = DEFAULT_WG_CONFIG_DIR,
    interface_name: str = "wg_shuttle",
) -> str:
    """Write config to disk and return the path."""
    content = generate_wg_config(local, remote)
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, f"{interface_name}.conf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------------------
# Tunnel lifecycle
# ---------------------------------------------------------------------------


def _detect_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return system


def tunnel_up(config_path: str) -> None:
    """Bring the WireGuard tunnel up via ``wg-quick``."""
    if not shutil.which("wg-quick"):
        raise RuntimeError(
            "wg-quick not found. Install WireGuard: "
            "brew install wireguard-tools (macOS) / "
            "apt install wireguard (Linux)"
        )
    subprocess.check_call(
        ["sudo", "wg-quick", "up", config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def tunnel_down(config_path: str) -> None:
    """Bring the WireGuard tunnel down via ``wg-quick``."""
    subprocess.check_call(
        ["sudo", "wg-quick", "down", config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def tunnel_status(interface: str = "wg_shuttle") -> dict:
    """Parse ``wg show`` output into a dict."""
    if not shutil.which("wg"):
        return {"error": "wg not found"}
    try:
        raw = subprocess.check_output(
            ["sudo", "wg", "show", interface], stderr=subprocess.PIPE
        ).decode()
    except subprocess.CalledProcessError:
        return {"interface": interface, "status": "down"}

    info: dict = {"interface": interface, "status": "up", "peers": []}
    current_peer: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("peer:"):
            if current_peer:
                info["peers"].append(current_peer)
            current_peer = {"public_key": line.split(":", 1)[1].strip()}
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            if current_peer:
                current_peer[key] = val
            else:
                info[key] = val
    if current_peer:
        info["peers"].append(current_peer)
    return info


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def wait_for_peer(
    peer_ip: str,
    timeout: int = 30,
    interval: float = 1.0,
) -> bool:
    """Wait until the peer's WireGuard IP responds to a ping."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            flag = "-c" if _detect_os() != "windows" else "-n"
            subprocess.check_call(
                ["ping", flag, "1", "-W", "1", peer_ip],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            time.sleep(interval)
    return False
