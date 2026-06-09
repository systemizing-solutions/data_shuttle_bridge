"""Invite / join flow for out-of-band WireGuard key exchange."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

from data_shuttle_bridge.p2p.wireguard import (
    DEFAULT_WG_CONFIG_DIR,
    WireGuardIdentity,
    WireGuardPeerConfig,
    generate_keypair,
    load_or_create_keypair,
    write_wg_config,
)
from data_shuttle_bridge.p2p.nat import (
    EndpointInfo,
    resolve_public_endpoint,
    NAT_SYMMETRIC,
)

PEERS_FILE = "peers.json"
DEFAULT_LISTEN_PORT = 51820
DEFAULT_SYNC_PORT = 5000
INVITER_VIRTUAL_IP = "10.0.0.1"
JOINER_VIRTUAL_IP = "10.0.0.2"


# ---------------------------------------------------------------------------
# Token encoding helpers
# ---------------------------------------------------------------------------


def _encode_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_token(token: str) -> dict:
    # Re-pad base64
    padding = 4 - len(token) % 4
    if padding != 4:
        token += "=" * padding
    raw = base64.urlsafe_b64decode(token)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Peers persistence
# ---------------------------------------------------------------------------


def _load_peers(config_dir: str) -> dict:
    path = os.path.join(config_dir, PEERS_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"peers": [], "next_ip_octet": 3}


def _save_peers(config_dir: str, peers: dict) -> None:
    path = os.path.join(config_dir, PEERS_FILE)
    os.makedirs(config_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(peers, f, indent=2)


# ---------------------------------------------------------------------------
# Create invite (Peer A — the server/inviter)
# ---------------------------------------------------------------------------


def create_invite(
    listen_port: int = DEFAULT_LISTEN_PORT,
    sync_port: int = DEFAULT_SYNC_PORT,
    endpoint: Optional[str] = None,
    config_dir: str = DEFAULT_WG_CONFIG_DIR,
    psk: Optional[str] = None,
) -> str:
    """Generate an invite token for Peer B.

    Returns a base64 string containing public key, endpoint, ports, and
    NAT traversal info.  Short enough to paste in chat/email.
    """
    identity = load_or_create_keypair(config_dir)

    # Auto-discover endpoint if not provided
    if endpoint:
        ep_info = EndpointInfo(
            public_ip=endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint,
            public_port=(
                int(endpoint.rsplit(":", 1)[1]) if ":" in endpoint else listen_port
            ),
            method="manual",
        )
    else:
        ep_info = resolve_public_endpoint(listen_port)

    payload = {
        "v": 1,  # token version
        "pk": identity.public_key,
        "ip": ep_info.public_ip,
        "pp": ep_info.public_port,  # peer port (WireGuard)
        "sp": sync_port,
        "lp": listen_port,
        "m": ep_info.method,
        "nt": ep_info.nat_type,
        "vip": INVITER_VIRTUAL_IP,
    }
    if psk:
        payload["psk"] = psk

    return _encode_token(payload)


# ---------------------------------------------------------------------------
# Accept invite (Peer B — the joiner)
# ---------------------------------------------------------------------------


def accept_invite(
    token: str,
    listen_port: int = DEFAULT_LISTEN_PORT,
    endpoint: Optional[str] = None,
    config_dir: str = DEFAULT_WG_CONFIG_DIR,
) -> Tuple[str, str, str]:
    """Decode an invite, generate configs, and return a response token.

    Returns:
        (config_path, response_token, warning_message_or_empty)
    """
    invite = _decode_token(token)
    if invite.get("v") != 1:
        raise ValueError(f"Unsupported invite version: {invite.get('v')}")

    identity = load_or_create_keypair(config_dir)

    # Discover our own endpoint
    if endpoint:
        my_ep = EndpointInfo(
            public_ip=endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint,
            public_port=(
                int(endpoint.rsplit(":", 1)[1]) if ":" in endpoint else listen_port
            ),
            method="manual",
        )
    else:
        my_ep = resolve_public_endpoint(listen_port)

    psk = invite.get("psk", "")

    # Build WireGuard configs
    # Local = Peer B (joiner)
    local_cfg = WireGuardPeerConfig(
        private_key=identity.private_key,
        public_key=identity.public_key,
        virtual_ip=JOINER_VIRTUAL_IP,
        listen_port=listen_port,
        endpoint="",
        allowed_ips=f"{invite['vip']}/32",
        preshared_key=psk,
        persistent_keepalive=25,
    )

    # Remote = Peer A (inviter)
    remote_endpoint = f"{invite['ip']}:{invite['pp']}"
    remote_cfg = WireGuardPeerConfig(
        private_key="",  # we don't have Peer A's private key
        public_key=invite["pk"],
        virtual_ip=invite["vip"],
        listen_port=invite["lp"],
        endpoint=remote_endpoint,
        allowed_ips=f"{invite['vip']}/32",
        preshared_key=psk,
    )

    # Write local config
    config_path = write_wg_config(local_cfg, remote_cfg, config_dir)

    # Save peer info
    peers = _load_peers(config_dir)
    peers["peers"].append(
        {
            "role": "inviter",
            "public_key": invite["pk"],
            "virtual_ip": invite["vip"],
            "endpoint": remote_endpoint,
            "sync_port": invite["sp"],
        }
    )
    _save_peers(config_dir, peers)

    # Build response token for Peer A
    response = {
        "v": 1,
        "pk": identity.public_key,
        "ip": my_ep.public_ip,
        "pp": my_ep.public_port,
        "lp": listen_port,
        "m": my_ep.method,
        "nt": my_ep.nat_type,
        "vip": JOINER_VIRTUAL_IP,
    }
    if psk:
        response["psk"] = psk

    response_token = _encode_token(response)

    warning = ""
    if invite.get("nt") == NAT_SYMMETRIC or my_ep.nat_type == NAT_SYMMETRIC:
        warning = (
            "WARNING: Symmetric NAT detected. Hole-punching is unlikely to work. "
            "Consider manual port forwarding on at least one side."
        )

    return config_path, response_token, warning


# ---------------------------------------------------------------------------
# Complete invite (Peer A — finalize after receiving response)
# ---------------------------------------------------------------------------


def complete_invite(
    response_token: str,
    listen_port: int = DEFAULT_LISTEN_PORT,
    sync_port: int = DEFAULT_SYNC_PORT,
    config_dir: str = DEFAULT_WG_CONFIG_DIR,
) -> Tuple[str, str]:
    """Import Peer B's response and write Peer A's WireGuard config.

    Returns:
        (config_path, warning_message_or_empty)
    """
    response = _decode_token(response_token)
    if response.get("v") != 1:
        raise ValueError(f"Unsupported response version: {response.get('v')}")

    identity = load_or_create_keypair(config_dir)
    psk = response.get("psk", "")

    # Local = Peer A (inviter)
    local_cfg = WireGuardPeerConfig(
        private_key=identity.private_key,
        public_key=identity.public_key,
        virtual_ip=INVITER_VIRTUAL_IP,
        listen_port=listen_port,
        endpoint="",
        allowed_ips=f"{response['vip']}/32",
        preshared_key=psk,
        persistent_keepalive=25,
    )

    # Remote = Peer B (joiner)
    remote_endpoint = f"{response['ip']}:{response['pp']}"
    remote_cfg = WireGuardPeerConfig(
        private_key="",
        public_key=response["pk"],
        virtual_ip=response["vip"],
        listen_port=response["lp"],
        endpoint=remote_endpoint,
        allowed_ips=f"{response['vip']}/32",
        preshared_key=psk,
    )

    config_path = write_wg_config(local_cfg, remote_cfg, config_dir)

    # Save peer info
    peers = _load_peers(config_dir)
    peers["peers"].append(
        {
            "role": "joiner",
            "public_key": response["pk"],
            "virtual_ip": response["vip"],
            "endpoint": remote_endpoint,
            "sync_port": sync_port,
        }
    )
    _save_peers(config_dir, peers)

    warning = ""
    if response.get("nt") == NAT_SYMMETRIC:
        warning = (
            "WARNING: Peer B is behind symmetric NAT. Hole-punching is unlikely. "
            "Consider manual port forwarding."
        )

    return config_path, warning
