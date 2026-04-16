"""Centralized model definitions for data_shuttle_bridge."""

from data_shuttle_bridge.models.enums import (
    ConflictPolicy,
    SyncScope,
    FilterOperator,
)
from data_shuttle_bridge.models.typing_ import ChangePayload, Op
from data_shuttle_bridge.models.changelog import ChangeLog, SyncState
from data_shuttle_bridge.models.versioning import (
    SchemaSet,
    SchemaVersion,
    SchemaDiff,
    MappingRule,
    ConsolidationView,
)
from data_shuttle_bridge.models.tenant import Tenant, TenantSecret
from data_shuttle_bridge.models.tenancy import ChangeLogMT, SyncStateMT
from data_shuttle_bridge.models.registry import NodeRegistry, MAX_NODE
from data_shuttle_bridge.models.mapping import (
    MappingRuleBase,
    RenameRule,
    CastRule,
    ExpressionRule,
    DropRule,
)
from data_shuttle_bridge.models.policy import ColumnDefault
from data_shuttle_bridge.models.diffing import DiffRecord
from data_shuttle_bridge.models.sync_config import (
    FilterCondition,
    FilterExpression,
    TableSyncRule,
    SchemaSyncConfig,
    SyncConfig,
)
from data_shuttle_bridge.models.nodeid import ClientNodeConfig
from data_shuttle_bridge.models.mixins import SyncRowSQLModelMixin, SyncRowSAMixin
from data_shuttle_bridge.models.payloads import TableSchema
from data_shuttle_bridge.models.p2p import (
    WireGuardIdentity,
    WireGuardPeerConfig,
    EndpointInfo,
)
from data_shuttle_bridge.models.file_backup import FileEntry, Snapshot

__all__ = [
    # Enums
    "ConflictPolicy",
    "SyncScope",
    "FilterOperator",
    # Typing
    "ChangePayload",
    "Op",
    # Changelog
    "ChangeLog",
    "SyncState",
    # Versioning
    "SchemaSet",
    "SchemaVersion",
    "SchemaDiff",
    "MappingRule",
    "ConsolidationView",
    # Tenant
    "Tenant",
    "TenantSecret",
    # Tenancy (multi-tenant)
    "ChangeLogMT",
    "SyncStateMT",
    # Registry
    "NodeRegistry",
    "MAX_NODE",
    # Mapping rules
    "MappingRuleBase",
    "RenameRule",
    "CastRule",
    "ExpressionRule",
    "DropRule",
    # Policy
    "ColumnDefault",
    # Diffing
    "DiffRecord",
    # Sync config
    "FilterCondition",
    "FilterExpression",
    "TableSyncRule",
    "SchemaSyncConfig",
    "SyncConfig",
    # Node ID
    "ClientNodeConfig",
    # Mixins
    "SyncRowSQLModelMixin",
    "SyncRowSAMixin",
    # Payloads
    "TableSchema",
    # P2P
    "WireGuardIdentity",
    "WireGuardPeerConfig",
    "EndpointInfo",
    # File backup
    "FileEntry",
    "Snapshot",
]
