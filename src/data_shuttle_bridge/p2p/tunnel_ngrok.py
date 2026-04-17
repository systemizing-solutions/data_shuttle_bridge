"""ngrok tunnel strategy.

Exposes a local port via ngrok and returns the public URL.

If ``ngrok`` is not installed it will be downloaded automatically.
An auth-token is optional for basic usage but recommended (ngrok
enforces rate limits without one).  If no token is configured the
user will be directed to the sign-up page.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from data_shuttle_bridge.p2p.tunnel import (
    SubprocessTunnelStrategy,
    register_tunnel_strategy,
)

_SIGNUP_URL = "https://dashboard.ngrok.com/signup"

# https://ngrok.com/download
_DOWNLOAD_BASE = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable"
_DOWNLOAD_MAP = {
    ("darwin", "amd64"): f"{_DOWNLOAD_BASE}-darwin-amd64.zip",
    ("darwin", "arm64"): f"{_DOWNLOAD_BASE}-darwin-arm64.zip",
    ("linux", "amd64"): f"{_DOWNLOAD_BASE}-linux-amd64.tgz",
    ("linux", "arm64"): f"{_DOWNLOAD_BASE}-linux-arm64.tgz",
    ("windows", "amd64"): f"{_DOWNLOAD_BASE}-windows-amd64.zip",
}


@register_tunnel_strategy
class NgrokTunnelStrategy(SubprocessTunnelStrategy):
    """Expose a local port through ngrok.

    ``ngrok`` will be **auto-installed** if not found.  Supply *authtoken*
    to authenticate (persisted by ngrok itself)::

        strategy = NgrokTunnelStrategy(local_port=5000, authtoken="...")
        with TunnelPeerTransport(strategy) as transport:
            ...

    The public URL is discovered by querying ngrok's local API at
    ``http://127.0.0.1:<api_port>/api/tunnels``.
    """

    name = "ngrok"
    signup_url = _SIGNUP_URL
    _binary_name = "ngrok"

    def __init__(
        self,
        local_port: int = 5000,
        authtoken: Optional[str] = None,
        domain: Optional[str] = None,
        binary: str = "ngrok",
        api_port: int = 4040,
        startup_timeout: int = 15,
    ) -> None:
        super().__init__()
        self._local_port = local_port
        self._authtoken = authtoken
        self._domain = domain
        self._binary = binary
        self._api_port = api_port
        self._startup_timeout = startup_timeout
        self._binary_name = binary

    # -- auto-install ------------------------------------------------------

    def install(self) -> None:
        plat = self._current_platform()
        arch = self._current_arch()

        if plat == "darwin":
            self._run_install_cmd(["brew", "install", "ngrok/ngrok/ngrok"], "ngrok")
            return

        key = (plat, arch)
        url = _DOWNLOAD_MAP.get(key)
        if not url:
            raise RuntimeError(
                f"No ngrok binary available for {plat}/{arch}. "
                f"Download manually from: https://ngrok.com/download"
            )

        import os
        import tempfile
        import urllib.request

        with tempfile.TemporaryDirectory() as tmpdir:
            archive = os.path.join(tmpdir, "ngrok-archive")
            urllib.request.urlretrieve(url, archive)

            if url.endswith(".zip"):
                import zipfile

                with zipfile.ZipFile(archive, "r") as zf:
                    zf.extractall(tmpdir)
            elif url.endswith(".tgz"):
                import tarfile

                with tarfile.open(archive, "r:gz") as tf:
                    tf.extractall(tmpdir)

            bin_path = os.path.join(tmpdir, "ngrok")
            dest = "/usr/local/bin/ngrok"
            self._run_install_cmd(
                ["sudo", "install", "-m", "0755", bin_path, dest],
                "ngrok",
            )

    # -- TunnelStrategy interface ------------------------------------------

    def start(self) -> str:
        self.ensure_ready()

        # Persist auth-token if provided (idempotent)
        if self._authtoken:
            subprocess.run(
                [self._binary, "config", "add-authtoken", self._authtoken],
                check=True,
                capture_output=True,
            )

        cmd = [self._binary, "http", str(self._local_port)]
        if self._domain:
            cmd.extend(["--domain", self._domain])

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self._public_url = self._wait_for_url()
        return self._public_url

    # -- helpers -----------------------------------------------------------

    def _wait_for_url(self) -> str:
        import requests

        api = f"http://127.0.0.1:{self._api_port}/api/tunnels"
        deadline = time.monotonic() + self._startup_timeout

        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"ngrok exited unexpectedly (code {self._process.returncode}). "
                    "Check that your authtoken is valid and the port is free."
                )
            try:
                resp = requests.get(api, timeout=2)
                resp.raise_for_status()
                tunnels = resp.json().get("tunnels", [])
                if tunnels:
                    return tunnels[0]["public_url"]
            except Exception:
                pass
            time.sleep(0.5)

        raise RuntimeError(
            f"ngrok did not expose a URL within {self._startup_timeout}s. "
            f"Ensure ngrok is configured correctly and port {self._local_port} "
            "is available."
        )
