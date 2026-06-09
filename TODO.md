# TODO: WireGuard P2P Transport for data_shuttle_bridge

Direct device-to-device sync over WireGuard — no third-party servers, no data relay,
fully private peer-to-peer connections.

## Overview

WireGuard creates an encrypted tunnel directly between two peers. Each peer gets a
virtual IP (e.g., `10.0.0.1`, `10.0.0.2`). Once the tunnel is up, the existing
`HttpPeerTransport` works unchanged — it just connects to the peer's WireGuard IP
instead of `localhost` or a public URL.

The implementation adds:
1. A CLI/helper to generate WireGuard keypairs and config files
2. A CLI to bring the tunnel up/down (wrapping `wg-quick`)
3. A `WireGuardPeerTransport` that combines tunnel management + HTTP transport
4. An invite/join flow so peers can exchange config with a single token
5. STUN-based endpoint discovery so peers auto-detect their public IP:port
6. UDP hole-punching so both peers can be behind NAT — no port forwarding needed
7. UPnP/NAT-PMP automatic port forwarding as a secondary strategy

### NAT Traversal Strategy (how two peers behind NAT connect)

No data ever touches a third party. The connection is established in this order:

| Step | What happens | Third-party involvement |
|---|---|---|
| 1. STUN discovery | Each peer learns their own public IP:port | STUN server (stateless, free, reflects your address back — no data) |
| 2. Exchange endpoints | Via invite/response token (copy-paste) | None |
| 3. Try direct connect | UDP hole-punch via WireGuard | None |
| 4. Fallback: UPnP | Auto-open port on router | None |
| 5. Last resort | Tell user to manually port-forward | None |

This works with ~85% of NAT types (full cone, restricted cone, port-restricted cone).
Only symmetric NAT blocks hole-punching — rare on home/small-office networks.

---

## Phase 1: Key Generation & Config Management

- [ ] **1.1 Create `src/data_shuttle_bridge/p2p/` package**
  - New package for all P2P/WireGuard functionality
  - `__init__.py`, `wireguard.py`, `invite.py`, `nat.py`

- [ ] **1.2 WireGuard keypair generation (`wireguard.py`)**
  - Generate private/public key pairs using `subprocess` calling `wg genkey | wg pubkey`
  - Fallback: use `cryptography` library (Curve25519) for environments without `wg` installed
  - Store keys in `~/.localfirst_sync/wireguard/` alongside existing node config
  - Functions:
    - `generate_keypair() -> (private_key: str, public_key: str)`
    - `load_or_create_keypair(config_dir) -> WireGuardIdentity`

- [ ] **1.3 WireGuard config file generation (`wireguard.py`)**
  - Generate `wg0.conf` files for both peers from structured config
  - Dataclass: `WireGuardPeerConfig(private_key, public_key, endpoint, allowed_ips, listen_port, virtual_ip)`
  - Function: `generate_wg_config(local: WireGuardPeerConfig, remote: WireGuardPeerConfig) -> str`
  - Output valid INI-format config for `wg-quick`

---

## Phase 2: NAT Traversal & Endpoint Discovery

- [ ] **2.1 STUN-based endpoint discovery (`nat.py`)**
  - Query a public STUN server to discover own public IP:port
  - STUN is stateless — it only reflects your address back, no data passes through
  - Use well-known free STUN servers (e.g., `stun.l.google.com:19302`,
    `stun.cloudflare.com:3478`) with fallback list
  - Pure Python implementation using RFC 5389 (STUN is a simple UDP protocol)
  - Functions:
    - `discover_endpoint(stun_server, local_port) -> (public_ip: str, public_port: int)`
    - `discover_endpoint_multi(stun_servers, local_port) -> (public_ip, public_port)`
      (try multiple servers for reliability)
  - Also detect NAT type (full cone / restricted / symmetric) to predict
    whether hole-punching will succeed

- [ ] **2.2 UPnP / NAT-PMP automatic port forwarding (`nat.py`)**
  - Attempt to auto-open the WireGuard UDP port on the router
  - Use `miniupnpc` library (optional dependency) for UPnP
  - Fallback to NAT-PMP (common on Apple routers)
  - Functions:
    - `try_upnp_forward(local_port, protocol="UDP") -> (external_ip, external_port) | None`
    - `try_natpmp_forward(local_port) -> (external_ip, external_port) | None`
    - `auto_forward(local_port) -> (external_ip, external_port) | None`
      (tries UPnP first, then NAT-PMP, returns None if neither works)
  - Clean up port mappings on tunnel down

- [ ] **2.3 NAT traversal orchestrator (`nat.py`)**
  - Combines STUN + UPnP into a single call:
    1. Try UPnP/NAT-PMP first (most reliable if available)
    2. If that fails, use STUN to discover public endpoint for hole-punching
    3. If STUN detects symmetric NAT, warn user that manual port-forward is needed
  - Function:
    - `resolve_public_endpoint(local_port) -> EndpointInfo`
    - `EndpointInfo(public_ip, public_port, method: "upnp"|"natpmp"|"stun"|"manual", nat_type)`

---

## Phase 3: Invite / Join Flow (Out-of-Band Key Exchange)

- [ ] **3.1 Create invite token (`invite.py`)**
  - Peer A (the one running the server) generates a compact invite token containing:
    - Their WireGuard public key
    - Their public IP:port (auto-discovered via Phase 2 NAT traversal)
    - The sync server port (e.g., 5000)
    - A pre-shared key (PSK) for additional security (optional)
    - NAT traversal method used (so Peer B knows if hole-punching is needed)
  - Auto-discovers endpoint using `resolve_public_endpoint()` — user doesn't need
    to know their public IP
  - Encode as base64 JSON, short enough to paste in a chat/email
  - Function: `create_invite(listen_port, sync_port, public_key, endpoint_info) -> str`

- [ ] **3.2 Accept invite token (`invite.py`)**
  - Peer B decodes the invite and:
    - Generates their own keypair (if not already done)
    - Discovers their own public endpoint (STUN/UPnP)
    - Creates WireGuard configs for both sides
    - Returns a "response token" containing Peer B's public key + endpoint
  - Peer A then imports the response token to complete the handshake
  - Functions:
    - `accept_invite(token: str) -> (local_config, response_token: str)`
    - `complete_invite(response_token: str) -> remote_config`

- [ ] **3.3 IP allocation**
  - Simple scheme: inviter gets `10.0.0.1/24`, joiner gets `10.0.0.2/24`
  - For multi-peer: auto-increment from a small subnet
  - Store allocated IPs in `~/.localfirst_sync/wireguard/peers.json`

- [ ] **3.4 UDP hole-punch coordination**
  - When both peers are behind NAT (STUN-discovered endpoints), both must start
    sending WireGuard packets simultaneously to punch through their NATs
  - The invite/response tokens include a "connect at" timestamp or both peers
    start their tunnel immediately after the token exchange
  - WireGuard's `PersistentKeepalive = 25` ensures ongoing hole maintenance

---

## Phase 4: Tunnel Lifecycle Management

- [ ] **4.1 Tunnel up/down helpers (`wireguard.py`)**
  - Wrap `wg-quick up/down` with proper config path
  - Detect OS: macOS (`utun`), Linux (`wg0`), Windows (not supported initially)
  - Check for root/sudo — WireGuard needs elevated privileges
  - On tunnel down: clean up any UPnP port mappings created in Phase 2
  - Functions:
    - `tunnel_up(config_path: str) -> None`
    - `tunnel_down(config_path: str) -> None`
    - `tunnel_status() -> dict` (parse `wg show` output)

- [ ] **4.2 Health check**
  - Ping the peer's WireGuard virtual IP before starting sync
  - Retry with backoff if tunnel just came up
  - If hole-punching is in use, allow extra time for NAT entries to establish
  - Function: `wait_for_peer(peer_ip: str, timeout: int = 30) -> bool`

---

## Phase 5: WireGuard Peer Transport

- [ ] **5.1 Create `WireGuardPeerTransport` class (`transport_wireguard.py`)**
  - Extends or wraps `HttpPeerTransport`
  - Constructor takes WireGuard peer config instead of a URL
  - Builds the HTTP base URL from the peer's virtual IP + sync port
  - Optionally brings tunnel up on first use, down on close
  - Example:
    ```python
    class WireGuardPeerTransport(PeerTransport):
        def __init__(self, peer_virtual_ip: str, sync_port: int = 5000,
                     manage_tunnel: bool = False, wg_config_path: str = None):
            base_url = f"http://{peer_virtual_ip}:{sync_port}"
            self._http = HttpPeerTransport(base_url)
            ...
    ```

- [ ] **5.2 Context manager support**
  - `__enter__` brings tunnel up + health check
  - `__exit__` optionally brings tunnel down (and cleans up UPnP mappings)
  - Also support `async with` for future async transport

---

## Phase 6: CLI Commands

- [ ] **6.1 `shuttle p2p init`**
  - Generate WireGuard keypair and store in config dir
  - Print public key for manual sharing

- [ ] **6.2 `shuttle p2p invite`**
  - Generate invite token (prints to stdout)
  - Auto-discovers public endpoint via STUN/UPnP (no `--endpoint` needed in most cases)
  - Options: `--port` (WireGuard listen port), `--sync-port`, `--endpoint` (manual override)
  - Shows NAT traversal result: "Discovered public endpoint via UPnP: 203.0.113.5:51820"

- [ ] **6.3 `shuttle p2p join <token>`**
  - Accept an invite token
  - Generate local keypair if needed
  - Auto-discover own public endpoint
  - Write WireGuard config files for both peers
  - Print response token for Peer A

- [ ] **6.4 `shuttle p2p complete <response_token>`**
  - Peer A imports Peer B's response to finalize config

- [ ] **6.5 `shuttle p2p up` / `shuttle p2p down`**
  - Bring WireGuard tunnel up/down
  - On `down`: clean up UPnP port mappings
  - Show connection status

- [ ] **6.6 `shuttle p2p status`**
  - Show tunnel status, peer info, last handshake, data transferred
  - Show NAT traversal method in use

- [ ] **6.7 `shuttle p2p sync`**
  - Convenience: bring tunnel up + run sync + optionally bring tunnel down
  - Wraps existing sync logic with WireGuard transport

---

## Phase 7: Example & Docs

- [ ] **7.1 Create `examples/p2p_sync_demo.py`**
  - End-to-end example showing two peers syncing over WireGuard
  - Includes invite/join flow and sync
  - Show NAT traversal output in demo

- [ ] **7.2 Update README with P2P section**
  - Prerequisites (WireGuard installed)
  - Quick start: invite → join → sync
  - Diagram of the flow
  - Explain NAT traversal: "works behind most home/office NATs automatically"

---

## Phase 8: Testing

- [ ] **8.1 Unit tests for key generation and config rendering**
- [ ] **8.2 Unit tests for invite token encode/decode**
- [ ] **8.3 Unit tests for STUN packet encode/decode**
- [ ] **8.4 Unit tests for UPnP/NAT-PMP discovery (mocked)**
- [ ] **8.5 Unit tests for NAT traversal orchestrator (mocked)**
- [ ] **8.6 Integration test with WireGuard (requires root, CI-optional)**
- [ ] **8.7 Mock-based test for `WireGuardPeerTransport`**

---

## Prerequisites for Users

- WireGuard installed (`brew install wireguard-tools` / `apt install wireguard`)
- Root/sudo access (WireGuard needs it for tunnel interface creation)
- Internet connectivity (for STUN discovery — the STUN server only reflects
  your public address back, no data passes through it)
- Optional: `miniupnpc` Python package for automatic UPnP port forwarding
  (`pip install miniupnpc`)

### NAT Compatibility

| NAT Type | Auto-connect? | Method |
|---|---|---|
| No NAT (public IP) | Yes | Direct |
| Full cone NAT | Yes | STUN + hole-punch |
| Restricted cone NAT | Yes | STUN + hole-punch |
| Port-restricted cone NAT | Yes | STUN + hole-punch |
| UPnP-enabled router | Yes | UPnP auto port-forward |
| Symmetric NAT | No — manual port-forward needed | CLI warns user |

## Stretch Goals

- [ ] **S.1 Multi-peer mesh** — extend beyond 2 peers with auto-configured
  WireGuard AllowedIPs
- [ ] **S.2 QR code invite** — render invite token as QR for mobile/in-person sharing
- [ ] **S.3 TURN-style self-hosted relay** — for the rare symmetric NAT case,
  allow a user to run their own relay on a VPS (data still under their control)
- [ ] **S.4 mDNS/Bonjour LAN discovery** — auto-discover peers on the same
  local network without any invite token or WireGuard (simplest case)
- [ ] **S.5 Windows support** — WireGuard tunnel management on Windows
