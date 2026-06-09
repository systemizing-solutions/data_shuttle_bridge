"""Cloudflare Tunnel (``cloudflared``) strategy.

Uses ``cloudflared tunnel --url`` ("quick tunnel") to expose a local port
via a ``*.trycloudflare.com`` URL — no Cloudflare account required.

If ``cloudflared`` is not installed it will be downloaded automatically.
"""

from __future__ import annotations

import re
import subprocess
import threading
from typing import Optional

from data_shuttle_bridge.p2p.tunnel import (
    SubprocessTunnelStrategy,
    register_tunnel_strategy,
)

_URL_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")

# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
_DOWNLOAD_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"
_DOWNLOAD_MAP = {
    ("darwin", "amd64"): f"{_DOWNLOAD_BASE}/cloudflared-darwin-amd64.tgz",
    ("darwin", "arm64"): f"{_DOWNLOAD_BASE}/cloudflared-darwin-amd64.tgz",
    ("linux", "amd64"): f"{_DOWNLOAD_BASE}/cloudflared-linux-amd64",
    ("linux", "arm64"): f"{_DOWNLOAD_BASE}/cloudflared-linux-arm64",
    ("windows", "amd64"): f"{_DOWNLOAD_BASE}/cloudflared-windows-amd64.exe",
}


@register_tunnel_strategy
class CloudflaredTunnelStrategy(SubprocessTunnelStrategy):
    """Expose a local port through Cloudflare's free quick-tunnel service.

    ``cloudflared`` will be **auto-installed** if not found.  No account or
    configuration is needed::

        strategy = CloudflaredTunnelStrategy(local_port=5000)
        with TunnelPeerTransport(strategy) as transport:
            ...
    """

    name = "cloudflared"
    signup_url = None  # No account required for quick tunnels
    _binary_name = "cloudflared"

    def __init__(
        self,
        local_port: int = 5000,
        binary: str = "cloudflared",
        startup_timeout: int = 30,
    ) -> None:
        super().__init__()
        self._local_port = local_port
        self._binary = binary
        self._startup_timeout = startup_timeout
        self._output_lines: list[str] = []
        self._reader_thread: Optional[threading.Thread] = None
        self._binary_name = binary

    # -- auto-install ------------------------------------------------------

    def install(self) -> None:
        plat = self._current_platform()
        arch = self._current_arch()
        key = (plat, arch)

        if plat == "darwin":
            # Prefer Homebrew on macOS
            self._run_install_cmd(["brew", "install", "cloudflared"], "cloudflared")
            return

        url = _DOWNLOAD_MAP.get(key)
        if not url:
            raise RuntimeError(
                f"No cloudflared binary available for {plat}/{arch}. "
                f"Download manually from: {_DOWNLOAD_BASE}"
            )

        import os
        import tempfile
        import urllib.request

        if url.endswith(".tgz"):
            import tarfile

            with tempfile.TemporaryDirectory() as tmpdir:
                archive = os.path.join(tmpdir, "cloudflared.tgz")
                urllib.request.urlretrieve(url, archive)
                with tarfile.open(archive, "r:gz") as tf:
                    tf.extractall(tmpdir)
                bin_path = os.path.join(tmpdir, "cloudflared")
                dest = "/usr/local/bin/cloudflared"
                self._run_install_cmd(
                    ["sudo", "install", "-m", "0755", bin_path, dest],
                    "cloudflared",
                )
        elif url.endswith(".exe"):
            dest = os.path.join(
                os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "cloudflared.exe"
            )
            urllib.request.urlretrieve(url, dest)
        else:
            # Bare binary (Linux)
            dest = "/usr/local/bin/cloudflared"
            with tempfile.NamedTemporaryFile(
                delete=False, suffix="-cloudflared"
            ) as tmp:
                tmp_path = tmp.name
            urllib.request.urlretrieve(url, tmp_path)
            self._run_install_cmd(
                ["sudo", "install", "-m", "0755", tmp_path, dest],
                "cloudflared",
            )
            os.unlink(tmp_path)

    # -- TunnelStrategy interface ------------------------------------------

    def start(self) -> str:
        self.ensure_ready()

        cmd = [
            self._binary,
            "tunnel",
            "--url",
            f"http://localhost:{self._local_port}",
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # cloudflared prints progress (including the public URL) to
        # combined stdout/stderr.  Read in a background thread so the
        # main thread can poll for the URL with a timeout.
        self._output_lines = []
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

        self._public_url = self._wait_for_url()
        return self._public_url

    def stop(self) -> None:
        super().stop()
        self._output_lines = []
        self._reader_thread = None

    # -- helpers -----------------------------------------------------------

    def _read_output(self) -> None:
        """Background thread: accumulate process output line-by-line."""
        assert self._process is not None
        assert self._process.stdout is not None
        for raw in self._process.stdout:
            self._output_lines.append(raw.decode(errors="replace"))

    def _wait_for_url(self) -> str:
        import time

        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            for line in self._output_lines:
                m = _URL_RE.search(line)
                if m:
                    return m.group(1)
            if self._process is not None and self._process.poll() is not None:
                output = "".join(self._output_lines)
                raise RuntimeError(f"cloudflared exited unexpectedly:\n{output}")
            time.sleep(0.5)

        output = "".join(self._output_lines)
        raise RuntimeError(
            f"cloudflared did not provide a URL within "
            f"{self._startup_timeout}s:\n{output}"
        )
