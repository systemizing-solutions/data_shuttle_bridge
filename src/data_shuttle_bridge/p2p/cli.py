"""CLI commands for the ``shuttle p2p`` command group."""

from __future__ import annotations

import argparse
import json
import sys

from data_shuttle_bridge.p2p.wireguard import (
    DEFAULT_WG_CONFIG_DIR,
    load_or_create_keypair,
    tunnel_up,
    tunnel_down,
    tunnel_status,
    wait_for_peer,
)
from data_shuttle_bridge.p2p.nat import (
    resolve_public_endpoint,
    remove_upnp_forward,
    NAT_SYMMETRIC,
)
from data_shuttle_bridge.p2p.invite import (
    create_invite,
    accept_invite,
    complete_invite,
    INVITER_VIRTUAL_IP,
    JOINER_VIRTUAL_IP,
    DEFAULT_LISTEN_PORT,
    DEFAULT_SYNC_PORT,
)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_p2p_init(args: argparse.Namespace) -> int:
    """Generate WireGuard keypair and store in config dir."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    identity = load_or_create_keypair(config_dir)
    print(f"Public key: {identity.public_key}")
    print(f"Config dir: {config_dir}")
    return 0


def cmd_p2p_invite(args: argparse.Namespace) -> int:
    """Generate an invite token for a peer."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    port = args.port or DEFAULT_LISTEN_PORT
    sync_port = args.sync_port or DEFAULT_SYNC_PORT
    endpoint = args.endpoint or None

    if not endpoint:
        print("Discovering public endpoint...", file=sys.stderr)
        ep = resolve_public_endpoint(port)
        print(
            f"  Method: {ep.method} | NAT type: {ep.nat_type} | "
            f"Endpoint: {ep.public_ip}:{ep.public_port}",
            file=sys.stderr,
        )
        if ep.nat_type == NAT_SYMMETRIC:
            print(
                "  WARNING: Symmetric NAT detected. Consider port forwarding.",
                file=sys.stderr,
            )

    token = create_invite(
        listen_port=port,
        sync_port=sync_port,
        endpoint=endpoint,
        config_dir=config_dir,
        psk=args.psk if hasattr(args, "psk") else None,
    )
    print(f"\nInvite token (send this to your peer):\n")
    print(token)
    print(f"\nPeer B runs:  data-shuttle p2p join {token[:20]}...", file=sys.stderr)
    return 0


def cmd_p2p_join(args: argparse.Namespace) -> int:
    """Accept an invite token from a peer."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    port = args.port or DEFAULT_LISTEN_PORT
    endpoint = args.endpoint or None

    config_path, response_token, warning = accept_invite(
        token=args.token,
        listen_port=port,
        endpoint=endpoint,
        config_dir=config_dir,
    )

    if warning:
        print(f"\n{warning}", file=sys.stderr)

    print(f"WireGuard config written to: {config_path}", file=sys.stderr)
    print(f"\nResponse token (send this back to Peer A):\n")
    print(response_token)
    print(
        f"\nPeer A runs:  data-shuttle p2p complete {response_token[:20]}...",
        file=sys.stderr,
    )
    print(f"\nThen both run:  sudo data-shuttle p2p up", file=sys.stderr)
    return 0


def cmd_p2p_complete(args: argparse.Namespace) -> int:
    """Import a peer's response token to finalize WireGuard config."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    port = args.port or DEFAULT_LISTEN_PORT
    sync_port = args.sync_port or DEFAULT_SYNC_PORT

    config_path, warning = complete_invite(
        response_token=args.token,
        listen_port=port,
        sync_port=sync_port,
        config_dir=config_dir,
    )

    if warning:
        print(f"\n{warning}", file=sys.stderr)

    print(f"WireGuard config written to: {config_path}")
    print(f"\nBoth peers can now run:  sudo data-shuttle p2p up")
    return 0


def cmd_p2p_up(args: argparse.Namespace) -> int:
    """Bring the WireGuard tunnel up."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    import os

    config_path = os.path.join(config_dir, "wg_shuttle.conf")
    if not os.path.exists(config_path):
        print(f"No config found at {config_path}", file=sys.stderr)
        print(
            "Run 'data-shuttle p2p invite' and 'data-shuttle p2p complete' first.",
            file=sys.stderr,
        )
        return 1

    print(f"Bringing tunnel up ({config_path})...")
    try:
        tunnel_up(config_path)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print("Tunnel is up.")

    # Try to detect peer IP from config
    peer_ip = _detect_peer_ip(config_dir)
    if peer_ip:
        print(f"Waiting for peer at {peer_ip}...")
        if wait_for_peer(peer_ip, timeout=15):
            print(f"Peer {peer_ip} is reachable!")
        else:
            print(f"Peer {peer_ip} not yet reachable (they may not have started yet).")
    return 0


def cmd_p2p_down(args: argparse.Namespace) -> int:
    """Bring the WireGuard tunnel down."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    import os

    config_path = os.path.join(config_dir, "wg_shuttle.conf")
    try:
        tunnel_down(config_path)
        remove_upnp_forward()
        print("Tunnel is down.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_p2p_status(args: argparse.Namespace) -> int:
    """Show WireGuard tunnel status."""
    status = tunnel_status("wg_shuttle")
    if status.get("status") == "down" or "error" in status:
        print(f"Tunnel: down")
        if "error" in status:
            print(f"  ({status['error']})")
        return 0

    print(f"Tunnel: up")
    for key, val in status.items():
        if key in ("interface", "status", "peers"):
            continue
        print(f"  {key}: {val}")

    for peer in status.get("peers", []):
        print(f"\n  Peer: {peer.get('public_key', 'unknown')}")
        for k, v in peer.items():
            if k == "public_key":
                continue
            print(f"    {k}: {v}")
    return 0


def cmd_p2p_sync(args: argparse.Namespace) -> int:
    """Bring tunnel up, sync, optionally bring tunnel down."""
    config_dir = args.config_dir or DEFAULT_WG_CONFIG_DIR
    import os

    config_path = os.path.join(config_dir, "wg_shuttle.conf")
    peer_ip = _detect_peer_ip(config_dir)
    sync_port = args.sync_port or DEFAULT_SYNC_PORT

    if not peer_ip:
        print("Cannot determine peer IP. Run invite/join flow first.", file=sys.stderr)
        return 1

    from data_shuttle_bridge.p2p.transport_wireguard import WireGuardPeerTransport

    transport = WireGuardPeerTransport(
        peer_virtual_ip=peer_ip,
        sync_port=sync_port,
        manage_tunnel=True,
        wg_config_path=config_path,
    )

    try:
        with transport:
            print(f"Connected to peer at {peer_ip}:{sync_port}")
            print("Tunnel is up and peer is reachable.")
            print("Use SyncEngine with this transport to sync data.")
            # The actual sync requires models/schema which is app-specific.
            # This command validates connectivity. For full sync, use the
            # programmatic API (see examples/p2p_sync_demo.py).
            print("\nTo sync programmatically:")
            print(
                "  from data_shuttle_bridge.p2p.transport_wireguard import WireGuardPeerTransport"
            )
            print(f'  transport = WireGuardPeerTransport("{peer_ip}", {sync_port})')
            print("  pulled, pushed = sync_engine.pull_then_push(transport)")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.keep_up:
        print("Tunnel left up (--keep-up).")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_peer_ip(config_dir: str) -> str | None:
    """Detect the peer's virtual IP from the peers.json file."""
    import os

    peers_path = os.path.join(config_dir, "peers.json")
    if not os.path.exists(peers_path):
        return None
    with open(peers_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    peers = data.get("peers", [])
    if peers:
        return peers[-1].get("virtual_ip")
    return None


# ---------------------------------------------------------------------------
# Subparser registration (called from main CLI)
# ---------------------------------------------------------------------------


def add_p2p_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``p2p`` command group."""
    p2p = subparsers.add_parser("p2p", help="Peer-to-peer WireGuard sync")
    p2p_sub = p2p.add_subparsers(dest="p2p_cmd", required=True)

    # Common arguments
    def _add_common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--config-dir",
            default=None,
            help=f"WireGuard config directory (default: {DEFAULT_WG_CONFIG_DIR})",
        )

    # p2p init
    init_p = p2p_sub.add_parser("init", help="Generate WireGuard keypair")
    _add_common(init_p)
    init_p.set_defaults(func=cmd_p2p_init)

    # p2p invite
    invite_p = p2p_sub.add_parser("invite", help="Generate invite token for a peer")
    _add_common(invite_p)
    invite_p.add_argument("--port", type=int, help="WireGuard listen port")
    invite_p.add_argument("--sync-port", type=int, help="Sync server port")
    invite_p.add_argument("--endpoint", help="Manual public endpoint (ip:port)")
    invite_p.add_argument("--psk", help="Pre-shared key for extra security")
    invite_p.set_defaults(func=cmd_p2p_invite)

    # p2p join
    join_p = p2p_sub.add_parser("join", help="Accept a peer's invite token")
    _add_common(join_p)
    join_p.add_argument("token", help="Invite token from Peer A")
    join_p.add_argument("--port", type=int, help="WireGuard listen port")
    join_p.add_argument("--endpoint", help="Manual public endpoint (ip:port)")
    join_p.set_defaults(func=cmd_p2p_join)

    # p2p complete
    complete_p = p2p_sub.add_parser(
        "complete", help="Finalize config with peer's response"
    )
    _add_common(complete_p)
    complete_p.add_argument("token", help="Response token from Peer B")
    complete_p.add_argument("--port", type=int, help="WireGuard listen port")
    complete_p.add_argument("--sync-port", type=int, help="Sync server port")
    complete_p.set_defaults(func=cmd_p2p_complete)

    # p2p up
    up_p = p2p_sub.add_parser("up", help="Bring WireGuard tunnel up")
    _add_common(up_p)
    up_p.set_defaults(func=cmd_p2p_up)

    # p2p down
    down_p = p2p_sub.add_parser("down", help="Bring WireGuard tunnel down")
    _add_common(down_p)
    down_p.set_defaults(func=cmd_p2p_down)

    # p2p status
    status_p = p2p_sub.add_parser("status", help="Show tunnel status")
    _add_common(status_p)
    status_p.set_defaults(func=cmd_p2p_status)

    # p2p sync
    sync_p = p2p_sub.add_parser("sync", help="Bring tunnel up, verify connectivity")
    _add_common(sync_p)
    sync_p.add_argument("--sync-port", type=int, help="Sync server port")
    sync_p.add_argument(
        "--keep-up", action="store_true", help="Leave tunnel up after sync"
    )
    sync_p.set_defaults(func=cmd_p2p_sync)
