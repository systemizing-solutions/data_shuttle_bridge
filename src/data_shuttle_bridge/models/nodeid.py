"""Client node configuration data model."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClientNodeConfig:
    device_key: str
    node_id: Optional[int] = None
