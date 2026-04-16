"""Tests for cli_main entry point."""

from unittest.mock import patch


class TestCliEntryPoint:
    """Tests for the cli() wrapper function."""

    @patch("data_shuttle_bridge.cli_main.main", return_value=0)
    def test_cli_calls_main(self, mock_main):
        from data_shuttle_bridge.cli_main import cli

        result = cli()

        mock_main.assert_called_once()
        assert result == 0

    @patch("data_shuttle_bridge.cli_main.main", return_value=1)
    def test_cli_propagates_nonzero_return(self, mock_main):
        from data_shuttle_bridge.cli_main import cli

        result = cli()

        assert result == 1


class TestCliMainBlock:
    """Tests for the __main__ guard."""

    @patch("data_shuttle_bridge.cli_main.main", return_value=0)
    def test_main_block_raises_system_exit_zero(self, mock_main):
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            exec(
                compile(
                    "raise SystemExit(cli())",
                    "<string>",
                    "exec",
                ),
                {"cli": lambda: mock_main()},
            )

        assert exc_info.value.code == 0

    @patch("data_shuttle_bridge.cli_main.main", return_value=1)
    def test_main_block_raises_system_exit_nonzero(self, mock_main):
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            exec(
                compile(
                    "raise SystemExit(cli())",
                    "<string>",
                    "exec",
                ),
                {"cli": lambda: mock_main()},
            )

        assert exc_info.value.code == 1

    @patch("data_shuttle_bridge.cli.main", return_value=0)
    def test_runpy_main(self, mock_main):
        """Test running the module via runpy, as python -m would."""
        import runpy
        import sys
        import pytest

        sys.modules.pop("data_shuttle_bridge.cli_main", None)

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("data_shuttle_bridge.cli_main", run_name="__main__")

        mock_main.assert_called_once()
        assert exc_info.value.code == 0
