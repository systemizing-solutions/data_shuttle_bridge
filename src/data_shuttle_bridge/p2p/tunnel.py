"""Pluggable tunnel strategies for peer-to-peer sync.

Provides :class:`TunnelStrategy` — the abstract base for all tunnel
back-ends — and :class:`TunnelPeerTransport`, a generic
:class:`~data_shuttle_bridge.sql.transport.PeerTransport` that delegates
networking to whichever strategy is plugged in.

Strategies register themselves via :func:`register_tunnel_strategy` and
can be looked up by name with :func:`get_tunnel_strategy_class`.

Default strategy: ``"wireguard"``.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import webbrowser
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Type

from data_shuttle_bridge.sql.transport import HttpPeerTransport, PeerTransport
from data_shuttle_bridge.sql.typing_ import ChangePayload

DEFAULT_STRATEGY = "wireguard"


def _log(msg: str) -> None:
    """Print an informational message to stderr."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class TunnelStrategy(ABC):
    """Abstract base for network tunnel strategies.

    A concrete strategy knows how to **start** a tunnel (returning the URL
    through which the peer's sync service is reachable) and how to **stop**
    it again.

    Subclasses *must* set the :attr:`name` class attribute to a unique
    short identifier (e.g. ``"wireguard"``, ``"ngrok"``).

    Subclasses *should* override :meth:`install` and :attr:`signup_url` so
    that :meth:`ensure_ready` can auto-install the binary and direct the
    user to create an account when required.
    """

    name: str = ""
    signup_url: Optional[str] = None
    """URL where the user can create an account, or ``None`` if no account
    is needed."""

    @abstractmethod
    def start(self) -> str:
        """Start the tunnel and return the base URL for the sync service."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the tunnel and clean up resources."""
        ...

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """Whether the tunnel is currently active."""
        ...

    @property
    def url(self) -> Optional[str]:
        """The peer URL if known before ``start()`` is called, else ``None``."""
        return None

    # -- auto-install helpers ----------------------------------------------

    def is_installed(self) -> bool:
        """Return ``True`` if the strategy's binary / dependency is available."""
        return True

    def install(self) -> None:
        """Download and install the binary for the current platform.

        Subclasses should override this.  The default raises
        :class:`NotImplementedError`.
        """
        raise NotImplementedError(
            f"Auto-install is not implemented for {self.name!r}. "
            f"Please install it manually."
        )

    def ensure_ready(self, *, auto_install: bool = True) -> None:
        """Make sure the binary is available, installing it if needed.

        When *auto_install* is ``True`` (the default) and the binary is
        missing, :meth:`install` is called automatically.  If an account
        is required, :attr:`signup_url` is opened in the default browser
        and a message is printed to stderr.
        """
        if self.is_installed():
            return

        if auto_install:
            _log(f"{self.name}: binary not found — installing automatically …")
            self.install()

            if self.signup_url:
                _log(
                    f"{self.name}: an account may be required. "
                    f"Sign up at: {self.signup_url}"
                )
                try:
                    webbrowser.open(self.signup_url)
                except Exception:
                    pass

            if not self.is_installed():
                raise RuntimeError(
                    f"{self.name}: auto-install finished but binary still not found. "
                    f"Please install it manually."
                )
            _log(f"{self.name}: installed successfully.")
        else:
            msg = f"{self.name} binary not found."
            if self.signup_url:
                msg += f" Sign up / download at: {self.signup_url}"
            raise RuntimeError(msg)


class SubprocessTunnelStrategy(TunnelStrategy):
    """Convenience base for strategies that shell out to an external CLI tool.

    Handles process lifecycle, auto-install, and provides platform detection.
    Concrete subclasses set ``_binary_name`` and override :meth:`install`.
    """

    _binary_name: str = ""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._public_url: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def url(self) -> Optional[str]:
        return self._public_url

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._public_url = None

    def is_installed(self) -> bool:
        return shutil.which(self._binary_name) is not None

    # -- platform helpers --------------------------------------------------

    @staticmethod
    def _current_platform() -> str:
        """Return normalised platform: ``darwin``, ``linux``, or ``windows``."""
        s = platform.system().lower()
        if s == "darwin":
            return "darwin"
        if s == "windows":
            return "windows"
        return "linux"

    @staticmethod
    def _current_arch() -> str:
        """Return normalised arch: ``amd64``, ``arm64``, or raw machine string."""
        m = platform.machine().lower()
        if m in ("x86_64", "amd64"):
            return "amd64"
        if m in ("aarch64", "arm64"):
            return "arm64"
        return m

    @staticmethod
    def _run_install_cmd(cmd: list[str], description: str) -> None:
        """Run an install command, raising on failure."""
        _log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to install {description}:\n"
                f"  stdout: {result.stdout}\n"
                f"  stderr: {result.stderr}"
            )

    @staticmethod
    def _check_binary(name: str, path: str) -> None:
        """Raise :class:`RuntimeError` if *path* is not on ``$PATH``."""
        if not shutil.which(path):
            raise RuntimeError(
                f"{name} binary not found at {path!r}. "
                f"Install it or provide the correct path."
            )


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Type[TunnelStrategy]] = {}


def register_tunnel_strategy(cls: Type[TunnelStrategy]) -> Type[TunnelStrategy]:
    """Class decorator — registers *cls* under ``cls.name``."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a non-empty 'name' attribute")
    _REGISTRY[cls.name] = cls
    return cls


def get_tunnel_strategy_class(name: str) -> Type[TunnelStrategy]:
    """Return the strategy class registered under *name*."""
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown tunnel strategy {name!r}. Available: {available}")
    return _REGISTRY[name]


def list_tunnel_strategies() -> List[str]:
    """Return sorted list of registered strategy names."""
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Generic transport
# ---------------------------------------------------------------------------


class TunnelPeerTransport(PeerTransport):
    """PeerTransport that syncs through a pluggable :class:`TunnelStrategy`.

    When used as a context manager with ``manage_tunnel=True`` (the default),
    :meth:`bring_up` / :meth:`bring_down` are called automatically::

        with TunnelPeerTransport.from_config("cloudflared", local_port=5000) as t:
            pulled, pushed = sync_engine.pull_then_push(t)

    Swap strategies by changing the name — the calling code stays identical::

        # From config / env / CLI arg:
        with TunnelPeerTransport.from_config(name, **kwargs) as t:
            sync_engine.pull_then_push(t)
    """

    def __init__(
        self,
        strategy: TunnelStrategy,
        manage_tunnel: bool = True,
    ) -> None:
        self._strategy = strategy
        self._manage_tunnel = manage_tunnel
        self._http: Optional[HttpPeerTransport] = None

        # Pre-create HTTP transport when the URL is already deterministic
        if strategy.url:
            self._http = HttpPeerTransport(strategy.url)

    @classmethod
    def from_config(
        cls,
        strategy: str = DEFAULT_STRATEGY,
        *,
        manage_tunnel: bool = True,
        **kwargs,
    ) -> "TunnelPeerTransport":
        """Create a transport from a strategy name and keyword arguments.

        The *strategy* name is looked up in the registry (e.g.
        ``"wireguard"``, ``"cloudflared"``, ``"ngrok"``).  All remaining
        keyword arguments are forwarded to the strategy constructor::

            # These three are interchangeable — only config changes:
            t = TunnelPeerTransport.from_config("wireguard", peer_virtual_ip="10.0.0.1")
            t = TunnelPeerTransport.from_config("cloudflared", local_port=5000)
            t = TunnelPeerTransport.from_config("ngrok", local_port=5000, authtoken="...")
        """
        strategy_cls = get_tunnel_strategy_class(strategy)
        return cls(strategy_cls(**kwargs), manage_tunnel=manage_tunnel)

    @property
    def strategy(self) -> TunnelStrategy:
        """The underlying tunnel strategy."""
        return self._strategy

    # -- helpers -----------------------------------------------------------

    def _ensure_http(self) -> HttpPeerTransport:
        if self._http is None:
            raise RuntimeError(
                "Tunnel not started. Use as a context manager or call bring_up()."
            )
        return self._http

    # -- tunnel lifecycle --------------------------------------------------

    def bring_up(self) -> None:
        """Start the tunnel and prepare the HTTP transport."""
        base_url = self._strategy.start()
        self._http = HttpPeerTransport(base_url)

    def bring_down(self) -> None:
        """Stop the tunnel."""
        self._strategy.stop()

    # -- PeerTransport interface -------------------------------------------

    def get_changes_since(
        self,
        since_id: int,
        limit: int = 1000,
        exclude_node_id: Optional[str] = None,
    ) -> List[ChangePayload]:
        return self._ensure_http().get_changes_since(
            since_id, limit=limit, exclude_node_id=exclude_node_id
        )

    def apply_changes(self, changes: Iterable[ChangePayload]) -> None:
        self._ensure_http().apply_changes(changes)

    def ack(self, last_seen_change_id: int) -> None:
        self._ensure_http().ack(last_seen_change_id)

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "TunnelPeerTransport":
        if self._manage_tunnel:
            self.bring_up()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if self._manage_tunnel:
            self.bring_down()

    async def __aenter__(self) -> "TunnelPeerTransport":
        if self._manage_tunnel:
            self.bring_up()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if self._manage_tunnel:
            self.bring_down()
