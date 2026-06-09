"""
Example: Peer-to-peer sync over WireGuard

This example shows how two peers can sync data directly over WireGuard
without any central server or third-party relay.

== SETUP ==

Both peers need WireGuard installed:
    Windows: https://www.wireguard.com/install/
    macOS:   brew install wireguard-tools
    Linux:   apt install wireguard

== STEP 1: Peer A (the server/inviter) ==

    # Generate keypair
    data-shuttle p2p init

    # Generate invite token (auto-discovers public endpoint)
    data-shuttle p2p invite --sync-port 5000

    # Copy the printed token and send it to Peer B

== STEP 2: Peer B (the joiner) ==

    # Accept the invite
    data-shuttle p2p join <token_from_peer_a>

    # Copy the printed response token and send it back to Peer A

== STEP 3: Peer A (finalize) ==

    # Complete the handshake
    data-shuttle p2p complete <response_token_from_peer_b>

== STEP 4: Both peers ==

    sudo data-shuttle p2p up

== STEP 5: Sync ==

    Run this script on both machines (adjust role="server" or role="client").
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Field, select, create_engine, Session

from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import (
    attach_change_hooks_for_models,
    set_id_generator,
)
from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport


# ===== Models (must match on both peers) =====


class Note(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    body: str
    version: int = Field(default=1)


# ===== Setup =====

MODELS = [Note]


def setup_db(db_path: str):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def run_server(peer_ip: str, sync_port: int = 5000):
    """Run the Flask sync server (Peer A)."""
    from flask import Flask
    from data_shuttle_bridge.sql.blueprints import sync_blueprint

    engine = setup_db("p2p_server.db")
    attach_change_hooks_for_models(MODELS)
    set_id_generator("peer-a")

    schema = build_schema(MODELS)

    app = Flask(__name__)
    app.config["SYNC_ENGINE_FACTORY"] = lambda: SyncEngine(
        session=Session(engine),
        peer_id="peer-b",
        schema=schema,
        policy=ConflictPolicy.LWW,
        node_id="peer-a",
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///p2p_server.db"
    app.register_blueprint(sync_blueprint)

    # Add some sample data
    with Session(engine) as sess:
        if not sess.exec(select(Note)).first():
            sess.add(Note(title="Hello from Peer A", body="Synced over WireGuard!"))
            sess.commit()
            print("Added sample note.")

    print(f"\nServer listening on 0.0.0.0:{sync_port}")
    print(f"Peer B will connect via WireGuard at {peer_ip}:{sync_port}")
    app.run(host="0.0.0.0", port=sync_port)


def run_client(peer_ip: str, sync_port: int = 5000):
    """Run the sync client (Peer B)."""
    engine = setup_db("p2p_client.db")
    attach_change_hooks_for_models(MODELS)
    set_id_generator("peer-b")

    schema = build_schema(MODELS)

    transport = TunnelPeerTransport.from_config(
        strategy="wireguard",
        peer_virtual_ip=peer_ip,
        sync_port=sync_port,
        manage_tunnel=False,
    )

    with Session(engine) as sess:
        sync_engine = SyncEngine(
            session=sess,
            peer_id="peer-a",
            schema=schema,
            policy=ConflictPolicy.LWW,
            node_id="peer-b",
        )

        print(f"\nSyncing with peer at {peer_ip}:{sync_port}...")
        pulled, pushed = sync_engine.pull_then_push(transport, batch=100)
        print(f"  Pulled {pulled} changes, Pushed {pushed} changes")

        notes = sess.exec(select(Note)).all()
        print(f"\nLocal notes after sync: {len(notes)}")
        for n in notes:
            print(f"  - {n.title}: {n.body}")

        # Add a local note and sync again
        if len(notes) < 2:
            sess.add(
                Note(title="Hello from Peer B", body="Direct P2P, no third party!")
            )
            sess.commit()
            print("\nAdded local note, syncing again...")
            pulled, pushed = sync_engine.pull_then_push(transport, batch=100)
            print(f"  Pulled {pulled} changes, Pushed {pushed} changes")


def main():
    print(
        """
╔════════════════════════════════════════════════════════════════╗
║           P2P WireGuard Sync Example                          ║
╚════════════════════════════════════════════════════════════════╝

Usage:
  Peer A (server):  python examples/p2p_sync_demo.py server
  Peer B (client):  python examples/p2p_sync_demo.py client

Prerequisites:
  1. Both peers have run the invite/join flow (see docstring above)
  2. Both peers have run: sudo data-shuttle p2p up

Default WireGuard IPs:
  Peer A (inviter): 10.0.0.1
  Peer B (joiner):  10.0.0.2
    """
    )

    if len(sys.argv) < 2 or sys.argv[1] not in ("server", "client"):
        print(
            "Usage: python p2p_sync_demo.py <server|client> [--peer-ip IP] [--port PORT]"
        )
        return 1

    role = sys.argv[1]
    # Simple arg parsing for the demo
    peer_ip = "10.0.0.2" if role == "server" else "10.0.0.1"
    sync_port = 5000

    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == "--peer-ip" and i + 1 < len(sys.argv):
            peer_ip = sys.argv[i + 1]
        elif arg == "--port" and i + 1 < len(sys.argv):
            sync_port = int(sys.argv[i + 1])

    if role == "server":
        run_server(peer_ip, sync_port)
    else:
        run_client(peer_ip, sync_port)

    return 0


if __name__ == "__main__":
    sys.exit(main())
