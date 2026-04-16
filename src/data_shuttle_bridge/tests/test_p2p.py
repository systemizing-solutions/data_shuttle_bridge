"""Tests for the P2P WireGuard module."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import time
from unittest.mock import MagicMock, call, patch

import pytest

# Import individual modules directly to avoid cascading through the main __init__
from data_shuttle_bridge.p2p.wireguard import (
    WireGuardIdentity,
    WireGuardPeerConfig,
    _detect_os,
    _generate_keypair_wg_cli,
    generate_keypair,
    generate_wg_config,
    load_or_create_keypair,
    tunnel_down,
    tunnel_status,
    tunnel_up,
    wait_for_peer,
    write_wg_config,
)
from data_shuttle_bridge.p2p.nat import (
    STUN_ATTR_XOR_MAPPED_ADDRESS,
    STUN_BINDING_RESPONSE,
    STUN_MAGIC_COOKIE,
    EndpointInfo,
    NAT_FULL_CONE,
    NAT_SYMMETRIC,
    NAT_UNKNOWN,
    _build_stun_request,
    _parse_stun_response,
    detect_nat_type,
    discover_endpoint,
    resolve_public_endpoint,
)
from data_shuttle_bridge.p2p.invite import (
    _decode_token,
    _encode_token,
    accept_invite,
    complete_invite,
    create_invite,
    INVITER_VIRTUAL_IP,
    JOINER_VIRTUAL_IP,
)


# ============================================================================
# Phase 1: Key generation & config
# ============================================================================


class TestKeypairGeneration:
    def test_generate_keypair_returns_base64_strings(self):
        priv, pub = generate_keypair()
        assert isinstance(priv, str)
        assert isinstance(pub, str)
        assert len(priv) > 10
        assert len(pub) > 10
        # Keys should be different
        assert priv != pub

    def test_generate_keypair_deterministic_length(self):
        """Curve25519 keys are 32 bytes → 44 chars in base64."""
        priv, pub = generate_keypair()
        # Allow for both wg CLI (44 chars with =) and cryptography lib
        assert 40 <= len(priv) <= 48
        assert 40 <= len(pub) <= 48

    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value="/usr/bin/wg")
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_output")
    def test_generate_keypair_wg_cli_path(self, mock_check_output, mock_which):
        """generate_keypair dispatches to _generate_keypair_wg_cli when wg is found."""
        mock_check_output.side_effect = [
            b"FAKE_PRIVATE_KEY\n",  # wg genkey
            b"FAKE_PUBLIC_KEY\n",  # wg pubkey
        ]
        priv, pub = generate_keypair()
        assert priv == "FAKE_PRIVATE_KEY"
        assert pub == "FAKE_PUBLIC_KEY"
        mock_which.assert_called_once_with("wg")

    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_output")
    def test_generate_keypair_wg_cli_direct(self, mock_check_output):
        """_generate_keypair_wg_cli calls wg genkey then wg pubkey."""
        mock_check_output.side_effect = [
            b"cGhvbmVrZXk=\n",  # wg genkey
            b"cHVia2V5MTIz\n",  # wg pubkey
        ]
        priv, pub = _generate_keypair_wg_cli()
        assert priv == "cGhvbmVrZXk="
        assert pub == "cHVia2V5MTIz"
        assert mock_check_output.call_count == 2
        # First call: wg genkey
        assert mock_check_output.call_args_list[0] == call(
            ["wg", "genkey"], stderr=subprocess.DEVNULL
        )
        # Second call: wg pubkey with private key as input
        assert mock_check_output.call_args_list[1] == call(
            ["wg", "pubkey"], input=b"cGhvbmVrZXk=", stderr=subprocess.DEVNULL
        )

    def test_load_or_create_keypair_creates_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = load_or_create_keypair(tmpdir)
            assert isinstance(identity, WireGuardIdentity)
            assert identity.private_key
            assert identity.public_key

            # File should exist
            assert os.path.exists(os.path.join(tmpdir, "identity.json"))

    def test_load_or_create_keypair_reuses_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            id1 = load_or_create_keypair(tmpdir)
            id2 = load_or_create_keypair(tmpdir)
            assert id1.private_key == id2.private_key
            assert id1.public_key == id2.public_key

    def test_identity_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_id.json")
            priv, pub = generate_keypair()
            identity = WireGuardIdentity(private_key=priv, public_key=pub)
            identity.save(path)

            loaded = WireGuardIdentity.load(path)
            assert loaded.private_key == priv
            assert loaded.public_key == pub


class TestConfigRendering:
    def test_generate_wg_config_basic(self):
        local = WireGuardPeerConfig(
            private_key="LOCAL_PRIV_KEY",
            public_key="LOCAL_PUB_KEY",
            virtual_ip="10.0.0.1",
            listen_port=51820,
        )
        remote = WireGuardPeerConfig(
            private_key="",
            public_key="REMOTE_PUB_KEY",
            virtual_ip="10.0.0.2",
            endpoint="1.2.3.4:51820",
            allowed_ips="10.0.0.2/32",
        )
        config = generate_wg_config(local, remote)

        assert "[Interface]" in config
        assert "PrivateKey = LOCAL_PRIV_KEY" in config
        assert "Address = 10.0.0.1/24" in config
        assert "ListenPort = 51820" in config
        assert "[Peer]" in config
        assert "PublicKey = REMOTE_PUB_KEY" in config
        assert "AllowedIPs = 10.0.0.2/32" in config
        assert "Endpoint = 1.2.3.4:51820" in config
        assert "PersistentKeepalive = 25" in config

    def test_generate_wg_config_with_psk(self):
        local = WireGuardPeerConfig(
            private_key="PRIV",
            public_key="PUB",
            virtual_ip="10.0.0.1",
            preshared_key="PSK_VALUE",
        )
        remote = WireGuardPeerConfig(
            private_key="", public_key="RPUB", virtual_ip="10.0.0.2"
        )
        config = generate_wg_config(local, remote)
        assert "PresharedKey = PSK_VALUE" in config

    def test_generate_wg_config_no_endpoint(self):
        local = WireGuardPeerConfig(
            private_key="PRIV", public_key="PUB", virtual_ip="10.0.0.1"
        )
        remote = WireGuardPeerConfig(
            private_key="", public_key="RPUB", virtual_ip="10.0.0.2", endpoint=""
        )
        config = generate_wg_config(local, remote)
        assert "Endpoint" not in config

    def test_write_wg_config_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local = WireGuardPeerConfig(
                private_key="PRIV", public_key="PUB", virtual_ip="10.0.0.1"
            )
            remote = WireGuardPeerConfig(
                private_key="", public_key="RPUB", virtual_ip="10.0.0.2"
            )
            path = write_wg_config(local, remote, tmpdir, "test_iface")
            assert os.path.exists(path)
            assert path.endswith("test_iface.conf")
            with open(path) as f:
                content = f.read()
            assert "[Interface]" in content

    def test_generate_wg_config_with_dns(self):
        local = WireGuardPeerConfig(
            private_key="PRIV",
            public_key="PUB",
            virtual_ip="10.0.0.1",
            dns="1.1.1.1",
        )
        remote = WireGuardPeerConfig(
            private_key="", public_key="RPUB", virtual_ip="10.0.0.2"
        )
        config = generate_wg_config(local, remote)
        assert "DNS = 1.1.1.1" in config


# ============================================================================
# Tunnel lifecycle & OS detection
# ============================================================================


class TestDetectOS:
    @patch("data_shuttle_bridge.p2p.wireguard.platform.system", return_value="Darwin")
    def test_detect_macos(self, _mock):
        assert _detect_os() == "macos"

    @patch("data_shuttle_bridge.p2p.wireguard.platform.system", return_value="Linux")
    def test_detect_linux(self, _mock):
        assert _detect_os() == "linux"

    @patch("data_shuttle_bridge.p2p.wireguard.platform.system", return_value="Windows")
    def test_detect_windows(self, _mock):
        assert _detect_os() == "windows"


class TestTunnelUp:
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_call")
    @patch(
        "data_shuttle_bridge.p2p.wireguard.shutil.which",
        return_value="/usr/bin/wg-quick",
    )
    def test_tunnel_up_calls_wg_quick(self, mock_which, mock_call):
        tunnel_up("/etc/wireguard/wg0.conf")
        mock_which.assert_called_once_with("wg-quick")
        mock_call.assert_called_once_with(
            ["sudo", "wg-quick", "up", "/etc/wireguard/wg0.conf"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value=None)
    def test_tunnel_up_raises_if_wg_quick_missing(self, _mock):
        with pytest.raises(RuntimeError, match="wg-quick not found"):
            tunnel_up("/etc/wireguard/wg0.conf")


class TestTunnelDown:
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_call")
    def test_tunnel_down_calls_wg_quick(self, mock_call):
        tunnel_down("/etc/wireguard/wg0.conf")
        mock_call.assert_called_once_with(
            ["sudo", "wg-quick", "down", "/etc/wireguard/wg0.conf"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )


class TestTunnelStatus:
    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value=None)
    def test_returns_error_when_wg_not_found(self, _mock):
        result = tunnel_status()
        assert result == {"error": "wg not found"}

    @patch(
        "data_shuttle_bridge.p2p.wireguard.subprocess.check_output",
        side_effect=subprocess.CalledProcessError(1, "wg"),
    )
    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value="/usr/bin/wg")
    def test_returns_down_on_called_process_error(self, _mock_which, _mock_out):
        result = tunnel_status("wg0")
        assert result == {"interface": "wg0", "status": "down"}

    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_output")
    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value="/usr/bin/wg")
    def test_parses_wg_show_output(self, _mock_which, mock_output):
        mock_output.return_value = (
            b"interface: wg_shuttle\n"
            b"  public key: INTERFACE_PUB_KEY\n"
            b"  listening port: 51820\n"
            b"\n"
            b"peer: PEER_PUB_KEY_1\n"
            b"  endpoint: 1.2.3.4:51820\n"
            b"  allowed ips: 10.0.0.2/32\n"
            b"  latest handshake: 5 seconds ago\n"
            b"\n"
            b"peer: PEER_PUB_KEY_2\n"
            b"  endpoint: 5.6.7.8:51820\n"
            b"  allowed ips: 10.0.0.3/32\n"
        )
        result = tunnel_status("wg_shuttle")
        assert result["interface"] == "wg_shuttle"
        assert result["status"] == "up"
        assert result["public_key"] == "INTERFACE_PUB_KEY"
        assert result["listening_port"] == "51820"
        assert len(result["peers"]) == 2
        assert result["peers"][0]["public_key"] == "PEER_PUB_KEY_1"
        assert result["peers"][0]["endpoint"] == "1.2.3.4:51820"
        assert result["peers"][0]["allowed_ips"] == "10.0.0.2/32"
        assert result["peers"][1]["public_key"] == "PEER_PUB_KEY_2"

    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_output")
    @patch("data_shuttle_bridge.p2p.wireguard.shutil.which", return_value="/usr/bin/wg")
    def test_parses_output_with_no_peers(self, _mock_which, mock_output):
        mock_output.return_value = (
            b"interface: wg_shuttle\n"
            b"  public key: INTERFACE_PUB_KEY\n"
            b"  listening port: 51820\n"
        )
        result = tunnel_status()
        assert result["status"] == "up"
        assert result["peers"] == []


class TestWaitForPeer:
    @patch("data_shuttle_bridge.p2p.wireguard._detect_os", return_value="macos")
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_call")
    def test_returns_true_on_immediate_success(self, mock_call, _mock_os):
        assert wait_for_peer("10.0.0.2", timeout=5) is True
        mock_call.assert_called_once_with(
            ["ping", "-c", "1", "-W", "1", "10.0.0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch("data_shuttle_bridge.p2p.wireguard._detect_os", return_value="windows")
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_call")
    def test_uses_n_flag_on_windows(self, mock_call, _mock_os):
        wait_for_peer("10.0.0.2", timeout=5)
        mock_call.assert_called_once_with(
            ["ping", "-n", "1", "-W", "1", "10.0.0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch("data_shuttle_bridge.p2p.wireguard.time.monotonic")
    @patch("data_shuttle_bridge.p2p.wireguard.time.sleep")
    @patch("data_shuttle_bridge.p2p.wireguard._detect_os", return_value="linux")
    @patch(
        "data_shuttle_bridge.p2p.wireguard.subprocess.check_call",
        side_effect=subprocess.CalledProcessError(1, "ping"),
    )
    def test_returns_false_on_timeout(
        self, _mock_call, _mock_os, _mock_sleep, mock_mono
    ):
        # Simulate time progressing past the deadline
        mock_mono.side_effect = [0.0, 0.0, 5.0]
        assert wait_for_peer("10.0.0.2", timeout=2) is False

    @patch("data_shuttle_bridge.p2p.wireguard.time.monotonic")
    @patch("data_shuttle_bridge.p2p.wireguard.time.sleep")
    @patch("data_shuttle_bridge.p2p.wireguard._detect_os", return_value="linux")
    @patch("data_shuttle_bridge.p2p.wireguard.subprocess.check_call")
    def test_retries_then_succeeds(self, mock_call, _mock_os, _mock_sleep, mock_mono):
        # First ping fails, second succeeds
        mock_call.side_effect = [
            subprocess.CalledProcessError(1, "ping"),
            None,
        ]
        # monotonic: deadline calc, first check, second check
        mock_mono.side_effect = [0.0, 0.0, 1.0, 1.5]
        assert wait_for_peer("10.0.0.2", timeout=5) is True
        assert mock_call.call_count == 2


# ============================================================================
# Phase 2: STUN & NAT
# ============================================================================


class TestSTUN:
    def test_build_stun_request_format(self):
        pkt, txn_id = _build_stun_request()
        assert len(pkt) == 20
        assert len(txn_id) == 12
        msg_type, msg_len, cookie = struct.unpack_from("!HHI", pkt, 0)
        assert msg_type == 0x0001  # Binding Request
        assert msg_len == 0
        assert cookie == STUN_MAGIC_COOKIE

    def test_parse_stun_xor_mapped_address(self):
        """Build a synthetic STUN response with XOR-MAPPED-ADDRESS."""
        txn_id = b"\x01" * 12
        # XOR-MAPPED-ADDRESS for 203.0.113.5:12345
        ip_int = int.from_bytes(bytes([203, 0, 113, 5]), "big")
        xored_ip = ip_int ^ STUN_MAGIC_COOKIE
        xored_port = 12345 ^ (STUN_MAGIC_COOKIE >> 16)

        attr_value = struct.pack("!BBH I", 0, 0x01, xored_port, xored_ip)
        attr = (
            struct.pack("!HH", STUN_ATTR_XOR_MAPPED_ADDRESS, len(attr_value))
            + attr_value
        )

        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, len(attr), STUN_MAGIC_COOKIE, txn_id
        )
        response = header + attr

        result = _parse_stun_response(response, txn_id)
        assert result is not None
        assert result == ("203.0.113.5", 12345)

    def test_parse_stun_wrong_txn_id(self):
        txn_id = b"\x01" * 12
        wrong_txn = b"\x02" * 12
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, 0, STUN_MAGIC_COOKIE, wrong_txn
        )
        result = _parse_stun_response(header, txn_id)
        assert result is None

    def test_parse_stun_too_short(self):
        result = _parse_stun_response(b"\x00" * 10, b"\x01" * 12)
        assert result is None


class TestNATTraversal:
    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=("1.2.3.4", 51820))
    def test_resolve_upnp_first(self, mock_fwd):
        ep = resolve_public_endpoint(51820)
        assert ep.method == "upnp"
        assert ep.public_ip == "1.2.3.4"

    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=None)
    @patch(
        "data_shuttle_bridge.p2p.nat.discover_endpoint_multi",
        return_value=("5.6.7.8", 51820),
    )
    @patch("data_shuttle_bridge.p2p.nat.detect_nat_type", return_value=NAT_FULL_CONE)
    def test_resolve_stun_fallback(self, mock_nat, mock_stun, mock_fwd):
        ep = resolve_public_endpoint(51820)
        assert ep.method == "stun"
        assert ep.public_ip == "5.6.7.8"
        assert ep.nat_type == NAT_FULL_CONE

    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=None)
    @patch("data_shuttle_bridge.p2p.nat.discover_endpoint_multi", return_value=None)
    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    def test_resolve_manual_fallback(self, mock_ip, mock_stun, mock_fwd):
        ep = resolve_public_endpoint(51820)
        assert ep.method == "manual"
        assert ep.public_ip == "192.168.1.100"


# ============================================================================
# Phase 3: Invite / Join
# ============================================================================


class TestTokenEncoding:
    def test_encode_decode_roundtrip(self):
        payload = {"key": "value", "num": 42}
        token = _encode_token(payload)
        assert isinstance(token, str)
        decoded = _decode_token(token)
        assert decoded == payload

    def test_token_is_url_safe(self):
        payload = {"pk": "abc+/123==", "ip": "1.2.3.4"}
        token = _encode_token(payload)
        # Should not contain characters that break URLs/copy-paste
        assert "+" not in token
        assert "/" not in token


class TestInviteFlow:
    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_create_invite_produces_valid_token(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.2.3.4",
            public_port=51820,
            method="stun",
            nat_type=NAT_FULL_CONE,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            token = create_invite(listen_port=51820, sync_port=5000, config_dir=tmpdir)
            decoded = _decode_token(token)
            assert decoded["v"] == 1
            assert decoded["ip"] == "1.2.3.4"
            assert decoded["sp"] == 5000
            assert decoded["vip"] == INVITER_VIRTUAL_IP
            assert "pk" in decoded

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_full_invite_join_complete_flow(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="5.6.7.8",
            public_port=51820,
            method="stun",
            nat_type=NAT_FULL_CONE,
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            # Peer A creates invite
            invite_token = create_invite(config_dir=dir_a)

            # Peer B accepts
            config_path_b, response_token, warning = accept_invite(
                invite_token, config_dir=dir_b
            )
            assert os.path.exists(config_path_b)
            assert response_token
            assert not warning  # No symmetric NAT

            # Peer A completes
            config_path_a, warning = complete_invite(response_token, config_dir=dir_a)
            assert os.path.exists(config_path_a)

            # Verify configs reference each other's keys
            with open(config_path_a) as f:
                cfg_a = f.read()
            with open(config_path_b) as f:
                cfg_b = f.read()

            id_a = WireGuardIdentity.load(os.path.join(dir_a, "identity.json"))
            id_b = WireGuardIdentity.load(os.path.join(dir_b, "identity.json"))

            # Peer A's config should have Peer B's public key as [Peer]
            assert id_b.public_key in cfg_a
            # Peer B's config should have Peer A's public key as [Peer]
            assert id_a.public_key in cfg_b

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_symmetric_nat_warning(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.2.3.4",
            public_port=51820,
            method="stun",
            nat_type=NAT_SYMMETRIC,
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(config_dir=dir_a)
            _cfg, _resp, warning = accept_invite(invite_token, config_dir=dir_b)
            assert "symmetric" in warning.lower() or "Symmetric" in warning


class TestIPAllocation:
    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_inviter_gets_10_0_0_1_joiner_gets_10_0_0_2(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(config_dir=dir_a)
            decoded = _decode_token(invite_token)
            assert decoded["vip"] == "10.0.0.1"

            config_path_b, response_token, _ = accept_invite(
                invite_token, config_dir=dir_b
            )
            resp = _decode_token(response_token)
            assert resp["vip"] == "10.0.0.2"


# ============================================================================
# Phase 5: WireGuardPeerTransport (mocked)
# ============================================================================


class TestWireGuardPeerTransport:
    @pytest.fixture(autouse=True)
    def _import_transport(self):
        from data_shuttle_bridge.p2p.transport_wireguard import WireGuardPeerTransport

        self.TransportClass = WireGuardPeerTransport

    def test_delegates_to_http_transport(self):
        transport = self.TransportClass(peer_virtual_ip="10.0.0.1", sync_port=5000)
        assert transport._http.base_url == "http://10.0.0.1:5000"

    def test_context_manager_without_tunnel_management(self):
        transport = self.TransportClass(peer_virtual_ip="10.0.0.1", manage_tunnel=False)
        with transport as t:
            assert t is transport

    @patch("data_shuttle_bridge.p2p.transport_wireguard.tunnel_up")
    @patch(
        "data_shuttle_bridge.p2p.transport_wireguard.wait_for_peer", return_value=True
    )
    @patch("data_shuttle_bridge.p2p.transport_wireguard.tunnel_down")
    @patch("data_shuttle_bridge.p2p.transport_wireguard.remove_upnp_forward")
    def test_context_manager_with_tunnel_management(
        self, mock_upnp, mock_down, mock_wait, mock_up
    ):
        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path="/tmp/test.conf",
        )
        with transport:
            mock_up.assert_called_once_with("/tmp/test.conf")
            mock_wait.assert_called_once()
        mock_down.assert_called_once()

    @patch("data_shuttle_bridge.p2p.transport_wireguard.tunnel_up")
    @patch(
        "data_shuttle_bridge.p2p.transport_wireguard.wait_for_peer", return_value=False
    )
    def test_health_check_failure_raises(self, mock_wait, mock_up):
        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path="/tmp/test.conf",
            health_check_timeout=1,
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            transport.bring_up()

    def test_bring_up_without_config_raises(self):
        """Test bring_up raises RuntimeError when wg_config_path not set (line 65)."""
        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path=None,
        )
        with pytest.raises(RuntimeError, match="wg_config_path required"):
            transport.bring_up()

    @patch("data_shuttle_bridge.p2p.transport_wireguard.remove_upnp_forward")
    def test_bring_down_without_active_tunnel(self, mock_upnp):
        """Test bring_down when tunnel is not active (line 78->exit)."""
        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            wg_config_path="/tmp/test.conf",
            cleanup_upnp=True,
        )
        # tunnel_active is False by default
        transport.bring_down()
        # tunnel_down should NOT be called, but upnp cleanup should
        mock_upnp.assert_called_once()

    @patch("data_shuttle_bridge.p2p.transport_wireguard.remove_upnp_forward")
    def test_bring_down_no_upnp_cleanup(self, mock_upnp):
        """Test bring_down with cleanup_upnp=False."""
        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            cleanup_upnp=False,
        )
        transport.bring_down()
        mock_upnp.assert_not_called()

    def test_get_changes_since_delegates(self):
        """Test get_changes_since delegates to HTTP transport (line 50)."""
        transport = self.TransportClass(peer_virtual_ip="10.0.0.1")
        transport._http = MagicMock()
        transport._http.get_changes_since.return_value = [{"id": 1}]
        result = transport.get_changes_since(0, limit=100, exclude_node_id="node1")
        transport._http.get_changes_since.assert_called_once_with(
            0, limit=100, exclude_node_id="node1"
        )
        assert result == [{"id": 1}]

    def test_apply_changes_delegates(self):
        """Test apply_changes delegates to HTTP transport (line 55)."""
        transport = self.TransportClass(peer_virtual_ip="10.0.0.1")
        transport._http = MagicMock()
        changes = [{"id": 1}]
        transport.apply_changes(changes)
        transport._http.apply_changes.assert_called_once_with(changes)

    def test_ack_delegates(self):
        """Test ack delegates to HTTP transport (line 58)."""
        transport = self.TransportClass(peer_virtual_ip="10.0.0.1")
        transport._http = MagicMock()
        transport.ack(42)
        transport._http.ack.assert_called_once_with(42)

    @patch("data_shuttle_bridge.p2p.transport_wireguard.tunnel_up")
    @patch(
        "data_shuttle_bridge.p2p.transport_wireguard.wait_for_peer", return_value=True
    )
    @patch("data_shuttle_bridge.p2p.transport_wireguard.tunnel_down")
    @patch("data_shuttle_bridge.p2p.transport_wireguard.remove_upnp_forward")
    def test_async_context_manager(self, mock_upnp, mock_down, mock_wait, mock_up):
        """Test async context manager (lines 95-101)."""
        import asyncio

        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path="/tmp/test.conf",
        )

        async def _run():
            async with transport as t:
                assert t is transport
                mock_up.assert_called_once()
            mock_down.assert_called_once()

        asyncio.run(_run())

    def test_async_context_manager_no_tunnel(self):
        """Test async context manager without tunnel management."""
        import asyncio

        transport = self.TransportClass(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=False,
        )

        async def _run():
            async with transport as t:
                assert t is transport

        asyncio.run(_run())
