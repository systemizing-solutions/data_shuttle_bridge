"""Tests for NAT traversal utilities (p2p/nat.py)."""

import socket
import struct
import pytest
from unittest.mock import MagicMock, patch

from data_shuttle_bridge.p2p.nat import (
    _build_stun_request,
    _parse_stun_response,
    _get_local_ip,
    _get_default_gateway,
    _natpmp_get_external_ip,
    discover_endpoint,
    discover_endpoint_multi,
    detect_nat_type,
    try_upnp_forward,
    try_natpmp_forward,
    remove_upnp_forward,
    auto_forward,
    resolve_public_endpoint,
    EndpointInfo,
    STUN_BINDING_RESPONSE,
    STUN_MAGIC_COOKIE,
    STUN_ATTR_XOR_MAPPED_ADDRESS,
    STUN_ATTR_MAPPED_ADDRESS,
    NAT_FULL_CONE,
    NAT_SYMMETRIC,
    NAT_UNKNOWN,
    NAT_OPEN,
)


class TestBuildStunRequest:
    def test_returns_packet_and_txn_id(self):
        pkt, txn_id = _build_stun_request()
        assert len(txn_id) == 12
        assert len(pkt) == 20  # STUN header is 20 bytes

    def test_packet_structure(self):
        pkt, txn_id = _build_stun_request()
        msg_type, msg_len, cookie = struct.unpack_from("!HHI", pkt, 0)
        assert msg_type == 0x0001  # Binding Request
        assert msg_len == 0  # No attributes
        assert cookie == STUN_MAGIC_COOKIE


class TestParseStunResponse:
    def _make_xor_mapped_response(self, ip_str, port, txn_id):
        """Build a fake STUN response with XOR-MAPPED-ADDRESS."""
        import socket as _socket

        ip_int = struct.unpack("!I", _socket.inet_aton(ip_str))[0]
        xor_port = port ^ (STUN_MAGIC_COOKIE >> 16)
        xor_addr = ip_int ^ STUN_MAGIC_COOKIE

        attr_body = struct.pack("!BBHI", 0, 0x01, xor_port, xor_addr)
        attr_header = struct.pack("!HH", STUN_ATTR_XOR_MAPPED_ADDRESS, len(attr_body))

        msg_body = attr_header + attr_body
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, len(msg_body), STUN_MAGIC_COOKIE, txn_id
        )
        return header + msg_body

    def _make_mapped_response(self, ip_str, port, txn_id):
        """Build a fake STUN response with MAPPED-ADDRESS."""
        import socket as _socket

        ip_int = struct.unpack("!I", _socket.inet_aton(ip_str))[0]

        attr_body = struct.pack("!BBHI", 0, 0x01, port, ip_int)
        attr_header = struct.pack("!HH", STUN_ATTR_MAPPED_ADDRESS, len(attr_body))

        msg_body = attr_header + attr_body
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, len(msg_body), STUN_MAGIC_COOKIE, txn_id
        )
        return header + msg_body

    def test_parse_xor_mapped(self):
        txn_id = b"\x01" * 12
        data = self._make_xor_mapped_response("1.2.3.4", 12345, txn_id)
        result = _parse_stun_response(data, txn_id)
        assert result is not None
        assert result == ("1.2.3.4", 12345)

    def test_parse_mapped_address(self):
        txn_id = b"\x02" * 12
        data = self._make_mapped_response("5.6.7.8", 54321, txn_id)
        result = _parse_stun_response(data, txn_id)
        assert result is not None
        assert result == ("5.6.7.8", 54321)

    def test_wrong_txn_id(self):
        txn_id = b"\x01" * 12
        wrong_txn = b"\x02" * 12
        data = self._make_xor_mapped_response("1.2.3.4", 12345, txn_id)
        result = _parse_stun_response(data, wrong_txn)
        assert result is None

    def test_too_short(self):
        result = _parse_stun_response(b"\x00" * 10, b"\x00" * 12)
        assert result is None

    def test_wrong_msg_type(self):
        txn_id = b"\x01" * 12
        # Build a request instead of response
        header = struct.pack("!HHI12s", 0x0001, 0, STUN_MAGIC_COOKIE, txn_id)
        result = _parse_stun_response(header, txn_id)
        assert result is None


class TestDiscoverEndpoint:
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_success(self, mock_socket_cls):
        txn_id = b"\x01" * 12
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Build a valid STUN response
        ip_int = struct.unpack("!I", socket.inet_aton("1.2.3.4"))[0]
        xor_port = 5678 ^ (STUN_MAGIC_COOKIE >> 16)
        xor_addr = ip_int ^ STUN_MAGIC_COOKIE
        attr_body = struct.pack("!BBHI", 0, 0x01, xor_port, xor_addr)
        attr_header = struct.pack("!HH", STUN_ATTR_XOR_MAPPED_ADDRESS, len(attr_body))
        msg_body = attr_header + attr_body
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, len(msg_body), STUN_MAGIC_COOKIE, txn_id
        )
        response = header + msg_body

        # Need to match the txn_id the function generates
        with patch(
            "data_shuttle_bridge.p2p.nat._build_stun_request",
            return_value=(b"request", txn_id),
        ):
            mock_sock.recvfrom.return_value = (response, ("1.2.3.4", 19302))
            result = discover_endpoint(("stun.test.com", 3478))
            assert result == ("1.2.3.4", 5678)

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_timeout(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.side_effect = socket.timeout()
        result = discover_endpoint(("stun.test.com", 3478))
        assert result is None

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_oserror(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.side_effect = OSError("connection refused")
        result = discover_endpoint(("stun.test.com", 3478))
        assert result is None

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_with_local_port(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.side_effect = socket.timeout()
        discover_endpoint(("stun.test.com", 3478), local_port=5555)
        mock_sock.bind.assert_called_once_with(("", 5555))


class TestDiscoverEndpointMulti:
    @patch("data_shuttle_bridge.p2p.nat.discover_endpoint")
    def test_returns_first_success(self, mock_discover):
        mock_discover.side_effect = [None, ("1.2.3.4", 5678)]
        result = discover_endpoint_multi([("a.com", 3478), ("b.com", 3478)])
        assert result == ("1.2.3.4", 5678)

    @patch("data_shuttle_bridge.p2p.nat.discover_endpoint")
    def test_returns_none_all_fail(self, mock_discover):
        mock_discover.return_value = None
        result = discover_endpoint_multi([("a.com", 3478)])
        assert result is None


class TestDetectNatType:
    def test_too_few_servers(self):
        result = detect_nat_type([("only.one", 3478)])
        assert result == NAT_UNKNOWN

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_socket_failure(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.sendto.side_effect = OSError("fail")
        result = detect_nat_type()
        assert result == NAT_UNKNOWN


class TestGetLocalIp:
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_success(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.getsockname.return_value = ("192.168.1.100", 0)
        result = _get_local_ip()
        assert result == "192.168.1.100"

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_failure(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = OSError("no route")
        result = _get_local_ip()
        assert result == "127.0.0.1"


class TestUPnPForward:
    def test_no_miniupnpc(self):
        with patch.dict("sys.modules", {"miniupnpc": None}):
            # miniupnpc import should fail, returning None
            result = try_upnp_forward(51820)
            assert result is None

    @patch("data_shuttle_bridge.p2p.nat._upnp_mapping", None)
    def test_remove_upnp_no_mapping(self):
        # Should not raise
        remove_upnp_forward()


class TestNatPmpForward:
    @patch("data_shuttle_bridge.p2p.nat._get_default_gateway", return_value=None)
    def test_no_gateway(self, mock_gw):
        result = try_natpmp_forward(51820)
        assert result is None

    @patch(
        "data_shuttle_bridge.p2p.nat._get_default_gateway", return_value="192.168.1.1"
    )
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_timeout(self, mock_socket_cls, mock_gw):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.side_effect = socket.timeout()
        result = try_natpmp_forward(51820)
        assert result is None

    @patch(
        "data_shuttle_bridge.p2p.nat._get_default_gateway", return_value="192.168.1.1"
    )
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_short_response(self, mock_socket_cls, mock_gw):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.return_value = (b"\x00" * 10, ("192.168.1.1", 5351))
        result = try_natpmp_forward(51820)
        assert result is None


class TestNatPmpGetExternalIp:
    def test_timeout(self):
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = socket.timeout()
        result = _natpmp_get_external_ip("192.168.1.1", mock_sock)
        assert result is None

    def test_success(self):
        # Build NAT-PMP response: ver(1) + op(1) + result(2) + epoch(4) + ip(4) = 12 bytes
        ip_bytes = socket.inet_aton("1.2.3.4")
        response = struct.pack("!BBHI", 0, 128, 0, 12345) + ip_bytes
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (response, ("192.168.1.1", 5351))
        result = _natpmp_get_external_ip("192.168.1.1", mock_sock)
        assert result == "1.2.3.4"


class TestGetDefaultGateway:
    @patch("data_shuttle_bridge.p2p.nat.subprocess.check_output")
    def test_darwin(self, mock_check):
        mock_check.return_value = b"   route to: default\n   gateway: 192.168.1.1\n"
        import platform

        with patch("platform.system", return_value="Darwin"):
            result = _get_default_gateway()
            assert result == "192.168.1.1"

    @patch("data_shuttle_bridge.p2p.nat.subprocess.check_output")
    def test_linux(self, mock_check):
        mock_check.return_value = b"default via 10.0.0.1 dev eth0\n"
        import platform

        with patch("platform.system", return_value="Linux"):
            result = _get_default_gateway()
            assert result == "10.0.0.1"

    @patch(
        "data_shuttle_bridge.p2p.nat.subprocess.check_output",
        side_effect=FileNotFoundError,
    )
    def test_failure(self, mock_check):
        result = _get_default_gateway()
        assert result is None


class TestAutoForward:
    @patch("data_shuttle_bridge.p2p.nat.try_natpmp_forward", return_value=None)
    @patch("data_shuttle_bridge.p2p.nat.try_upnp_forward", return_value=None)
    def test_both_fail(self, mock_upnp, mock_natpmp):
        result = auto_forward(51820)
        assert result is None

    @patch(
        "data_shuttle_bridge.p2p.nat.try_upnp_forward", return_value=("1.2.3.4", 51820)
    )
    def test_upnp_success(self, mock_upnp):
        result = auto_forward(51820)
        assert result == ("1.2.3.4", 51820)

    @patch(
        "data_shuttle_bridge.p2p.nat.try_natpmp_forward",
        return_value=("5.6.7.8", 51820),
    )
    @patch("data_shuttle_bridge.p2p.nat.try_upnp_forward", return_value=None)
    def test_natpmp_fallback(self, mock_upnp, mock_natpmp):
        result = auto_forward(51820)
        assert result == ("5.6.7.8", 51820)


class TestResolvePublicEndpoint:
    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=("1.2.3.4", 51820))
    def test_upnp(self, mock_fwd):
        ep = resolve_public_endpoint(51820)
        assert ep.public_ip == "1.2.3.4"
        assert ep.method == "upnp"

    @patch("data_shuttle_bridge.p2p.nat.detect_nat_type", return_value=NAT_FULL_CONE)
    @patch(
        "data_shuttle_bridge.p2p.nat.discover_endpoint_multi",
        return_value=("5.6.7.8", 9999),
    )
    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=None)
    def test_stun(self, mock_fwd, mock_stun, mock_nat):
        ep = resolve_public_endpoint(51820)
        assert ep.public_ip == "5.6.7.8"
        assert ep.method == "stun"
        assert ep.nat_type == NAT_FULL_CONE

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    @patch("data_shuttle_bridge.p2p.nat.discover_endpoint_multi", return_value=None)
    @patch("data_shuttle_bridge.p2p.nat.auto_forward", return_value=None)
    def test_manual_fallback(self, mock_fwd, mock_stun, mock_ip):
        ep = resolve_public_endpoint(51820)
        assert ep.public_ip == "192.168.1.100"
        assert ep.method == "manual"
        assert ep.nat_type == NAT_UNKNOWN


class TestEndpointInfo:
    def test_dataclass(self):
        ep = EndpointInfo("1.2.3.4", 51820, "stun", NAT_FULL_CONE)
        assert ep.public_ip == "1.2.3.4"
        assert ep.public_port == 51820
        assert ep.method == "stun"
        assert ep.nat_type == NAT_FULL_CONE

    def test_defaults(self):
        ep = EndpointInfo("1.2.3.4", 51820, "manual")
        assert ep.nat_type == NAT_UNKNOWN


# ============================================================================
# Additional coverage tests
# ============================================================================


class TestParseStunResponseEdgeCases:
    """Cover edge cases in _parse_stun_response."""

    def test_truncated_attribute_header(self):
        """Attribute header extends past data (line 76)."""
        txn_id = b"\x01" * 12
        # Header says msg_len=10, but only provide 3 bytes of body (not enough for attr header)
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, 10, STUN_MAGIC_COOKIE, txn_id
        )
        data = header + b"\x00" * 3  # truncated attr header
        result = _parse_stun_response(data, txn_id)
        assert result is None

    def test_truncated_attribute_value(self):
        """Attribute value extends past data (line 80)."""
        txn_id = b"\x01" * 12
        # Valid attr header claiming 100 bytes, but only 4 bytes follow
        attr_header = struct.pack("!HH", 0x9999, 100)  # unknown attr type, len=100
        msg_body = attr_header + b"\x00" * 4
        header = struct.pack(
            "!HHI12s",
            STUN_BINDING_RESPONSE,
            len(msg_body),
            STUN_MAGIC_COOKIE,
            txn_id,
        )
        result = _parse_stun_response(header + msg_body, txn_id)
        assert result is None

    def test_no_recognized_attributes(self):
        """Response with unknown attributes only (lines 99-101 padding/return None)."""
        txn_id = b"\x01" * 12
        # Unknown attribute type with valid length
        attr_body = b"\x00" * 8
        attr_header = struct.pack("!HH", 0x9999, len(attr_body))
        msg_body = attr_header + attr_body
        header = struct.pack(
            "!HHI12s",
            STUN_BINDING_RESPONSE,
            len(msg_body),
            STUN_MAGIC_COOKIE,
            txn_id,
        )
        result = _parse_stun_response(header + msg_body, txn_id)
        assert result is None


class TestDetectNatTypeOutcomes:
    """Cover detect_nat_type branches for full_cone, open, and symmetric (lines 164-194)."""

    def _make_xor_mapped_response(self, ip_str, port, txn_id):
        ip_int = struct.unpack("!I", socket.inet_aton(ip_str))[0]
        xor_port = port ^ (STUN_MAGIC_COOKIE >> 16)
        xor_addr = ip_int ^ STUN_MAGIC_COOKIE
        attr_body = struct.pack("!BBHI", 0, 0x01, xor_port, xor_addr)
        attr_header = struct.pack("!HH", STUN_ATTR_XOR_MAPPED_ADDRESS, len(attr_body))
        msg_body = attr_header + attr_body
        header = struct.pack(
            "!HHI12s", STUN_BINDING_RESPONSE, len(msg_body), STUN_MAGIC_COOKIE, txn_id
        )
        return header + msg_body

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_full_cone(self, mock_socket_cls, mock_local_ip):
        """Same mapped address from both servers → FULL_CONE."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.getsockname.return_value = ("0.0.0.0", 12345)

        txn_ids = [b"\x01" * 12, b"\x02" * 12]
        responses = [
            self._make_xor_mapped_response("1.2.3.4", 5000, txn_ids[0]),
            self._make_xor_mapped_response("1.2.3.4", 5000, txn_ids[1]),
        ]

        call_count = [0]

        def fake_recvfrom(size):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx], ("server", 3478)

        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch(
            "data_shuttle_bridge.p2p.nat._build_stun_request",
            side_effect=[(b"req", txn_ids[0]), (b"req", txn_ids[1])],
        ):
            result = detect_nat_type(
                [("a.com", 3478), ("b.com", 3478)], local_port=12345
            )
            assert result == NAT_FULL_CONE

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="1.2.3.4")
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_open_nat(self, mock_socket_cls, mock_local_ip):
        """Same mapped address matching local IP → NAT_OPEN."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.getsockname.return_value = ("0.0.0.0", 5000)

        txn_ids = [b"\x01" * 12, b"\x02" * 12]
        responses = [
            self._make_xor_mapped_response("1.2.3.4", 5000, txn_ids[0]),
            self._make_xor_mapped_response("1.2.3.4", 5000, txn_ids[1]),
        ]

        call_count = [0]

        def fake_recvfrom(size):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx], ("server", 3478)

        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch(
            "data_shuttle_bridge.p2p.nat._build_stun_request",
            side_effect=[(b"req", txn_ids[0]), (b"req", txn_ids[1])],
        ):
            result = detect_nat_type([("a.com", 3478), ("b.com", 3478)])
            assert result == NAT_OPEN

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_symmetric_nat(self, mock_socket_cls):
        """Same IP, different port → NAT_SYMMETRIC."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.getsockname.return_value = ("0.0.0.0", 12345)

        txn_ids = [b"\x01" * 12, b"\x02" * 12]
        responses = [
            self._make_xor_mapped_response("1.2.3.4", 5000, txn_ids[0]),
            self._make_xor_mapped_response("1.2.3.4", 6000, txn_ids[1]),
        ]

        call_count = [0]

        def fake_recvfrom(size):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx], ("server", 3478)

        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch(
            "data_shuttle_bridge.p2p.nat._build_stun_request",
            side_effect=[(b"req", txn_ids[0]), (b"req", txn_ids[1])],
        ):
            result = detect_nat_type([("a.com", 3478), ("b.com", 3478)])
            assert result == NAT_SYMMETRIC

    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_with_explicit_local_port(self, mock_socket_cls):
        """Cover the local_port binding branch in detect_nat_type."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recvfrom.side_effect = socket.timeout()

        result = detect_nat_type(
            [("a.com", 3478), ("b.com", 3478)], local_port=55555
        )
        mock_sock.bind.assert_called_once_with(("", 55555))
        assert result == NAT_UNKNOWN


class TestUPnPForwardSuccess:
    """Cover successful UPnP forward path (lines 234-268)."""

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    def test_upnp_success(self, mock_local_ip):
        mock_upnp_instance = MagicMock()
        mock_upnp_instance.discover.return_value = 1
        mock_upnp_instance.externalipaddress.return_value = "1.2.3.4"
        mock_upnp_instance.addportmapping.return_value = True

        mock_module = MagicMock()
        mock_module.UPnP.return_value = mock_upnp_instance

        import data_shuttle_bridge.p2p.nat as nat_mod

        old_mapping = nat_mod._upnp_mapping
        nat_mod._upnp_mapping = None

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            result = try_upnp_forward(51820)
            assert result == ("1.2.3.4", 51820)
            assert nat_mod._upnp_mapping == (51820, "UDP")

        nat_mod._upnp_mapping = old_mapping

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    def test_upnp_no_devices(self, mock_local_ip):
        mock_upnp_instance = MagicMock()
        mock_upnp_instance.discover.return_value = 0

        mock_module = MagicMock()
        mock_module.UPnP.return_value = mock_upnp_instance

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            result = try_upnp_forward(51820)
            assert result is None

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    def test_upnp_mapping_fails(self, mock_local_ip):
        mock_upnp_instance = MagicMock()
        mock_upnp_instance.discover.return_value = 1
        mock_upnp_instance.externalipaddress.return_value = "1.2.3.4"
        mock_upnp_instance.addportmapping.return_value = False

        mock_module = MagicMock()
        mock_module.UPnP.return_value = mock_upnp_instance

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            result = try_upnp_forward(51820)
            assert result is None

    @patch("data_shuttle_bridge.p2p.nat._get_local_ip", return_value="192.168.1.100")
    def test_upnp_exception(self, mock_local_ip):
        mock_upnp_instance = MagicMock()
        mock_upnp_instance.discover.side_effect = Exception("UPnP error")

        mock_module = MagicMock()
        mock_module.UPnP.return_value = mock_upnp_instance

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            result = try_upnp_forward(51820)
            assert result is None


class TestRemoveUPnPForwardSuccess:
    """Cover successful remove_upnp_forward path (lines 276-288)."""

    def test_remove_upnp_with_active_mapping(self):
        import data_shuttle_bridge.p2p.nat as nat_mod

        old_mapping = nat_mod._upnp_mapping
        nat_mod._upnp_mapping = (51820, "UDP")

        mock_upnp_instance = MagicMock()
        mock_upnp_instance.discover.return_value = 1

        mock_module = MagicMock()
        mock_module.UPnP.return_value = mock_upnp_instance

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            remove_upnp_forward()
            mock_upnp_instance.deleteportmapping.assert_called_once_with(51820, "UDP")
            assert nat_mod._upnp_mapping is None

        nat_mod._upnp_mapping = old_mapping

    def test_remove_upnp_exception(self):
        import data_shuttle_bridge.p2p.nat as nat_mod

        old_mapping = nat_mod._upnp_mapping
        nat_mod._upnp_mapping = (51820, "UDP")

        mock_module = MagicMock()
        mock_module.UPnP.side_effect = Exception("cleanup fail")

        with patch.dict("sys.modules", {"miniupnpc": mock_module}):
            remove_upnp_forward()  # Should not raise

        nat_mod._upnp_mapping = old_mapping


class TestNatPmpForwardSuccess:
    """Cover successful NAT-PMP forward (lines 329-347)."""

    @patch(
        "data_shuttle_bridge.p2p.nat._get_default_gateway", return_value="192.168.1.1"
    )
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_natpmp_success(self, mock_socket_cls, mock_gw):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Build NAT-PMP mapping response
        map_response = struct.pack(
            "!BBHIHHI",
            0,      # version
            129,    # opcode (response)
            0,      # result: success
            12345,  # epoch
            51820,  # internal port
            51820,  # external port
            7200,   # lifetime
        )
        # Build external IP response
        ip_bytes = socket.inet_aton("1.2.3.4")
        ip_response = struct.pack("!BBHI", 0, 128, 0, 12345) + ip_bytes

        mock_sock.recvfrom.side_effect = [
            (map_response, ("192.168.1.1", 5351)),
            (ip_response, ("192.168.1.1", 5351)),
        ]

        result = try_natpmp_forward(51820)
        assert result == ("1.2.3.4", 51820)

    @patch(
        "data_shuttle_bridge.p2p.nat._get_default_gateway", return_value="192.168.1.1"
    )
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_natpmp_error_result_code(self, mock_socket_cls, mock_gw):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Build NAT-PMP response with error
        map_response = struct.pack(
            "!BBHIHHI",
            0, 129, 1,  # result_code=1 (error)
            12345, 51820, 51820, 7200,
        )
        mock_sock.recvfrom.return_value = (map_response, ("192.168.1.1", 5351))

        result = try_natpmp_forward(51820)
        assert result is None

    @patch(
        "data_shuttle_bridge.p2p.nat._get_default_gateway", return_value="192.168.1.1"
    )
    @patch("data_shuttle_bridge.p2p.nat.socket.socket")
    def test_natpmp_no_external_ip(self, mock_socket_cls, mock_gw):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Build valid mapping response
        map_response = struct.pack(
            "!BBHIHHI", 0, 129, 0, 12345, 51820, 51820, 7200,
        )
        mock_sock.recvfrom.side_effect = [
            (map_response, ("192.168.1.1", 5351)),
            socket.timeout(),  # external IP request fails
        ]

        result = try_natpmp_forward(51820)
        assert result is None


class TestNatPmpGetExternalIpOwnSocket:
    """Cover _natpmp_get_external_ip with own socket (lines 361-362, 371, 376)."""

    def test_own_socket_success(self):
        """When sock=None, should create its own socket."""
        ip_bytes = socket.inet_aton("5.6.7.8")
        response = struct.pack("!BBHI", 0, 128, 0, 12345) + ip_bytes

        with patch("data_shuttle_bridge.p2p.nat.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_cls.return_value = mock_sock
            mock_sock.recvfrom.return_value = (response, ("192.168.1.1", 5351))

            result = _natpmp_get_external_ip("192.168.1.1")
            assert result == "5.6.7.8"
            mock_sock.close.assert_called_once()

    def test_own_socket_timeout(self):
        """When sock=None and request times out."""
        with patch("data_shuttle_bridge.p2p.nat.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_cls.return_value = mock_sock
            mock_sock.recvfrom.side_effect = socket.timeout()

            result = _natpmp_get_external_ip("192.168.1.1")
            assert result is None
            mock_sock.close.assert_called_once()

    def test_short_response(self):
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (b"\x00" * 5, ("192.168.1.1", 5351))
        result = _natpmp_get_external_ip("192.168.1.1", mock_sock)
        assert result is None


class TestGetDefaultGatewayEdgeCases:
    """Cover additional gateway detection branches."""

    @patch("data_shuttle_bridge.p2p.nat.subprocess.check_output")
    def test_linux_no_via(self, mock_check):
        """Linux output without 'via' keyword."""
        mock_check.return_value = b"default dev eth0 proto static\n"
        with patch("platform.system", return_value="Linux"):
            result = _get_default_gateway()
            # 'via' not in parts, so IndexError or None
            assert result is None

    @patch("platform.system", return_value="Windows")
    def test_unsupported_os(self, mock_sys):
        """OS that's neither darwin nor linux."""
        result = _get_default_gateway()
        assert result is None
