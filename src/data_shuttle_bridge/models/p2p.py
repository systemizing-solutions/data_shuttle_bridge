"""P2P networking data models."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from typing import Optional


@dataclass
class WireGuardIdentity:
    private_key: str
    public_key: str

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.chmod(path, 0o600)

    @classmethod
    def load(cls, path: str) -> "WireGuardIdentity":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(private_key=data["private_key"], public_key=data["public_key"])


@dataclass
class WireGuardPeerConfig:
    private_key: str
    public_key: str
    virtual_ip: str
    listen_port: int = 51820
    endpoint: str = ""
    allowed_ips: str = "10.0.0.0/24"
    preshared_key: str = ""
    persistent_keepalive: int = 25
    dns: str = ""


@dataclass
class EndpointInfo:
    public_ip: str
    public_port: int
    method: str  # "upnp" | "natpmp" | "stun" | "manual"
    nat_type: str = "unknown"
