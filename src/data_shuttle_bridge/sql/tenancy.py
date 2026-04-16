from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Type, Dict, List, Set, Tuple

from flask import Blueprint, request, jsonify, g

from sqlmodel import SQLModel, Field, select, Column as SQLModelColumn, Session

from sqlalchemy import event, String as SA_String, Integer as SA_Integer, JSON

from data_shuttle_bridge.models.typing_ import ChangePayload
from data_shuttle_bridge.models.payloads import TableSchema
from data_shuttle_bridge.models.enums import ConflictPolicy
from data_shuttle_bridge.models.tenancy import ChangeLogMT, SyncStateMT
from data_shuttle_bridge.sql.payloads import apply_row
from data_shuttle_bridge.sql.schema import build_schema
from data_shuttle_bridge.sql.wiring import _summary

# -------------------------------
# A) DATABASE-PER-TENANT (recommended)
# -------------------------------


def tenant_sync_blueprint_db_per_tenant(
    session_factory_for_tenant: Callable[[str], Session],
    models: Iterable[Type],
    peer_id_namer: Callable[[str], str] | None = None,
    policy: ConflictPolicy = ConflictPolicy.LWW,
):
    """
    A sync blueprint where each tenant has its own engine/database.
    No schema changes required. We just route to a per-tenant Session.

    - session_factory_for_tenant(tenant) -> Session
    - models: list of model classes (SQLAlchemy or SQLModel)
    - peer_id_namer: if provided, builds the peer_id used in SyncState per tenant
    """
    from .sync import SyncEngine  # avoid cycle
    from .wiring import attach_change_hooks_for_models

    bp = Blueprint("sync_mt_db", __name__)
    models = list(models)
    attach_change_hooks_for_models(models)
    SCHEMA = build_schema(models)

    def _tenant() -> str:
        t = request.args.get("tenant") or getattr(g, "tenant", None)
        if not t:
            return "default"
        return str(t)

    def _engine_for(tenant: str) -> SyncEngine:
        sess = session_factory_for_tenant(tenant)
        peer = peer_id_namer(tenant) if peer_id_namer else f"peer:{tenant}"
        return SyncEngine(session=sess, peer_id=peer, schema=SCHEMA, policy=policy)

    @bp.get("/sync/changes")
    def get_changes():
        tenant = _tenant()
        eng = _engine_for(tenant)
        since_id = int(request.args.get("since_id", "0"))
        limit = int(request.args.get("limit", "1000"))
        changes = eng.local_changes_since(since_id, limit=limit)
        return jsonify({"changes": changes})

    @bp.post("/sync/apply")
    def apply():
        tenant = _tenant()
        eng = _engine_for(tenant)
        payload = request.get_json(force=True) or {}
        changes: list[ChangePayload] = payload.get("changes", [])
        eng.apply_remote_changes(changes)
        eng.sess.commit()
        return jsonify({"ok": True})

    @bp.post("/sync/ack")
    def ack():
        # no-op; kept for API symmetry
        return jsonify({"ok": True})

    return bp


# -------------------------------
# B) ROW-LEVEL TENANCY (single DB) – optional
# -------------------------------


def attach_change_hooks_mt_for_models(
    models: Iterable[Type], current_tenant: Callable[[], str]
):
    """
    Like attach_change_hooks_for_models, but writes to ChangeLogMT with tenant tag.
    """

    def _log_mt(connection, table: str, pk: int, op: str, version: int, summary):
        tenant = current_tenant() or "default"
        connection.execute(
            ChangeLogMT.__table__.insert().values(
                tenant=tenant,
                table=table,
                pk=pk,
                op=op,
                version=version,
                summary=summary,
            )
        )

    for model in models:
        table = getattr(model, "__table__", None)
        table_name = getattr(model, "__tablename__", None) or (
            table.name if table is not None else None
        )
        if not table_name:
            raise ValueError(f"Model {model} has no table mapping")

        @event.listens_for(model, "before_update", propagate=True)
        def _bump_version(mapper, connection, target):
            cur = getattr(target, "version", 1)
            try:
                next_v = int(cur) + 1
            except Exception:
                next_v = 1
            setattr(target, "version", next_v)

        @event.listens_for(model, "after_insert")
        def _after_insert(mapper, connection, target, table_name=table_name):
            _log_mt(
                connection,
                table_name,
                int(getattr(target, "id")),
                "I",
                int(getattr(target, "version", 1)),
                _summary(target),
            )

        @event.listens_for(model, "after_update")
        def _after_update(mapper, connection, target, table_name=table_name):
            _log_mt(
                connection,
                table_name,
                int(getattr(target, "id")),
                "U",
                int(getattr(target, "version", 1)),
                _summary(target),
            )

        @event.listens_for(model, "after_delete")
        def _after_delete(mapper, connection, target, table_name=table_name):
            _log_mt(
                connection,
                table_name,
                int(getattr(target, "id")),
                "D",
                int(getattr(target, "version", 1)),
                None,
            )


class SyncEngineMT:
    """
    Tenant-scoped SyncEngine variant for single-DB (row-level tenancy).
    Filters change log and maintains per-tenant watermarks.
    """

    def __init__(
        self,
        session: Session,
        tenant: str,
        peer_id: str,
        schema: Dict[str, TableSchema],
        policy: ConflictPolicy = ConflictPolicy.LWW,
    ):
        from .payloads import apply_row  # reuse same apply

        self.sess = session
        self.tenant = tenant
        self.peer_id = peer_id
        self.schema = schema
        self.policy = policy
        self._order = self._compute_order()

    def _compute_order(self) -> List[str]:
        deps: Dict[str, Set[str]] = {
            name: set(ts.parents) for name, ts in self.schema.items()
        }
        indeg: Dict[str, int] = {name: 0 for name in deps}
        for n in deps:
            for p in deps[n]:
                if p in deps:
                    indeg[n] += 1
        from collections import deque

        q = deque([n for n, d in indeg.items() if d == 0])
        out: List[str] = []
        while q:
            u = q.popleft()
            out.append(u)
            for v in deps:
                if u in deps[v]:
                    indeg[v] -= 1
                    if indeg[v] == 0:
                        q.append(v)
        if len(out) != len(deps):
            remain = [n for n in deps if n not in out]
            out.extend(remain)
        return out

    def _serialize_change(self, ch: ChangeLogMT) -> ChangePayload:
        ts = self.schema[ch.table]
        data = None
        if ch.op in ("I", "U"):
            obj = self.sess.get(ts.model, ch.pk)
            if obj:
                data = {f: getattr(obj, f) for f in ts.fields}
        return {
            "id": ch.id,  # type: ignore
            "table": ch.table,
            "pk": ch.pk,
            "op": ch.op,  # type: ignore
            "version": ch.version,
            "data": data,
            "at": None,
        }

    def _ensure_state(self) -> SyncStateMT:
        st = self.sess.get(
            SyncStateMT, {"tenant": self.tenant, "peer_id": self.peer_id}
        )
        if not st:
            st = SyncStateMT(
                tenant=self.tenant,
                peer_id=self.peer_id,
                last_pushed_change_id=0,
                last_pulled_change_id=0,
            )
            self.sess.add(st)
            self.sess.commit()
        return st

    def local_changes_since(
        self, since_id: int, limit: int = 1000
    ) -> List[ChangePayload]:
        rows = self.sess.exec(
            select(ChangeLogMT)
            .where(
                (ChangeLogMT.tenant == self.tenant) & (ChangeLogMT.id > since_id)  # type: ignore
            )
            .order_by(ChangeLogMT.id.asc())
            .limit(limit)
        ).all()
        return [self._serialize_change(r) for r in rows]

    def _apply_one(self, cp: ChangePayload):
        ts = self.schema[cp["table"]]
        model = ts.model
        obj = self.sess.get(model, cp["pk"])
        if cp["op"] == "D":
            if obj:
                self.sess.delete(obj)
            return
        incoming_version = cp["version"]
        if obj is None:
            obj = model(id=cp["pk"])  # type: ignore
            self.sess.add(obj)
            if cp["data"]:
                apply_row(obj, cp["data"])
            setattr(obj, "version", incoming_version)
            return
        current_version = getattr(obj, "version", 1)
        if (
            self.policy == ConflictPolicy.VERSION
            and incoming_version <= current_version
        ):
            return
        if cp["data"]:
            apply_row(obj, cp["data"])
        setattr(obj, "version", max(current_version, incoming_version))

    def apply_remote_changes(self, changes: Iterable[ChangePayload]):
        from collections import defaultdict

        by_table: Dict[str, List[ChangePayload]] = defaultdict(list)
        for c in changes:
            by_table[c["table"]].append(c)
        for table in self._order:
            for c in by_table.get(table, []):
                self._apply_one(c)

    def pull_then_push(self, peer_transport, batch: int = 1000) -> Tuple[int, int]:
        st = self._ensure_state()

        pulled = 0
        while True:
            remote_changes = peer_transport.get_changes_since(
                st.last_pulled_change_id, limit=batch
            )
            if not remote_changes:
                break
            self.apply_remote_changes(remote_changes)
            self.sess.commit()
            st.last_pulled_change_id = remote_changes[-1]["id"]
            self.sess.add(st)
            self.sess.commit()
            peer_transport.ack(st.last_pulled_change_id)
            pulled += len(remote_changes)

        pushed = 0
        while True:
            out = self.local_changes_since(st.last_pushed_change_id, limit=batch)
            if not out:
                break
            peer_transport.apply_changes(out)
            st.last_pushed_change_id = out[-1]["id"]
            self.sess.add(st)
            self.sess.commit()
            pushed += len(out)

        return pulled, pushed


def tenant_sync_blueprint_row_level(
    session_factory: Callable[[], Session],
    models: Iterable[Type],
    tenant_resolver: Callable[[], str],
    policy: ConflictPolicy = ConflictPolicy.LWW,
):
    """
    Sync blueprint for single-DB, row-level multi-tenancy.
    Requires ChangeLogMT/SyncStateMT tables (create_all).
    """
    bp = Blueprint("sync_mt_row", __name__)
    models = list(models)
    schema = build_schema(models)
    attach_change_hooks_mt_for_models(models, current_tenant=tenant_resolver)

    def _eng() -> SyncEngineMT:
        sess = session_factory()
        tenant = tenant_resolver() or "default"
        return SyncEngineMT(
            session=sess,
            tenant=tenant,
            peer_id=f"peer:{tenant}",
            schema=schema,
            policy=policy,
        )

    @bp.get("/sync/changes")
    def get_changes():
        eng = _eng()
        since_id = int(request.args.get("since_id", "0"))
        limit = int(request.args.get("limit", "1000"))
        changes = eng.local_changes_since(since_id, limit=limit)
        return jsonify({"changes": changes})

    @bp.post("/sync/apply")
    def apply():
        eng = _eng()
        payload = request.get_json(force=True) or {}
        changes: list[ChangePayload] = payload.get("changes", [])
        eng.apply_remote_changes(changes)
        eng.sess.commit()
        return jsonify({"ok": True})

    @bp.post("/sync/ack")
    def ack():
        return jsonify({"ok": True})

    return bp
