from typing import Optional

from flask import Blueprint, request, jsonify

from sqlmodel import SQLModel, Field, Session, select, Column as SQLModelColumn

from sqlalchemy import Integer, String, UniqueConstraint

from data_shuttle_bridge.models.registry import NodeRegistry, MAX_NODE


def allocate_node_id(sess: Session, device_key: str) -> int:
    existing = sess.exec(
        select(NodeRegistry).where(NodeRegistry.device_key == device_key)
    ).first()
    if existing:
        return existing.node_id
    used = set(sess.exec(select(NodeRegistry.node_id)).all())
    for candidate in range(1, MAX_NODE + 1):
        if candidate not in used:
            entry = NodeRegistry(device_key=device_key, node_id=candidate)
            sess.add(entry)
            sess.commit()
            return candidate
    raise RuntimeError("No available node_id slots")


def node_registry_blueprint(session_factory):
    bp = Blueprint("node_registry", __name__)

    @bp.post("/node/register")
    def register():
        payload = request.get_json(force=True) or {}
        device_key = payload.get("device_key")
        if not device_key or not isinstance(device_key, str) or len(device_key) > 64:
            return jsonify({"error": "invalid device_key"}), 400
        with session_factory() as sess:
            node_id = allocate_node_id(sess, device_key)
            return jsonify({"node_id": node_id})

    return bp
