"""Tests for CLI modules (file_backup/cli.py, p2p/cli.py, cli.py)."""

import argparse
import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, mock_open


# ============================================================================
# File Backup CLI Tests
# ============================================================================


class TestBackupCLI:
    @patch("data_shuttle_bridge.file_backup.cli.init_repo")
    def test_cmd_backup_init_success(self, mock_init):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_init

        args = argparse.Namespace(repo_url="memory://test-repo")
        result = cmd_backup_init(args)
        assert result == 0
        mock_init.assert_called_once_with("memory://test-repo")

    @patch(
        "data_shuttle_bridge.file_backup.cli.init_repo", side_effect=Exception("fail")
    )
    def test_cmd_backup_init_failure(self, mock_init):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_init

        args = argparse.Namespace(repo_url="memory://test-repo")
        result = cmd_backup_init(args)
        assert result == 1

    @patch("data_shuttle_bridge.file_backup.cli.run_backup")
    def test_cmd_backup_backup_success(self, mock_backup):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_backup

        args = argparse.Namespace(
            repo_url="memory://test-repo", sources=["/tmp/a", "/tmp/b"]
        )
        result = cmd_backup_backup(args)
        assert result == 0
        mock_backup.assert_called_once_with("memory://test-repo", ["/tmp/a", "/tmp/b"])

    @patch(
        "data_shuttle_bridge.file_backup.cli.run_backup", side_effect=Exception("fail")
    )
    def test_cmd_backup_backup_failure(self, mock_backup):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_backup

        args = argparse.Namespace(repo_url="memory://test-repo", sources=["/tmp/a"])
        result = cmd_backup_backup(args)
        assert result == 1

    @patch("data_shuttle_bridge.file_backup.cli.list_snapshots")
    def test_cmd_backup_snapshots_success(self, mock_list):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_snapshots

        args = argparse.Namespace(repo_url="memory://test-repo")
        result = cmd_backup_snapshots(args)
        assert result == 0
        mock_list.assert_called_once_with("memory://test-repo")

    @patch(
        "data_shuttle_bridge.file_backup.cli.list_snapshots",
        side_effect=Exception("fail"),
    )
    def test_cmd_backup_snapshots_failure(self, mock_list):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_snapshots

        args = argparse.Namespace(repo_url="memory://test-repo")
        result = cmd_backup_snapshots(args)
        assert result == 1

    @patch("data_shuttle_bridge.file_backup.cli.run_restore")
    def test_cmd_backup_restore_success(self, mock_restore):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_restore

        args = argparse.Namespace(
            repo_url="memory://test-repo", dest="/tmp/out", snapshot_id="abc123"
        )
        result = cmd_backup_restore(args)
        assert result == 0
        mock_restore.assert_called_once_with("memory://test-repo", "/tmp/out", "abc123")

    @patch(
        "data_shuttle_bridge.file_backup.cli.run_restore", side_effect=Exception("fail")
    )
    def test_cmd_backup_restore_failure(self, mock_restore):
        from data_shuttle_bridge.file_backup.cli import cmd_backup_restore

        args = argparse.Namespace(
            repo_url="memory://test-repo", dest="/tmp/out", snapshot_id="abc123"
        )
        result = cmd_backup_restore(args)
        assert result == 1

    def test_add_backup_commands(self):
        from data_shuttle_bridge.file_backup.cli import add_backup_commands

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        add_backup_commands(subparsers)
        # Should not raise; just verify the commands are added
        args = parser.parse_args(["backup", "init", "memory://test"])
        assert args.repo_url == "memory://test"


# ============================================================================
# P2P CLI Tests
# ============================================================================


class TestP2PCLI:
    @patch("data_shuttle_bridge.p2p.cli.load_or_create_keypair")
    def test_cmd_p2p_init(self, mock_keypair):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_init

        mock_identity = MagicMock()
        mock_identity.public_key = "pubkey123"
        mock_keypair.return_value = mock_identity

        args = argparse.Namespace(config_dir=None)
        result = cmd_p2p_init(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.tunnel_status")
    def test_cmd_p2p_status_down(self, mock_status):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_status

        mock_status.return_value = {"status": "down"}
        args = argparse.Namespace(config_dir=None)
        result = cmd_p2p_status(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.tunnel_status")
    def test_cmd_p2p_status_up(self, mock_status):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_status

        mock_status.return_value = {
            "status": "up",
            "interface": "wg_shuttle",
            "listen_port": 51820,
            "peers": [{"public_key": "abc123", "endpoint": "1.2.3.4:51820"}],
        }
        args = argparse.Namespace(config_dir=None)
        result = cmd_p2p_status(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.tunnel_status")
    def test_cmd_p2p_status_error(self, mock_status):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_status

        mock_status.return_value = {"status": "down", "error": "not found"}
        args = argparse.Namespace(config_dir=None)
        result = cmd_p2p_status(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.remove_upnp_forward")
    @patch("data_shuttle_bridge.p2p.cli.tunnel_down")
    def test_cmd_p2p_down_success(self, mock_down, mock_upnp):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_down

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_down(args)
            assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.tunnel_down", side_effect=Exception("fail"))
    def test_cmd_p2p_down_failure(self, mock_down):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_down

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_down(args)
            assert result == 1

    @patch("data_shuttle_bridge.p2p.cli.tunnel_up")
    def test_cmd_p2p_up_no_config(self, mock_up):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_up

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_up(args)
            assert result == 1  # No config file

    @patch("data_shuttle_bridge.p2p.cli.wait_for_peer", return_value=True)
    @patch("data_shuttle_bridge.p2p.cli.tunnel_up")
    def test_cmd_p2p_up_with_config(self, mock_up, mock_wait):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_up

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_up(args)
            assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.tunnel_up", side_effect=RuntimeError("failed"))
    def test_cmd_p2p_up_failure(self, mock_up):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_up

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_up(args)
            assert result == 1

    @patch("data_shuttle_bridge.p2p.cli.create_invite", return_value="invite_token_abc")
    @patch("data_shuttle_bridge.p2p.cli.resolve_public_endpoint")
    def test_cmd_p2p_invite(self, mock_resolve, mock_invite):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_invite

        mock_resolve.return_value = EndpointInfo("1.2.3.4", 51820, "stun", "full_cone")
        args = argparse.Namespace(
            config_dir=None, port=None, sync_port=None, endpoint=None
        )
        result = cmd_p2p_invite(args)
        assert result == 0

    @patch(
        "data_shuttle_bridge.p2p.cli.accept_invite",
        return_value=("/tmp/config", "response_token", None),
    )
    def test_cmd_p2p_join(self, mock_accept):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_join

        args = argparse.Namespace(
            config_dir=None, port=None, endpoint=None, token="invite_token"
        )
        result = cmd_p2p_join(args)
        assert result == 0

    @patch(
        "data_shuttle_bridge.p2p.cli.accept_invite",
        return_value=("/tmp/config", "response_token", "WARNING: test"),
    )
    def test_cmd_p2p_join_with_warning(self, mock_accept):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_join

        args = argparse.Namespace(
            config_dir=None, port=None, endpoint=None, token="invite_token"
        )
        result = cmd_p2p_join(args)
        assert result == 0

    @patch(
        "data_shuttle_bridge.p2p.cli.complete_invite",
        return_value=("/tmp/config", None),
    )
    def test_cmd_p2p_complete(self, mock_complete):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_complete

        args = argparse.Namespace(
            config_dir=None, port=None, sync_port=None, token="response_token"
        )
        result = cmd_p2p_complete(args)
        assert result == 0

    @patch(
        "data_shuttle_bridge.p2p.cli.complete_invite",
        return_value=("/tmp/config", "WARNING: symmetric NAT"),
    )
    def test_cmd_p2p_complete_with_warning(self, mock_complete):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_complete

        args = argparse.Namespace(
            config_dir=None, port=None, sync_port=None, token="response_token"
        )
        result = cmd_p2p_complete(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.load_or_create_keypair")
    def test_cmd_p2p_init_custom_config_dir(self, mock_keypair):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_init

        mock_identity = MagicMock()
        mock_identity.public_key = "pubkey456"
        mock_keypair.return_value = mock_identity

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_init(args)
            assert result == 0
            mock_keypair.assert_called_once_with(tmpdir)

    @patch("data_shuttle_bridge.p2p.cli.create_invite", return_value="invite_token_abc")
    def test_cmd_p2p_invite_explicit_endpoint(self, mock_invite):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_invite

        args = argparse.Namespace(
            config_dir=None,
            port=51820,
            sync_port=8384,
            endpoint="1.2.3.4:51820",
        )
        result = cmd_p2p_invite(args)
        assert result == 0
        # resolve_public_endpoint should NOT be called when endpoint is explicit
        mock_invite.assert_called_once()

    @patch("data_shuttle_bridge.p2p.cli.create_invite", return_value="invite_token_abc")
    @patch("data_shuttle_bridge.p2p.cli.resolve_public_endpoint")
    def test_cmd_p2p_invite_symmetric_nat_warning(self, mock_resolve, mock_invite):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_invite
        from data_shuttle_bridge.p2p.nat import NAT_SYMMETRIC

        mock_resolve.return_value = EndpointInfo(
            "1.2.3.4", 51820, "stun", NAT_SYMMETRIC
        )
        args = argparse.Namespace(
            config_dir=None, port=None, sync_port=None, endpoint=None
        )
        result = cmd_p2p_invite(args)
        assert result == 0

    @patch("data_shuttle_bridge.p2p.cli.create_invite", return_value="invite_token_psk")
    @patch("data_shuttle_bridge.p2p.cli.resolve_public_endpoint")
    def test_cmd_p2p_invite_with_psk(self, mock_resolve, mock_invite):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_invite

        mock_resolve.return_value = EndpointInfo("1.2.3.4", 51820, "stun", "full_cone")
        args = argparse.Namespace(
            config_dir=None, port=None, sync_port=None, endpoint=None, psk="secret123"
        )
        result = cmd_p2p_invite(args)
        assert result == 0
        _, kwargs = mock_invite.call_args
        assert kwargs["psk"] == "secret123"

    @patch("data_shuttle_bridge.p2p.cli.wait_for_peer", return_value=False)
    @patch("data_shuttle_bridge.p2p.cli.tunnel_up")
    def test_cmd_p2p_up_peer_unreachable(self, mock_up, mock_wait):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_up

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            peers_path = os.path.join(tmpdir, "peers.json")
            with open(peers_path, "w") as f:
                json.dump({"peers": [{"virtual_ip": "10.0.0.2"}]}, f)
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_up(args)
            assert result == 0
            mock_wait.assert_called_once_with("10.0.0.2", timeout=15)

    @patch("data_shuttle_bridge.p2p.cli.tunnel_up")
    def test_cmd_p2p_up_no_peer_detected(self, mock_up):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_up

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            # No peers.json -> no peer IP detected
            args = argparse.Namespace(config_dir=tmpdir)
            result = cmd_p2p_up(args)
            assert result == 0

    def test_cmd_p2p_sync_no_peer_ip(self):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_sync

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            args = argparse.Namespace(config_dir=tmpdir, sync_port=None, keep_up=False)
            result = cmd_p2p_sync(args)
            assert result == 1

    @patch(
        "data_shuttle_bridge.p2p.tunnel.TunnelPeerTransport",
    )
    def test_cmd_p2p_sync_success(self, mock_transport_cls):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_sync

        mock_transport = MagicMock()
        mock_transport.__enter__ = MagicMock(return_value=mock_transport)
        mock_transport.__exit__ = MagicMock(return_value=False)
        mock_transport_cls.from_config.return_value = mock_transport

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")
            peers_path = os.path.join(tmpdir, "peers.json")
            with open(peers_path, "w") as f:
                json.dump({"peers": [{"virtual_ip": "10.0.0.2"}]}, f)

            with patch(
                "data_shuttle_bridge.p2p.cli._detect_peer_ip", return_value="10.0.0.2"
            ):
                args = argparse.Namespace(
                    config_dir=tmpdir, sync_port=None, keep_up=False
                )
                result = cmd_p2p_sync(args)
                assert result == 0

    @patch(
        "data_shuttle_bridge.p2p.tunnel.TunnelPeerTransport",
    )
    def test_cmd_p2p_sync_runtime_error(self, mock_transport_cls):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_sync

        mock_transport = MagicMock()
        mock_transport.__enter__ = MagicMock(side_effect=RuntimeError("connect failed"))
        mock_transport.__exit__ = MagicMock(return_value=False)
        mock_transport_cls.from_config.return_value = mock_transport

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")

            with patch(
                "data_shuttle_bridge.p2p.cli._detect_peer_ip", return_value="10.0.0.2"
            ):
                args = argparse.Namespace(
                    config_dir=tmpdir, sync_port=None, keep_up=False
                )
                result = cmd_p2p_sync(args)
                assert result == 1

    @patch(
        "data_shuttle_bridge.p2p.tunnel.TunnelPeerTransport",
    )
    def test_cmd_p2p_sync_keep_up(self, mock_transport_cls):
        from data_shuttle_bridge.p2p.cli import cmd_p2p_sync

        mock_transport = MagicMock()
        mock_transport.__enter__ = MagicMock(return_value=mock_transport)
        mock_transport.__exit__ = MagicMock(return_value=False)
        mock_transport_cls.from_config.return_value = mock_transport

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "wg_shuttle.conf")
            with open(config_path, "w") as f:
                f.write("[Interface]\nListenPort = 51820\n")

            with patch(
                "data_shuttle_bridge.p2p.cli._detect_peer_ip", return_value="10.0.0.2"
            ):
                args = argparse.Namespace(
                    config_dir=tmpdir, sync_port=8384, keep_up=True
                )
                result = cmd_p2p_sync(args)
                assert result == 0


class TestDetectPeerIp:
    def test_no_peers_file(self):
        from data_shuttle_bridge.p2p.cli import _detect_peer_ip

        with tempfile.TemporaryDirectory() as tmpdir:
            assert _detect_peer_ip(tmpdir) is None

    def test_empty_peers_list(self):
        from data_shuttle_bridge.p2p.cli import _detect_peer_ip

        with tempfile.TemporaryDirectory() as tmpdir:
            peers_path = os.path.join(tmpdir, "peers.json")
            with open(peers_path, "w") as f:
                json.dump({"peers": []}, f)
            assert _detect_peer_ip(tmpdir) is None

    def test_returns_last_peer_ip(self):
        from data_shuttle_bridge.p2p.cli import _detect_peer_ip

        with tempfile.TemporaryDirectory() as tmpdir:
            peers_path = os.path.join(tmpdir, "peers.json")
            with open(peers_path, "w") as f:
                json.dump(
                    {
                        "peers": [
                            {"virtual_ip": "10.0.0.2"},
                            {"virtual_ip": "10.0.0.3"},
                        ]
                    },
                    f,
                )
            assert _detect_peer_ip(tmpdir) == "10.0.0.3"

    def test_peer_missing_virtual_ip_key(self):
        from data_shuttle_bridge.p2p.cli import _detect_peer_ip

        with tempfile.TemporaryDirectory() as tmpdir:
            peers_path = os.path.join(tmpdir, "peers.json")
            with open(peers_path, "w") as f:
                json.dump({"peers": [{"public_key": "abc"}]}, f)
            assert _detect_peer_ip(tmpdir) is None


class TestAddP2PCommands:
    def test_registers_subcommands(self):
        from data_shuttle_bridge.p2p.cli import add_p2p_commands

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        add_p2p_commands(subparsers)

        # Verify init subcommand
        args = parser.parse_args(["p2p", "init"])
        assert hasattr(args, "func")

        # Verify invite subcommand
        args = parser.parse_args(["p2p", "invite"])
        assert hasattr(args, "func")

        # Verify join subcommand
        args = parser.parse_args(["p2p", "join", "some_token"])
        assert args.token == "some_token"

        # Verify complete subcommand
        args = parser.parse_args(["p2p", "complete", "resp_token"])
        assert args.token == "resp_token"

        # Verify up/down/status subcommands
        for subcmd in ("up", "down", "status"):
            args = parser.parse_args(["p2p", subcmd])
            assert hasattr(args, "func")

        # Verify sync subcommand with --keep-up
        args = parser.parse_args(["p2p", "sync", "--keep-up"])
        assert args.keep_up is True


# Need to import for type reference
from data_shuttle_bridge.p2p.nat import EndpointInfo


# ============================================================================
# Main CLI Tests (data_shuttle_bridge/cli.py)
# ============================================================================


class TestGetEngine:
    @patch("data_shuttle_bridge.cli.create_engine")
    def test_uses_provided_url(self, mock_create):
        from data_shuttle_bridge.cli import _get_engine

        _get_engine("sqlite:///test.db")
        mock_create.assert_called_once_with("sqlite:///test.db", echo=False)

    @patch("data_shuttle_bridge.cli.create_engine")
    def test_falls_back_to_env_var(self, mock_create):
        from data_shuttle_bridge.cli import _get_engine

        with patch.dict(os.environ, {"SHUTTLE_DB_URL": "sqlite:///env.db"}):
            _get_engine("")
            mock_create.assert_called_once_with("sqlite:///env.db", echo=False)

    @patch("data_shuttle_bridge.cli.create_engine")
    def test_falls_back_to_default(self, mock_create):
        from data_shuttle_bridge.cli import _get_engine

        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("SHUTTLE_DB_URL", None)
            with patch.dict(os.environ, env, clear=True):
                _get_engine("")
                mock_create.assert_called_once_with("sqlite:///shuttle.db", echo=False)


class TestCmdNodeInit:
    @patch("data_shuttle_bridge.cli.ClientNodeManager")
    def test_success_with_server_arg(self, mock_mgr_cls):
        from data_shuttle_bridge.cli import cmd_node_init

        mock_mgr = MagicMock()
        mock_mgr.device_key = "dk-123"
        mock_mgr.ensure_node_id.return_value = 42
        mock_mgr_cls.return_value = mock_mgr

        args = argparse.Namespace(server="http://localhost:5001")
        result = cmd_node_init(args)
        assert result == 0
        mock_mgr.ensure_node_id.assert_called_once_with("http://localhost:5001")

    @patch("data_shuttle_bridge.cli.ClientNodeManager")
    def test_success_with_env_var(self, mock_mgr_cls):
        from data_shuttle_bridge.cli import cmd_node_init

        mock_mgr = MagicMock()
        mock_mgr.device_key = "dk-456"
        mock_mgr.ensure_node_id.return_value = 99
        mock_mgr_cls.return_value = mock_mgr

        with patch.dict(os.environ, {"LOCALFIRST_SERVER": "http://env-server:5001"}):
            args = argparse.Namespace(server=None)
            result = cmd_node_init(args)
            assert result == 0
            mock_mgr.ensure_node_id.assert_called_once_with("http://env-server:5001")

    def test_no_server_returns_error(self):
        from data_shuttle_bridge.cli import cmd_node_init

        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("LOCALFIRST_SERVER", None)
            with patch.dict(os.environ, env, clear=True):
                args = argparse.Namespace(server=None)
                result = cmd_node_init(args)
                assert result == 2


class TestCmdNodeShow:
    @patch("data_shuttle_bridge.cli.ClientNodeManager")
    def test_success(self, mock_mgr_cls):
        from data_shuttle_bridge.cli import cmd_node_show

        mock_mgr = MagicMock()
        mock_mgr.device_key = "dk-abc"
        mock_mgr.node_id = 7
        mock_mgr_cls.return_value = mock_mgr

        args = argparse.Namespace()
        result = cmd_node_show(args)
        assert result == 0


class TestCmdSchemaCreate:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_success(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_create

        mock_registry = MagicMock()
        mock_schema_set = MagicMock()
        mock_schema_set.key = "customer"
        mock_schema_set.id = 1
        mock_registry.create_schema_set.return_value = mock_schema_set
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            name="Customer",
            description="Customer schema",
        )
        result = cmd_schema_create(args)
        assert result == 0
        mock_registry.create_schema_set.assert_called_once_with(
            mock_session,
            key="customer",
            name="Customer",
            description="Customer schema",
        )

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("db error"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_schema_create

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            name="Customer",
            description=None,
        )
        result = cmd_schema_create(args)
        assert result == 1


class TestCmdSchemaList:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_with_results(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_list

        mock_registry = MagicMock()
        mock_set1 = MagicMock()
        mock_set1.key = "customer"
        mock_set1.name = "Customer"
        mock_set2 = MagicMock()
        mock_set2.key = "order"
        mock_set2.name = "Order"
        mock_registry.list_schema_sets.return_value = [mock_set1, mock_set2]
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(db_url="sqlite:///test.db")
        result = cmd_schema_list(args)
        assert result == 0

    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_empty_results(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_list

        mock_registry = MagicMock()
        mock_registry.list_schema_sets.return_value = []
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(db_url="sqlite:///test.db")
        result = cmd_schema_list(args)
        assert result == 0

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("db error"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_schema_list

        args = argparse.Namespace(db_url="sqlite:///test.db")
        result = cmd_schema_list(args)
        assert result == 1


class TestCmdSchemaAddVersion:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_success(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_add_version

        schema_json = {"type": "object", "properties": {"name": {"type": "string"}}}

        mock_registry = MagicMock()
        mock_version = MagicMock()
        mock_version.version = 1
        mock_version.table_name = "customer__v1"
        mock_registry.add_schema_version.return_value = mock_version
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema_json, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                    parent=None,
                )
                result = cmd_schema_add_version(args)
                assert result == 0
                mock_registry.add_schema_version.assert_called_once_with(
                    mock_session,
                    schema_set_key="customer",
                    version=1,
                    schema_json=schema_json,
                    parent_version=None,
                )
            finally:
                os.unlink(f.name)

    def test_file_not_found(self):
        from data_shuttle_bridge.cli import cmd_schema_add_version

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            version=1,
            file="/nonexistent/schema.json",
            parent=None,
        )
        result = cmd_schema_add_version(args)
        assert result == 1

    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_with_parent_version(
        self, mock_engine, mock_registry_cls, mock_session_cls
    ):
        from data_shuttle_bridge.cli import cmd_schema_add_version

        schema_json = {"type": "object", "properties": {"name": {"type": "string"}}}

        mock_registry = MagicMock()
        mock_version = MagicMock()
        mock_version.version = 2
        mock_version.table_name = "customer__v2"
        mock_registry.add_schema_version.return_value = mock_version
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema_json, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=2,
                    file=f.name,
                    parent=1,
                )
                result = cmd_schema_add_version(args)
                assert result == 0
                mock_registry.add_schema_version.assert_called_once_with(
                    mock_session,
                    schema_set_key="customer",
                    version=2,
                    schema_json=schema_json,
                    parent_version=1,
                )
            finally:
                os.unlink(f.name)

    @patch(
        "data_shuttle_bridge.cli._get_engine", side_effect=Exception("registry error")
    )
    def test_exception(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_schema_add_version

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"type": "object"}, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                    parent=None,
                )
                result = cmd_schema_add_version(args)
                assert result == 1
            finally:
                os.unlink(f.name)


class TestCmdSchemaDiff:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_with_diff(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_diff

        mock_registry = MagicMock()
        mock_registry.get_schema_diff.return_value = {
            "added": ["email"],
            "removed": [],
        }
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            from_version=1,
            to_version=2,
        )
        result = cmd_schema_diff(args)
        assert result == 0

    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_no_diff(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_schema_diff

        mock_registry = MagicMock()
        mock_registry.get_schema_diff.return_value = None
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            from_version=1,
            to_version=2,
        )
        result = cmd_schema_diff(args)
        assert result == 0

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("fail"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_schema_diff

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            from_version=1,
            to_version=2,
        )
        result = cmd_schema_diff(args)
        assert result == 1


class TestCmdDataIngest:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_ingest_from_file(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_data_ingest

        payload = {"name": "Alice", "email": "alice@example.com"}

        mock_registry = MagicMock()
        mock_registry.ingest_data.return_value = 1
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                )
                result = cmd_data_ingest(args)
                assert result == 0
                mock_registry.ingest_data.assert_called_once_with(
                    mock_session,
                    schema_set_key="customer",
                    version=1,
                    payload=payload,
                )
            finally:
                os.unlink(f.name)

    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli.SchemaRegistry")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_ingest_from_stdin(self, mock_engine, mock_registry_cls, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_data_ingest

        payload = {"name": "Bob"}

        mock_registry = MagicMock()
        mock_registry.ingest_data.return_value = 2
        mock_registry_cls.return_value = mock_registry

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("data_shuttle_bridge.cli.sys") as mock_sys:
            mock_sys.stdin = MagicMock()
            mock_sys.stderr = MagicMock()
            with patch("data_shuttle_bridge.cli.json") as mock_json:
                mock_json.load.return_value = payload

                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=None,
                )
                result = cmd_data_ingest(args)
                assert result == 0

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("fail"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_data_ingest

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"name": "test"}, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                )
                result = cmd_data_ingest(args)
                assert result == 1
            finally:
                os.unlink(f.name)


class TestCmdMappingApply:
    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_success(self, mock_engine, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_mapping_apply

        rules_data = {"rename": {"old_name": "new_name"}}

        mock_session = MagicMock()
        mock_schema_set = MagicMock()
        mock_schema_set.id = 1
        mock_schema_version = MagicMock()
        mock_schema_version.id = 10

        # Mock the query chain
        mock_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_schema_set)),
            MagicMock(first=MagicMock(return_value=mock_schema_version)),
        ]

        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(rules_data, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                )
                result = cmd_mapping_apply(args)
                assert result == 0
            finally:
                os.unlink(f.name)

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("fail"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_mapping_apply

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"rename": {}}, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="customer",
                    version=1,
                    file=f.name,
                )
                result = cmd_mapping_apply(args)
                assert result == 1
            finally:
                os.unlink(f.name)

    @patch("data_shuttle_bridge.cli.Session")
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_schema_set_not_found(self, mock_engine, mock_session_cls):
        from data_shuttle_bridge.cli import cmd_mapping_apply

        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"rename": {}}, f)
            f.flush()
            try:
                args = argparse.Namespace(
                    db_url="sqlite:///test.db",
                    key="nonexistent",
                    version=1,
                    file=f.name,
                )
                result = cmd_mapping_apply(args)
                assert result == 1
            finally:
                os.unlink(f.name)


class TestCmdViewBuild:
    @patch("data_shuttle_bridge.cli._get_engine")
    def test_success(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_view_build

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            name="customer_all",
            include="1,2",
            target="latest",
            mode="selectable",
        )
        result = cmd_view_build(args)
        assert result == 0

    @patch("data_shuttle_bridge.cli._get_engine", side_effect=Exception("fail"))
    def test_failure(self, mock_engine):
        from data_shuttle_bridge.cli import cmd_view_build

        args = argparse.Namespace(
            db_url="sqlite:///test.db",
            key="customer",
            name="customer_all",
            include="1,2",
            target="latest",
            mode="selectable",
        )
        result = cmd_view_build(args)
        assert result == 1


class TestMainCLI:
    @patch("data_shuttle_bridge.cli.cmd_node_init", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_node_init(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(["node", "init", "--server", "http://localhost:5001"])
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_node_show", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_node_show(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(["node", "show"])
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_schema_create", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_schema_create(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(["schema", "create", "customer", "Customer"])
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_schema_list", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_schema_list(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(["schema", "list"])
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_schema_add_version", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_schema_add_version(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(
            [
                "schema",
                "add-version",
                "customer",
                "--version",
                "1",
                "--file",
                "schema.json",
            ]
        )
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_schema_diff", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_schema_diff(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(
            [
                "schema",
                "diff",
                "customer",
                "--from",
                "1",
                "--to",
                "2",
            ]
        )
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_data_ingest", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_data_ingest(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(
            [
                "data",
                "ingest",
                "customer",
                "--version",
                "1",
                "--file",
                "data.json",
            ]
        )
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_mapping_apply", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_mapping_apply(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(
            [
                "mapping",
                "apply",
                "customer",
                "--version",
                "1",
                "--file",
                "rules.json",
            ]
        )
        assert result == 0

    @patch("data_shuttle_bridge.cli.cmd_view_build", return_value=0)
    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_view_build(self, mock_backup, mock_p2p, mock_cmd):
        from data_shuttle_bridge.cli import main

        result = main(
            [
                "view",
                "build",
                "customer",
                "--name",
                "all_versions",
                "--include",
                "1,2",
            ]
        )
        assert result == 0

    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_no_command_exits(self, mock_backup, mock_p2p):
        from data_shuttle_bridge.cli import main

        with pytest.raises(SystemExit):
            main([])

    @patch("data_shuttle_bridge.cli.add_p2p_commands")
    @patch("data_shuttle_bridge.cli.add_backup_commands")
    def test_db_url_passed(self, mock_backup, mock_p2p):
        from data_shuttle_bridge.cli import main

        with patch(
            "data_shuttle_bridge.cli.cmd_schema_list", return_value=0
        ) as mock_cmd:
            result = main(["--db-url", "sqlite:///custom.db", "schema", "list"])
            assert result == 0
            call_args = mock_cmd.call_args[0][0]
            assert call_args.db_url == "sqlite:///custom.db"
