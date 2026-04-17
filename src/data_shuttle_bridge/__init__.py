# SQL
from data_shuttle_bridge.sql.ids import (
    KSortedID,
    set_id_generator,
    get_id_generator,
    clear_id_generator,
)
from data_shuttle_bridge.sql.mixins import SyncRowSQLModelMixin, SyncRowSAMixin
from data_shuttle_bridge.sql.changelog import ChangeLog, SyncState
from data_shuttle_bridge.sql.wiring import (
    attach_change_hooks,
    attach_change_hooks_for_models,
    set_current_node_id,
    get_current_node_id,
)
from data_shuttle_bridge.sql.sync import SyncEngine, ConflictPolicy
from data_shuttle_bridge.sql.blueprints import sync_blueprint
from data_shuttle_bridge.sql.transport import InMemoryPeerTransport, HttpPeerTransport
from data_shuttle_bridge.sql.registry import (
    NodeRegistry,
    node_registry_blueprint,
    allocate_node_id,
)
from data_shuttle_bridge.sql.nodeid import ClientNodeManager
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.tenancy import (
    tenant_sync_blueprint_db_per_tenant,
    attach_change_hooks_mt_for_models,
    SyncEngineMT,
    ChangeLogMT,
    SyncStateMT,
    tenant_sync_blueprint_row_level,
)

# Multi-Tenant Service
from data_shuttle_bridge.sql.multi_tenant_service import (
    Tenant,
    TenantSecret,
    SecretManager,
    TenantManager,
    create_multi_tenant_app,
)
from data_shuttle_bridge.sql.bridge_client import (
    BridgeClient,
    BridgeAdminClient,
)

# File Backup
from data_shuttle_bridge.file_backup.runtime import (
    init_repo,
    run_backup,
    list_snapshots,
    run_restore,
)
from data_shuttle_bridge.file_backup.repo.repository import (
    Repository,
    Snapshot,
    FileEntry,
)
from data_shuttle_bridge.file_backup.pipeline.chunking import (
    ChunkingStrategy,
    FixedSizeChunker,
)

# P2P / Tunnel strategies
from data_shuttle_bridge.p2p.tunnel import (
    TunnelStrategy,
    SubprocessTunnelStrategy,
    TunnelPeerTransport,
    register_tunnel_strategy,
    get_tunnel_strategy_class,
    list_tunnel_strategies,
)
from data_shuttle_bridge.p2p.tunnel_wireguard import WireGuardTunnelStrategy
from data_shuttle_bridge.p2p.tunnel_cloudflared import CloudflaredTunnelStrategy
from data_shuttle_bridge.p2p.tunnel_ngrok import NgrokTunnelStrategy


__all__ = [
    # SQL
    "KSortedID",
    "set_id_generator",
    "get_id_generator",
    "clear_id_generator",
    "SyncRowSQLModelMixin",
    "SyncRowSAMixin",
    "ChangeLog",
    "SyncState",
    "attach_change_hooks",
    "attach_change_hooks_for_models",
    "set_current_node_id",
    "get_current_node_id",
    "SyncEngine",
    "ConflictPolicy",
    "sync_blueprint",
    "InMemoryPeerTransport",
    "HttpPeerTransport",
    "NodeRegistry",
    "node_registry_blueprint",
    "allocate_node_id",
    "ClientNodeManager",
    "build_schema",
    "tenant_sync_blueprint_db_per_tenant",
    "attach_change_hooks_mt_for_models",
    "SyncEngineMT",
    "ChangeLogMT",
    "SyncStateMT",
    "tenant_sync_blueprint_row_level",
    # Multi-Tenant Service
    "Tenant",
    "TenantSecret",
    "SecretManager",
    "TenantManager",
    "create_multi_tenant_app",
    "BridgeClient",
    "BridgeAdminClient",
    # File
    "init_repo",
    "run_backup",
    "list_snapshots",
    "run_restore",
    "Repository",
    "Snapshot",
    "FileEntry",
    "ChunkingStrategy",
    "FixedSizeChunker",
    # P2P / Tunnel strategies
    "TunnelStrategy",
    "SubprocessTunnelStrategy",
    "TunnelPeerTransport",
    "register_tunnel_strategy",
    "get_tunnel_strategy_class",
    "list_tunnel_strategies",
    "WireGuardTunnelStrategy",
    "CloudflaredTunnelStrategy",
    "NgrokTunnelStrategy",
]

__version__ = "0.0.1"
