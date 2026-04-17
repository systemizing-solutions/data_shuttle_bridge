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
# Phase 5: TunnelPeerTransport + WireGuardTunnelStrategy (mocked)
# ============================================================================


class TestWireGuardTunnelStrategy:
    @pytest.fixture(autouse=True)
    def _import_tunnel(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        self.TunnelPeerTransport = TunnelPeerTransport
        self.WireGuardTunnelStrategy = WireGuardTunnelStrategy

    def _make_transport(self, **kwargs):
        manage_tunnel = kwargs.pop("manage_tunnel", True)
        return self.TunnelPeerTransport.from_config(
            "wireguard", manage_tunnel=manage_tunnel, **kwargs
        )

    def test_delegates_to_http_transport(self):
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1", sync_port=5000, manage_tunnel=False
        )
        assert transport._http.base_url == "http://10.0.0.1:5000"

    def test_context_manager_without_tunnel_management(self):
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1", manage_tunnel=False
        )
        with transport as t:
            assert t is transport

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.tunnel_up")
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.wait_for_peer", return_value=True)
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.tunnel_down")
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.remove_upnp_forward")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_wireguard.WireGuardTunnelStrategy.is_installed",
        return_value=True,
    )
    def test_context_manager_with_tunnel_management(
        self, _mock_installed, mock_upnp, mock_down, mock_wait, mock_up
    ):
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path="/tmp/test.conf",
        )
        with transport:
            mock_up.assert_called_once_with("/tmp/test.conf")
            mock_wait.assert_called_once()
        mock_down.assert_called_once()

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.tunnel_up")
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.wait_for_peer", return_value=False)
    @patch(
        "data_shuttle_bridge.p2p.tunnel_wireguard.WireGuardTunnelStrategy.is_installed",
        return_value=True,
    )
    def test_health_check_failure_raises(self, _mock_installed, mock_wait, mock_up):
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path="/tmp/test.conf",
            health_check_timeout=1,
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            transport.bring_up()

    @patch(
        "data_shuttle_bridge.p2p.tunnel_wireguard.WireGuardTunnelStrategy.is_installed",
        return_value=True,
    )
    def test_bring_up_without_config_raises(self, _mock_installed):
        """Test bring_up raises RuntimeError when wg_config_path not set."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=True,
            wg_config_path=None,
        )
        with pytest.raises(RuntimeError, match="wg_config_path required"):
            transport.bring_up()

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.remove_upnp_forward")
    def test_bring_down_without_active_tunnel(self, mock_upnp):
        """Test bring_down when tunnel is not active."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            wg_config_path="/tmp/test.conf",
            cleanup_upnp=True,
            manage_tunnel=False,
        )
        transport.bring_down()
        # tunnel_down should NOT be called, but upnp cleanup should
        mock_upnp.assert_called_once()

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.remove_upnp_forward")
    def test_bring_down_no_upnp_cleanup(self, mock_upnp):
        """Test bring_down with cleanup_upnp=False."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            cleanup_upnp=False,
            manage_tunnel=False,
        )
        transport.bring_down()
        mock_upnp.assert_not_called()

    def test_get_changes_since_delegates(self):
        """Test get_changes_since delegates to HTTP transport."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1", manage_tunnel=False
        )
        transport._http = MagicMock()
        transport._http.get_changes_since.return_value = [{"id": 1}]
        result = transport.get_changes_since(0, limit=100, exclude_node_id="node1")
        transport._http.get_changes_since.assert_called_once_with(
            0, limit=100, exclude_node_id="node1"
        )
        assert result == [{"id": 1}]

    def test_apply_changes_delegates(self):
        """Test apply_changes delegates to HTTP transport."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1", manage_tunnel=False
        )
        transport._http = MagicMock()
        changes = [{"id": 1}]
        transport.apply_changes(changes)
        transport._http.apply_changes.assert_called_once_with(changes)

    def test_ack_delegates(self):
        """Test ack delegates to HTTP transport."""
        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1", manage_tunnel=False
        )
        transport._http = MagicMock()
        transport.ack(42)
        transport._http.ack.assert_called_once_with(42)

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.tunnel_up")
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.wait_for_peer", return_value=True)
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.tunnel_down")
    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.remove_upnp_forward")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_wireguard.WireGuardTunnelStrategy.is_installed",
        return_value=True,
    )
    def test_async_context_manager(
        self, _mock_installed, mock_upnp, mock_down, mock_wait, mock_up
    ):
        """Test async context manager."""
        import asyncio

        transport = self._make_transport(
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

        transport = self._make_transport(
            peer_virtual_ip="10.0.0.1",
            manage_tunnel=False,
        )

        async def _run():
            async with transport as t:
                assert t is transport

        asyncio.run(_run())


# ============================================================================
# Phase 5b: TunnelPeerTransport.from_config & strategy registry
# ============================================================================


class TestTunnelFromConfig:
    def test_from_config_wireguard(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        transport = TunnelPeerTransport.from_config(
            "wireguard", peer_virtual_ip="10.0.0.1", sync_port=5000, manage_tunnel=False
        )
        assert isinstance(transport.strategy, WireGuardTunnelStrategy)
        assert transport._http.base_url == "http://10.0.0.1:5000"

    def test_from_config_cloudflared(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport
        from data_shuttle_bridge.p2p.tunnel_cloudflared import CloudflaredTunnelStrategy

        transport = TunnelPeerTransport.from_config(
            "cloudflared", local_port=8080, manage_tunnel=False
        )
        assert isinstance(transport.strategy, CloudflaredTunnelStrategy)

    def test_from_config_ngrok(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        transport = TunnelPeerTransport.from_config(
            "ngrok", local_port=9000, manage_tunnel=False
        )
        assert isinstance(transport.strategy, NgrokTunnelStrategy)

    def test_from_config_unknown_raises(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport

        with pytest.raises(ValueError, match="Unknown tunnel strategy"):
            TunnelPeerTransport.from_config("nonexistent")

    def test_from_config_defaults_to_wireguard(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport, DEFAULT_STRATEGY
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        assert DEFAULT_STRATEGY == "wireguard"
        transport = TunnelPeerTransport.from_config(
            peer_virtual_ip="10.0.0.2", manage_tunnel=False
        )
        assert isinstance(transport.strategy, WireGuardTunnelStrategy)

    def test_list_tunnel_strategies(self):
        from data_shuttle_bridge.p2p.tunnel import list_tunnel_strategies

        strategies = list_tunnel_strategies()
        assert "wireguard" in strategies
        assert "cloudflared" in strategies
        assert "ngrok" in strategies

    def test_get_tunnel_strategy_class(self):
        from data_shuttle_bridge.p2p.tunnel import get_tunnel_strategy_class
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy
        from data_shuttle_bridge.p2p.tunnel_cloudflared import CloudflaredTunnelStrategy
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        assert get_tunnel_strategy_class("wireguard") is WireGuardTunnelStrategy
        assert get_tunnel_strategy_class("cloudflared") is CloudflaredTunnelStrategy
        assert get_tunnel_strategy_class("ngrok") is NgrokTunnelStrategy


# ============================================================================
# Phase 5c: CloudflaredTunnelStrategy
# ============================================================================


class TestCloudflaredTunnelStrategy:
    @pytest.fixture()
    def strategy(self):
        from data_shuttle_bridge.p2p.tunnel_cloudflared import CloudflaredTunnelStrategy

        return CloudflaredTunnelStrategy(
            local_port=9000, binary="cloudflared", startup_timeout=10
        )

    # -- class attributes --------------------------------------------------

    def test_class_attributes(self, strategy):
        assert strategy.name == "cloudflared"
        assert strategy.signup_url is None
        assert strategy._binary_name == "cloudflared"

    def test_default_constructor(self):
        from data_shuttle_bridge.p2p.tunnel_cloudflared import CloudflaredTunnelStrategy

        s = CloudflaredTunnelStrategy()
        assert s._local_port == 5000
        assert s._binary == "cloudflared"
        assert s._startup_timeout == 30

    def test_custom_constructor(self, strategy):
        assert strategy._local_port == 9000
        assert strategy._binary == "cloudflared"
        assert strategy._startup_timeout == 10

    # -- URL regex ---------------------------------------------------------

    def test_url_regex_matches_trycloudflare(self):
        from data_shuttle_bridge.p2p.tunnel_cloudflared import _URL_RE

        line = "INF |  https://some-random-name.trycloudflare.com"
        m = _URL_RE.search(line)
        assert m is not None
        assert m.group(1) == "https://some-random-name.trycloudflare.com"

    def test_url_regex_no_match(self):
        from data_shuttle_bridge.p2p.tunnel_cloudflared import _URL_RE

        assert _URL_RE.search("no url here") is None
        assert _URL_RE.search("https://example.com") is None

    # -- start() -----------------------------------------------------------

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy.ensure_ready"
    )
    @patch("data_shuttle_bridge.p2p.tunnel_cloudflared.subprocess.Popen")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._wait_for_url",
        return_value="https://my-tunnel.trycloudflare.com",
    )
    def test_start_returns_url(self, mock_wait, mock_popen, mock_ready, strategy):
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_popen.return_value = mock_proc

        url = strategy.start()
        assert url == "https://my-tunnel.trycloudflare.com"
        mock_ready.assert_called_once()
        mock_popen.assert_called_once_with(
            ["cloudflared", "tunnel", "--url", "http://localhost:9000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy.ensure_ready"
    )
    @patch("data_shuttle_bridge.p2p.tunnel_cloudflared.subprocess.Popen")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._wait_for_url",
        return_value="https://abc.trycloudflare.com",
    )
    def test_start_spawns_reader_thread(
        self, mock_wait, mock_popen, mock_ready, strategy
    ):
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_popen.return_value = mock_proc

        strategy.start()
        assert strategy._reader_thread is not None
        assert strategy._process is mock_proc

    # -- stop() ------------------------------------------------------------

    def test_stop_clears_state(self, strategy):
        strategy._process = MagicMock()
        strategy._process.wait.return_value = 0
        strategy._public_url = "https://x.trycloudflare.com"
        strategy._output_lines = ["line1"]
        strategy._reader_thread = MagicMock()

        strategy.stop()
        assert strategy._output_lines == []
        assert strategy._reader_thread is None
        assert strategy._public_url is None
        assert strategy._process is None

    def test_stop_terminates_process(self, strategy):
        mock_proc = MagicMock()
        strategy._process = mock_proc
        strategy._public_url = "https://x.trycloudflare.com"

        strategy.stop()
        mock_proc.terminate.assert_called_once()

    # -- _read_output() ----------------------------------------------------

    def test_read_output_accumulates_lines(self, strategy):
        strategy._process = MagicMock()
        strategy._process.stdout = [
            b"line one\n",
            b"line two\n",
            b"https://hello.trycloudflare.com\n",
        ]
        strategy._output_lines = []
        strategy._read_output()
        assert len(strategy._output_lines) == 3
        assert "line one\n" in strategy._output_lines[0]
        assert "trycloudflare" in strategy._output_lines[2]

    # -- _wait_for_url() ---------------------------------------------------

    @patch("time.monotonic")
    @patch("time.sleep")
    def test_wait_for_url_finds_url(self, mock_sleep, mock_mono, strategy):
        mock_mono.side_effect = [0.0, 0.5]
        strategy._output_lines = [
            "INF Starting tunnel\n",
            "INF https://my-test.trycloudflare.com\n",
        ]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None

        url = strategy._wait_for_url()
        assert url == "https://my-test.trycloudflare.com"

    @patch("time.monotonic")
    @patch("time.sleep")
    def test_wait_for_url_timeout_raises(self, mock_sleep, mock_mono, strategy):
        # monotonic returns values past the deadline immediately
        mock_mono.side_effect = [0.0, 100.0]
        strategy._output_lines = ["no url here\n"]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None

        with pytest.raises(RuntimeError, match="did not provide a URL"):
            strategy._wait_for_url()

    @patch("time.monotonic")
    @patch("time.sleep")
    def test_wait_for_url_process_exits_raises(self, mock_sleep, mock_mono, strategy):
        mock_mono.side_effect = [0.0, 0.5]
        strategy._output_lines = ["error: something failed\n"]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = 1  # process exited

        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            strategy._wait_for_url()

    # -- install() ---------------------------------------------------------

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_platform",
        return_value="darwin",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._run_install_cmd",
    )
    def test_install_darwin_uses_brew(self, mock_run, mock_plat, strategy):
        strategy.install()
        mock_run.assert_called_once_with(
            ["brew", "install", "cloudflared"], "cloudflared"
        )

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_platform",
        return_value="linux",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_arch",
        return_value="amd64",
    )
    @patch("urllib.request.urlretrieve")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._run_install_cmd",
    )
    @patch("os.unlink")
    def test_install_linux_amd64_bare_binary(
        self, mock_unlink, mock_run, mock_urlretrieve, mock_arch, mock_plat, strategy
    ):
        strategy.install()
        # Should download and install via sudo install
        assert mock_urlretrieve.call_count == 1
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "sudo"
        assert cmd[1] == "install"
        mock_unlink.assert_called_once()

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_platform",
        return_value="linux",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_arch",
        return_value="mips",
    )
    def test_install_unsupported_platform_raises(self, mock_arch, mock_plat, strategy):
        with pytest.raises(RuntimeError, match="No cloudflared binary available"):
            strategy.install()

    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_platform",
        return_value="windows",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_cloudflared.CloudflaredTunnelStrategy._current_arch",
        return_value="amd64",
    )
    @patch("urllib.request.urlretrieve")
    def test_install_windows_downloads_exe(
        self, mock_urlretrieve, mock_arch, mock_plat, strategy
    ):
        strategy.install()
        assert mock_urlretrieve.call_count == 1
        dest = mock_urlretrieve.call_args[0][1]
        assert dest.endswith("cloudflared.exe")

    # -- is_active property ------------------------------------------------

    def test_is_active_false_by_default(self, strategy):
        assert strategy.is_active is False

    def test_is_active_true_when_process_running(self, strategy):
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None
        assert strategy.is_active is True

    def test_is_active_false_when_process_exited(self, strategy):
        strategy._process = MagicMock()
        strategy._process.poll.return_value = 0
        assert strategy.is_active is False


# ============================================================================
# Phase 6: Additional invite.py coverage
# ============================================================================


class TestInviteManualEndpoint:
    """Cover manual endpoint parsing and PSK branches in invite functions."""

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_create_invite_with_manual_endpoint_with_port(self, mock_resolve):
        """create_invite with endpoint='1.2.3.4:9999' uses manual EndpointInfo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token = create_invite(endpoint="1.2.3.4:9999", config_dir=tmpdir)
            decoded = _decode_token(token)
            assert decoded["ip"] == "1.2.3.4"
            assert decoded["pp"] == 9999
            assert decoded["m"] == "manual"
            mock_resolve.assert_not_called()

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_create_invite_with_manual_endpoint_no_port(self, mock_resolve):
        """create_invite with endpoint='1.2.3.4' (no colon) uses listen_port."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token = create_invite(
                endpoint="1.2.3.4", listen_port=51820, config_dir=tmpdir
            )
            decoded = _decode_token(token)
            assert decoded["ip"] == "1.2.3.4"
            assert decoded["pp"] == 51820
            mock_resolve.assert_not_called()

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_create_invite_with_psk(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            token = create_invite(psk="my-secret-psk", config_dir=tmpdir)
            decoded = _decode_token(token)
            assert decoded["psk"] == "my-secret-psk"

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_accept_invite_with_manual_endpoint(self, mock_resolve):
        """accept_invite with endpoint='2.3.4.5:4321' parses correctly."""
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(config_dir=dir_a)
            config_path, response_token, _ = accept_invite(
                invite_token, endpoint="2.3.4.5:4321", config_dir=dir_b
            )
            resp = _decode_token(response_token)
            assert resp["ip"] == "2.3.4.5"
            assert resp["pp"] == 4321
            assert resp["m"] == "manual"

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_accept_invite_with_manual_endpoint_no_port(self, mock_resolve):
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(config_dir=dir_a)
            config_path, response_token, _ = accept_invite(
                invite_token, endpoint="2.3.4.5", config_dir=dir_b
            )
            resp = _decode_token(response_token)
            assert resp["ip"] == "2.3.4.5"
            assert resp["pp"] == 51820  # default listen_port

    def test_accept_invite_invalid_version(self):
        """accept_invite raises ValueError on unsupported version."""
        bad_token = _encode_token({"v": 99, "pk": "x"})
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Unsupported invite version"):
                accept_invite(bad_token, config_dir=tmpdir)

    def test_complete_invite_invalid_version(self):
        """complete_invite raises ValueError on unsupported version."""
        bad_token = _encode_token({"v": 42, "pk": "x"})
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Unsupported response version"):
                complete_invite(bad_token, config_dir=tmpdir)

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_complete_invite_symmetric_nat_warning(self, mock_resolve):
        """complete_invite warns when Peer B has symmetric NAT."""
        mock_resolve.return_value = EndpointInfo(
            public_ip="5.6.7.8",
            public_port=51820,
            method="stun",
            nat_type=NAT_FULL_CONE,
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(config_dir=dir_a)
            _, response_token, _ = accept_invite(invite_token, config_dir=dir_b)
            # Manually inject symmetric NAT into the response token
            resp_data = _decode_token(response_token)
            resp_data["nt"] = NAT_SYMMETRIC
            modified_token = _encode_token(resp_data)
            _, warning = complete_invite(modified_token, config_dir=dir_a)
            assert "symmetric" in warning.lower() or "Symmetric" in warning

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_accept_invite_psk_in_response(self, mock_resolve):
        """accept_invite includes PSK in response token when invite has PSK."""
        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            invite_token = create_invite(psk="shared-secret", config_dir=dir_a)
            _, response_token, _ = accept_invite(invite_token, config_dir=dir_b)
            resp = _decode_token(response_token)
            assert resp["psk"] == "shared-secret"

    @patch("data_shuttle_bridge.p2p.invite.resolve_public_endpoint")
    def test_load_peers_existing_file(self, mock_resolve):
        """_load_peers reads an existing peers.json file."""
        from data_shuttle_bridge.p2p.invite import _load_peers, _save_peers

        mock_resolve.return_value = EndpointInfo(
            public_ip="1.1.1.1", public_port=51820, method="stun"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a peers file with pre-existing data
            _save_peers(tmpdir, {"peers": [{"role": "test"}], "next_ip_octet": 5})
            loaded = _load_peers(tmpdir)
            assert loaded["peers"] == [{"role": "test"}]
            assert loaded["next_ip_octet"] == 5


# ============================================================================
# Phase 7: Additional tunnel.py coverage
# ============================================================================


class TestTunnelStrategyEnsureReady:
    """Cover ensure_ready branches, _log, and base class methods."""

    def test_log_prints_to_stderr(self, capsys):
        from data_shuttle_bridge.p2p.tunnel import _log

        _log("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.err

    def test_base_url_returns_none(self):
        """TunnelStrategy.url returns None by default."""
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        # For a strategy with known URL, test that base class default is None
        # We need a concrete subclass without url override
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        s = NgrokTunnelStrategy(local_port=5000)
        # url comes from SubprocessTunnelStrategy, returns _public_url which is None
        assert s.url is None

    def test_base_is_installed_returns_true(self):
        """TunnelStrategy.is_installed returns True by default."""
        # Create a minimal concrete strategy to test the base method
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_test"

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

        s = DummyStrategy()
        assert s.is_installed() is True

    def test_base_install_raises_not_implemented(self):
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_install"

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

        s = DummyStrategy()
        with pytest.raises(
            NotImplementedError, match="Auto-install is not implemented"
        ):
            s.install()

    def test_ensure_ready_auto_install_with_signup_url(self):
        """ensure_ready opens browser when signup_url is set after install."""
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_signup"
            signup_url = "https://example.com/signup"
            _installed = False

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

            def is_installed(self):
                return self._installed

            def install(self):
                self._installed = True

        s = DummyStrategy()
        with patch("data_shuttle_bridge.p2p.tunnel.webbrowser.open") as mock_open:
            s.ensure_ready(auto_install=True)
            mock_open.assert_called_once_with("https://example.com/signup")

    def test_ensure_ready_auto_install_signup_url_browser_error(self):
        """ensure_ready handles browser open failure gracefully."""
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_browser_err"
            signup_url = "https://example.com/signup"
            _installed = False

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

            def is_installed(self):
                return self._installed

            def install(self):
                self._installed = True

        s = DummyStrategy()
        with patch(
            "data_shuttle_bridge.p2p.tunnel.webbrowser.open",
            side_effect=Exception("no browser"),
        ):
            s.ensure_ready(auto_install=True)  # should not raise

    def test_ensure_ready_auto_install_still_not_found(self):
        """ensure_ready raises when install completes but binary still not found."""
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_notfound"

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

            def is_installed(self):
                return False  # always not found

            def install(self):
                pass  # does nothing

        s = DummyStrategy()
        with pytest.raises(
            RuntimeError, match="auto-install finished but binary still not found"
        ):
            s.ensure_ready(auto_install=True)

    def test_ensure_ready_no_auto_install_raises(self):
        """ensure_ready with auto_install=False raises when not installed."""
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_noauto"

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

            def is_installed(self):
                return False

        s = DummyStrategy()
        with pytest.raises(RuntimeError, match="binary not found"):
            s.ensure_ready(auto_install=False)

    def test_ensure_ready_no_auto_install_with_signup_url(self):
        """ensure_ready with auto_install=False includes signup_url in message."""
        from data_shuttle_bridge.p2p.tunnel import TunnelStrategy

        class DummyStrategy(TunnelStrategy):
            name = "dummy_noauto_signup"
            signup_url = "https://example.com"

            def start(self):
                return ""

            def stop(self):
                pass

            @property
            def is_active(self):
                return False

            def is_installed(self):
                return False

        s = DummyStrategy()
        with pytest.raises(RuntimeError, match="Sign up / download at"):
            s.ensure_ready(auto_install=False)


class TestSubprocessTunnelStrategyHelpers:
    """Cover SubprocessTunnelStrategy: stop with TimeoutExpired, platform helpers, etc."""

    def test_stop_with_timeout_expired(self):
        """stop() kills the process when terminate times out."""
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        s = NgrokTunnelStrategy(local_port=5000)
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("ngrok", 5)
        s._process = mock_proc
        s._public_url = "http://example.com"
        s.stop()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert s._process is None
        assert s._public_url is None

    def test_is_installed_false_when_binary_missing(self):
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        s = NgrokTunnelStrategy(local_port=5000, binary="nonexistent_binary_xyz")
        s._binary_name = "nonexistent_binary_xyz"
        assert s.is_installed() is False

    @patch("data_shuttle_bridge.p2p.tunnel.platform.system", return_value="Windows")
    def test_current_platform_windows(self, _):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        assert SubprocessTunnelStrategy._current_platform() == "windows"

    @patch("data_shuttle_bridge.p2p.tunnel.platform.system", return_value="SomeOS")
    def test_current_platform_defaults_to_linux(self, _):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        assert SubprocessTunnelStrategy._current_platform() == "linux"

    @patch("data_shuttle_bridge.p2p.tunnel.platform.machine", return_value="aarch64")
    def test_current_arch_arm64(self, _):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        assert SubprocessTunnelStrategy._current_arch() == "arm64"

    @patch("data_shuttle_bridge.p2p.tunnel.platform.machine", return_value="riscv64")
    def test_current_arch_fallback(self, _):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        assert SubprocessTunnelStrategy._current_arch() == "riscv64"

    @patch("data_shuttle_bridge.p2p.tunnel.subprocess.run")
    def test_run_install_cmd_failure(self, mock_run):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        mock_run.return_value = MagicMock(returncode=1, stdout="out", stderr="err")
        with pytest.raises(RuntimeError, match="Failed to install"):
            SubprocessTunnelStrategy._run_install_cmd(["false"], "test-tool")

    def test_check_binary_found(self):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        # python should be on PATH
        SubprocessTunnelStrategy._check_binary("python", "python3")

    def test_check_binary_not_found(self):
        from data_shuttle_bridge.p2p.tunnel import SubprocessTunnelStrategy

        with pytest.raises(RuntimeError, match="binary not found"):
            SubprocessTunnelStrategy._check_binary("test", "nonexistent_binary_xyz")

    def test_register_tunnel_strategy_empty_name(self):
        from data_shuttle_bridge.p2p.tunnel import (
            register_tunnel_strategy,
            TunnelStrategy,
        )

        with pytest.raises(ValueError, match="must set a non-empty 'name'"):

            @register_tunnel_strategy
            class BadStrategy(TunnelStrategy):
                name = ""

                def start(self):
                    return ""

                def stop(self):
                    pass

                @property
                def is_active(self):
                    return False

    def test_ensure_http_raises_when_not_started(self):
        """_ensure_http raises RuntimeError when tunnel not started."""
        from data_shuttle_bridge.p2p.tunnel import TunnelPeerTransport
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        strategy = NgrokTunnelStrategy(local_port=5000)
        transport = TunnelPeerTransport(strategy, manage_tunnel=False)
        with pytest.raises(RuntimeError, match="Tunnel not started"):
            transport.get_changes_since(0)


# ============================================================================
# Phase 8: Additional tunnel_wireguard.py coverage
# ============================================================================


class TestWireGuardTunnelStrategyInstall:
    """Cover WireGuardTunnelStrategy.install(), is_installed(), is_active."""

    @patch("data_shuttle_bridge.p2p.tunnel_wireguard.shutil.which", return_value=None)
    def test_is_installed_false(self, mock_which):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        assert s.is_installed() is False
        mock_which.assert_called_with("wg")

    @patch(
        "data_shuttle_bridge.p2p.tunnel_wireguard.shutil.which",
        return_value="/usr/bin/wg",
    )
    def test_is_installed_true(self, mock_which):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        assert s.is_installed() is True

    @patch("data_shuttle_bridge.p2p.tunnel.SubprocessTunnelStrategy._run_install_cmd")
    @patch("platform.system", return_value="Darwin")
    def test_install_darwin(self, mock_sys, mock_run):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        s.install()
        mock_run.assert_called_once_with(
            ["brew", "install", "wireguard-tools"], "wireguard-tools"
        )

    @patch("data_shuttle_bridge.p2p.tunnel.SubprocessTunnelStrategy._run_install_cmd")
    @patch("platform.system", return_value="Linux")
    def test_install_linux(self, mock_sys, mock_run):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        s.install()
        mock_run.assert_called_once_with(
            ["sudo", "apt-get", "install", "-y", "wireguard"], "wireguard-tools"
        )

    @patch("platform.system", return_value="Windows")
    def test_install_unsupported_platform(self, mock_sys):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        with pytest.raises(RuntimeError, match="Auto-install not supported"):
            s.install()

    def test_is_active_property(self):
        from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy

        s = WireGuardTunnelStrategy(peer_virtual_ip="10.0.0.1")
        assert s.is_active is False
        s._active = True
        assert s.is_active is True


# ============================================================================
# Phase 9: Additional tunnel_ngrok.py coverage
# ============================================================================


class TestNgrokTunnelStrategy:
    @pytest.fixture()
    def strategy(self):
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        return NgrokTunnelStrategy(
            local_port=8080,
            authtoken="test-token",
            domain="my.domain.com",
            binary="ngrok",
            api_port=4040,
            startup_timeout=10,
        )

    # -- constructor -------------------------------------------------------

    def test_constructor_defaults(self):
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        s = NgrokTunnelStrategy()
        assert s._local_port == 5000
        assert s._authtoken is None
        assert s._domain is None
        assert s._binary == "ngrok"
        assert s._api_port == 4040
        assert s._startup_timeout == 15

    def test_constructor_custom(self, strategy):
        assert strategy._local_port == 8080
        assert strategy._authtoken == "test-token"
        assert strategy._domain == "my.domain.com"
        assert strategy._startup_timeout == 10

    def test_class_attributes(self, strategy):
        assert strategy.name == "ngrok"
        assert strategy.signup_url is not None

    # -- install() ---------------------------------------------------------

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_platform",
        return_value="darwin",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._run_install_cmd",
    )
    def test_install_darwin_uses_brew(self, mock_run, mock_plat, strategy):
        strategy.install()
        mock_run.assert_called_once_with(
            ["brew", "install", "ngrok/ngrok/ngrok"], "ngrok"
        )

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_platform",
        return_value="linux",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_arch",
        return_value="amd64",
    )
    @patch("urllib.request.urlretrieve")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._run_install_cmd",
    )
    def test_install_linux_amd64_tgz(
        self, mock_run, mock_urlretrieve, mock_arch, mock_plat, strategy
    ):
        with patch("tarfile.open") as mock_tarfile:
            mock_tf = MagicMock()
            mock_tarfile.return_value.__enter__ = MagicMock(return_value=mock_tf)
            mock_tarfile.return_value.__exit__ = MagicMock(return_value=False)
            strategy.install()
            assert mock_urlretrieve.call_count == 1
            assert mock_run.call_count == 1
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "sudo"

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_platform",
        return_value="windows",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_arch",
        return_value="amd64",
    )
    @patch("urllib.request.urlretrieve")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._run_install_cmd",
    )
    def test_install_windows_zip(
        self, mock_run, mock_urlretrieve, mock_arch, mock_plat, strategy
    ):
        with patch("zipfile.ZipFile") as mock_zipfile:
            mock_zf = MagicMock()
            mock_zipfile.return_value.__enter__ = MagicMock(return_value=mock_zf)
            mock_zipfile.return_value.__exit__ = MagicMock(return_value=False)
            strategy.install()
            assert mock_urlretrieve.call_count == 1

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_platform",
        return_value="linux",
    )
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._current_arch",
        return_value="mips",
    )
    def test_install_unsupported_platform(self, mock_arch, mock_plat, strategy):
        with pytest.raises(RuntimeError, match="No ngrok binary available"):
            strategy.install()

    # -- start() -----------------------------------------------------------

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy.ensure_ready",
    )
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.subprocess.run")
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.subprocess.Popen")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._wait_for_url",
        return_value="https://abc123.ngrok.io",
    )
    def test_start_with_authtoken(
        self, mock_wait, mock_popen, mock_run, mock_ready, strategy
    ):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        url = strategy.start()
        assert url == "https://abc123.ngrok.io"
        mock_ready.assert_called_once()
        # authtoken should be configured
        mock_run.assert_called_once_with(
            ["ngrok", "config", "add-authtoken", "test-token"],
            check=True,
            capture_output=True,
        )
        # Popen should use --domain since strategy has domain set
        popen_cmd = mock_popen.call_args[0][0]
        assert "--domain" in popen_cmd
        assert "my.domain.com" in popen_cmd

    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy.ensure_ready",
    )
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.subprocess.Popen")
    @patch(
        "data_shuttle_bridge.p2p.tunnel_ngrok.NgrokTunnelStrategy._wait_for_url",
        return_value="https://xyz.ngrok.io",
    )
    def test_start_without_authtoken_or_domain(self, mock_wait, mock_popen, mock_ready):
        from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy

        s = NgrokTunnelStrategy(local_port=9000)
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        url = s.start()
        assert url == "https://xyz.ngrok.io"
        # No subprocess.run call for authtoken
        popen_cmd = mock_popen.call_args[0][0]
        assert popen_cmd == ["ngrok", "http", "9000"]
        assert "--domain" not in popen_cmd

    # -- _wait_for_url() ---------------------------------------------------

    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.monotonic")
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.sleep")
    def test_wait_for_url_success(self, mock_sleep, mock_mono, strategy):
        mock_mono.side_effect = [0.0, 0.5, 1.0]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tunnels": [{"public_url": "https://test123.ngrok.io"}]
        }
        with patch("requests.get", return_value=mock_resp):
            url = strategy._wait_for_url()
            assert url == "https://test123.ngrok.io"

    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.monotonic")
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.sleep")
    def test_wait_for_url_timeout(self, mock_sleep, mock_mono, strategy):
        mock_mono.side_effect = [0.0, 100.0]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None

        with patch("requests.get", side_effect=Exception("connection refused")):
            with pytest.raises(RuntimeError, match="did not expose a URL"):
                strategy._wait_for_url()

    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.monotonic")
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.sleep")
    def test_wait_for_url_process_exits(self, mock_sleep, mock_mono, strategy):
        mock_mono.side_effect = [0.0, 0.5]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = 1
        strategy._process.returncode = 1

        with pytest.raises(RuntimeError, match="ngrok exited unexpectedly"):
            strategy._wait_for_url()

    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.monotonic")
    @patch("data_shuttle_bridge.p2p.tunnel_ngrok.time.sleep")
    def test_wait_for_url_empty_tunnels_then_success(
        self, mock_sleep, mock_mono, strategy
    ):
        """API responds but with empty tunnels list, then succeeds."""
        mock_mono.side_effect = [0.0, 0.5, 1.0, 1.5]
        strategy._process = MagicMock()
        strategy._process.poll.return_value = None

        mock_resp_empty = MagicMock()
        mock_resp_empty.json.return_value = {"tunnels": []}
        mock_resp_ok = MagicMock()
        mock_resp_ok.json.return_value = {
            "tunnels": [{"public_url": "https://found.ngrok.io"}]
        }
        with patch("requests.get", side_effect=[mock_resp_empty, mock_resp_ok]):
            url = strategy._wait_for_url()
            assert url == "https://found.ngrok.io"
