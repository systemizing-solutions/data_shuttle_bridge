import json
import os
import uuid
from typing import Optional

from data_shuttle_bridge.models.nodeid import ClientNodeConfig

DEFAULT_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".localfirst_sync")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")


class ClientNodeManager:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self._cfg = self._load_or_create()

    def _load_or_create(self) -> ClientNodeConfig:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ClientNodeConfig(
                device_key=data.get("device_key") or str(uuid.uuid4()),
                node_id=data.get("node_id"),
            )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        cfg = ClientNodeConfig(device_key=str(uuid.uuid4()), node_id=None)
        self._save(cfg)
        return cfg

    def _save(self, cfg: ClientNodeConfig) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {"device_key": cfg.device_key, "node_id": cfg.node_id}, f, indent=2
            )

    @property
    def device_key(self) -> str:
        return self._cfg.device_key

    @property
    def node_id(self) -> Optional[int]:
        return self._cfg.node_id

    def ensure_node_id(self, server_base_url: str, session=None) -> int:
        if self._cfg.node_id is not None:
            return self._cfg.node_id
        import requests

        sess = session or requests.Session()
        url = server_base_url.rstrip("/") + "/node/register"
        r = sess.post(url, json={"device_key": self._cfg.device_key}, timeout=10)
        r.raise_for_status()
        node_id = int(r.json()["node_id"])
        self._cfg.node_id = node_id
        self._save(self._cfg)
        return node_id
