"""NAT traversal: STUN discovery, UPnP/NAT-PMP port forwarding, orchestrator."""

from __future__ import annotations

import logging
import os
import random
import socket
import struct
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from data_shuttle_bridge.models.p2p import EndpointInfo

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUN_SERVERS: List[Tuple[str, int]] = [
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.stunprotocol.org", 3478),
]

# STUN message constants (RFC 5389)
STUN_MAGIC_COOKIE = 0x2112A442
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101
STUN_ATTR_MAPPED_ADDRESS = 0x0001
STUN_ATTR_XOR_MAPPED_ADDRESS = 0x0020

# NAT type labels
NAT_FULL_CONE = "full_cone"
NAT_RESTRICTED = "restricted"
NAT_PORT_RESTRICTED = "port_restricted"
NAT_SYMMETRIC = "symmetric"
NAT_OPEN = "open"
NAT_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# STUN — pure Python RFC 5389 implementation
# ---------------------------------------------------------------------------


def _build_stun_request() -> Tuple[bytes, bytes]:
    """Build a STUN Binding Request and return (packet, transaction_id)."""
    txn_id = os.urandom(12)
    header = struct.pack(
        "!HHI12s",
        STUN_BINDING_REQUEST,
        0,  # message length (no attributes)
        STUN_MAGIC_COOKIE,
        txn_id,
    )
    return header, txn_id


def _parse_stun_response(data: bytes, txn_id: bytes) -> Optional[Tuple[str, int]]:
    """Parse a STUN Binding Response, return (ip, port) or None."""
    if len(data) < 20:
        return None

    msg_type, msg_len, cookie = struct.unpack_from("!HHI", data, 0)
    resp_txn = data[8:20]

    if msg_type != STUN_BINDING_RESPONSE or resp_txn != txn_id:
        return None

    offset = 20
    while offset < 20 + msg_len:
        if offset + 4 > len(data):
            break
        attr_type, attr_len = struct.unpack_from("!HH", data, offset)
        offset += 4
        if offset + attr_len > len(data):
            break

        if attr_type == STUN_ATTR_XOR_MAPPED_ADDRESS:
            _reserved, family = struct.unpack_from("!BB", data, offset)
            if family == 0x01:  # IPv4
                xport, xaddr = struct.unpack_from("!HI", data, offset + 2)
                port = xport ^ (STUN_MAGIC_COOKIE >> 16)
                addr = xaddr ^ STUN_MAGIC_COOKIE
                ip = socket.inet_ntoa(struct.pack("!I", addr))
                return ip, port

        elif attr_type == STUN_ATTR_MAPPED_ADDRESS:
            _reserved, family = struct.unpack_from("!BB", data, offset)
            if family == 0x01:
                port, addr = struct.unpack_from("!HI", data, offset + 2)
                ip = socket.inet_ntoa(struct.pack("!I", addr))
                return ip, port

        # Pad to 4-byte boundary
        offset += attr_len + (4 - attr_len % 4) % 4

    return None


def discover_endpoint(
    stun_server: Tuple[str, int],
    local_port: int = 0,
    timeout: float = 3.0,
) -> Optional[Tuple[str, int]]:
    """Query a single STUN server. Returns (public_ip, public_port) or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        if local_port:
            sock.bind(("", local_port))
        pkt, txn_id = _build_stun_request()
        sock.sendto(pkt, stun_server)
        data, _addr = sock.recvfrom(1024)
        return _parse_stun_response(data, txn_id)
    except (socket.timeout, OSError) as exc:
        log.debug("STUN query to %s failed: %s", stun_server, exc)
        return None
    finally:
        sock.close()


def discover_endpoint_multi(
    stun_servers: Optional[List[Tuple[str, int]]] = None,
    local_port: int = 0,
    timeout: float = 3.0,
) -> Optional[Tuple[str, int]]:
    """Try multiple STUN servers, return the first successful result."""
    servers = list(stun_servers or STUN_SERVERS)
    random.shuffle(servers)
    for srv in servers:
        result = discover_endpoint(srv, local_port=local_port, timeout=timeout)
        if result:
            return result
    return None


# ---------------------------------------------------------------------------
# NAT type detection (simplified)
# ---------------------------------------------------------------------------


def detect_nat_type(
    stun_servers: Optional[List[Tuple[str, int]]] = None,
    local_port: int = 0,
) -> str:
    """Detect NAT type by querying two different STUN servers from the same port.

    If both return the same public IP:port → cone NAT (hole-punching likely works).
    If they return different ports → symmetric NAT (hole-punching unlikely).
    """
    servers = list(stun_servers or STUN_SERVERS)
    if len(servers) < 2:
        return NAT_UNKNOWN

    # We need to bind to the same local port for both queries
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(3.0)
        if local_port:
            sock.bind(("", local_port))
        else:
            sock.bind(("", 0))

        results = []
        for srv in servers[:2]:
            pkt, txn_id = _build_stun_request()
            sock.sendto(pkt, srv)
            try:
                data, _addr = sock.recvfrom(1024)
                parsed = _parse_stun_response(data, txn_id)
                if parsed:
                    results.append(parsed)
            except socket.timeout:
                continue

        if len(results) < 2:
            return NAT_UNKNOWN

        if results[0] == results[1]:
            # Same mapped address from both servers → cone NAT
            local_addr = sock.getsockname()
            if results[0][0] == _get_local_ip() and results[0][1] == local_addr[1]:
                return NAT_OPEN
            return NAT_FULL_CONE

        if results[0][0] == results[1][0] and results[0][1] != results[1][1]:
            # Same IP, different port → symmetric NAT
            return NAT_SYMMETRIC

        return NAT_UNKNOWN
    except OSError:
        return NAT_UNKNOWN
    finally:
        sock.close()


def _get_local_ip() -> str:
    """Get the local IP address used for outbound connections."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# UPnP port forwarding
# ---------------------------------------------------------------------------

_upnp_mapping: Optional[Tuple[int, str]] = None  # (external_port, protocol)


def try_upnp_forward(
    local_port: int,
    protocol: str = "UDP",
    description: str = "data_shuttle_bridge P2P",
    lease_duration: int = 0,
) -> Optional[Tuple[str, int]]:
    """Attempt UPnP port forwarding. Returns (external_ip, external_port) or None."""
    global _upnp_mapping
    try:
        import miniupnpc  # type: ignore[import-untyped]
    except ImportError:
        log.debug("miniupnpc not installed — skipping UPnP")
        return None

    try:
        u = miniupnpc.UPnP()
        u.discoverdelay = 2000
        devices = u.discover()
        if devices == 0:
            return None
        u.selectigd()
        external_ip = u.externalipaddress()
        local_ip = _get_local_ip()
        # Try to map the same external port
        external_port = local_port
        result = u.addportmapping(
            external_port,
            protocol,
            local_ip,
            local_port,
            description,
            "",
            lease_duration,
        )
        if result:
            _upnp_mapping = (external_port, protocol)
            log.info(
                "UPnP: mapped %s:%d → %s:%d (%s)",
                external_ip,
                external_port,
                local_ip,
                local_port,
                protocol,
            )
            return external_ip, external_port
        return None
    except Exception as exc:
        log.debug("UPnP forwarding failed: %s", exc)
        return None


def remove_upnp_forward() -> None:
    """Remove the UPnP port mapping created by ``try_upnp_forward``."""
    global _upnp_mapping
    if _upnp_mapping is None:
        return
    try:
        import miniupnpc  # type: ignore[import-untyped]

        u = miniupnpc.UPnP()
        u.discoverdelay = 2000
        u.discover()
        u.selectigd()
        external_port, protocol = _upnp_mapping
        u.deleteportmapping(external_port, protocol)
        log.info("UPnP: removed port mapping %d/%s", external_port, protocol)
        _upnp_mapping = None
    except Exception as exc:
        log.debug("UPnP cleanup failed: %s", exc)


# ---------------------------------------------------------------------------
# NAT-PMP port forwarding
# ---------------------------------------------------------------------------


def try_natpmp_forward(
    local_port: int,
    lifetime: int = 7200,
) -> Optional[Tuple[str, int]]:
    """Attempt NAT-PMP port forwarding. Returns (external_ip, external_port) or None.

    NAT-PMP is common on Apple routers and some consumer routers.
    Uses a simple UDP protocol (RFC 6886) to gateway at x.x.x.1.
    """
    gateway = _get_default_gateway()
    if not gateway:
        return None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(2.0)
        # NAT-PMP mapping request: version=0, opcode=1 (UDP), reserved=0,
        # internal_port, external_port=0 (let router choose), lifetime
        request = struct.pack(
            "!BBHHHHI",
            0,  # version
            1,  # opcode: map UDP
            0,  # reserved
            local_port,
            0,  # suggested external port (0 = any)
            0,  # upper 16 bits of lifetime
            lifetime,
        )
        sock.sendto(request, (gateway, 5351))
        data, _addr = sock.recvfrom(256)

        if len(data) < 16:
            return None
        _ver, opcode, result_code = struct.unpack_from("!BBH", data, 0)
        if result_code != 0:
            return None
        _epoch, internal, external, mapped_lifetime = struct.unpack_from(
            "!IHHI", data, 4
        )

        # Get external IP via a separate request
        ext_ip = _natpmp_get_external_ip(gateway, sock)
        if ext_ip:
            log.info(
                "NAT-PMP: mapped %s:%d → local:%d for %ds",
                ext_ip,
                external,
                internal,
                mapped_lifetime,
            )
            return ext_ip, external
        return None
    except (socket.timeout, OSError) as exc:
        log.debug("NAT-PMP failed: %s", exc)
        return None
    finally:
        sock.close()


def _natpmp_get_external_ip(
    gateway: str, sock: Optional[socket.socket] = None
) -> Optional[str]:
    """Request the external IP via NAT-PMP (opcode 0)."""
    own_sock = sock is None
    if own_sock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
    try:
        request = struct.pack("!BB", 0, 0)  # version=0, opcode=0
        sock.sendto(request, (gateway, 5351))
        data, _addr = sock.recvfrom(256)
        if len(data) >= 12:
            _ver, _op, _result, _epoch = struct.unpack_from("!BBHI", data, 0)
            ip_bytes = data[8:12]
            return socket.inet_ntoa(ip_bytes)
        return None
    except (socket.timeout, OSError):
        return None
    finally:
        if own_sock:
            sock.close()


def _get_default_gateway() -> Optional[str]:
    """Get the default gateway IP (best-effort, cross-platform)."""
    import platform as _platform

    system = _platform.system().lower()
    try:
        if system == "darwin":
            out = subprocess.check_output(
                ["route", "-n", "get", "default"], stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                if "gateway:" in line.lower():
                    return line.split(":", 1)[1].strip()
        elif system == "linux":
            out = subprocess.check_output(
                ["ip", "route", "show", "default"], stderr=subprocess.DEVNULL
            ).decode()
            parts = out.split()
            if "via" in parts:
                return parts[parts.index("via") + 1]
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass
    return None


# Need subprocess for gateway detection
import subprocess  # noqa: E402


# ---------------------------------------------------------------------------
# Auto-forward: tries UPnP then NAT-PMP
# ---------------------------------------------------------------------------


def auto_forward(
    local_port: int,
) -> Optional[Tuple[str, int]]:
    """Try UPnP first, then NAT-PMP. Returns (external_ip, external_port) or None."""
    result = try_upnp_forward(local_port)
    if result:
        return result
    return try_natpmp_forward(local_port)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def resolve_public_endpoint(
    local_port: int = 51820,
    stun_servers: Optional[List[Tuple[str, int]]] = None,
) -> EndpointInfo:
    """Discover our public endpoint using the best available method.

    Order: UPnP/NAT-PMP → STUN → manual.
    """
    # 1. Try automatic port forwarding (most reliable)
    fwd = auto_forward(local_port)
    if fwd:
        return EndpointInfo(
            public_ip=fwd[0],
            public_port=fwd[1],
            method="upnp",
            nat_type=NAT_FULL_CONE,
        )

    # 2. STUN discovery
    endpoint = discover_endpoint_multi(stun_servers, local_port=local_port)
    if endpoint:
        nat_type = detect_nat_type(stun_servers, local_port=local_port)
        return EndpointInfo(
            public_ip=endpoint[0],
            public_port=endpoint[1],
            method="stun",
            nat_type=nat_type,
        )

    # 3. Fallback: manual
    local_ip = _get_local_ip()
    return EndpointInfo(
        public_ip=local_ip,
        public_port=local_port,
        method="manual",
        nat_type=NAT_UNKNOWN,
    )
