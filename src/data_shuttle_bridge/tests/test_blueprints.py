"""Tests for Flask sync_blueprint."""

import json
import pytest
from unittest.mock import MagicMock, patch

from flask import Flask

from data_shuttle_bridge.sql.blueprints import sync_blueprint


@pytest.fixture
def app():
    app = Flask(__name__)
    mock_engine = MagicMock()
    mock_engine.remote_changes_since.return_value = [
        {
            "id": 1,
            "table": "users",
            "op": "I",
            "pk": 1,
            "data": {"name": "Alice"},
            "version": 1,
            "at": None,
        }
    ]
    mock_engine.apply_remote_changes.return_value = None
    mock_engine.sess = MagicMock()

    bp = sync_blueprint(lambda: mock_engine)
    app.register_blueprint(bp)
    app.config["TESTING"] = True
    return app, mock_engine


class TestSyncBlueprint:
    def test_get_changes(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            resp = client.get("/sync/changes?since_id=0&limit=100")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "changes" in data
            assert len(data["changes"]) == 1
            engine.remote_changes_since.assert_called_once()

    def test_get_changes_defaults(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            resp = client.get("/sync/changes")
            assert resp.status_code == 200
            # Default since_id=0, limit=1000
            call_args = engine.remote_changes_since.call_args
            assert call_args.args[0] == 0
            assert call_args.kwargs["limit"] == 1000

    def test_get_changes_with_exclude_node_id(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            resp = client.get("/sync/changes?exclude_node_id=node_A")
            assert resp.status_code == 200
            call_args = engine.remote_changes_since.call_args
            assert call_args.kwargs["exclude_node_id"] == "node_A"

    def test_apply_changes(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            changes = [{"id": 1, "table": "users", "op": "I"}]
            resp = client.post(
                "/sync/apply",
                data=json.dumps({"changes": changes}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            engine.apply_remote_changes.assert_called_once_with(changes)
            engine.sess.commit.assert_called_once()

    def test_apply_empty(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            resp = client.post(
                "/sync/apply",
                data=json.dumps({}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            engine.apply_remote_changes.assert_called_once_with([])

    def test_ack(self, app):
        flask_app, engine = app
        with flask_app.test_client() as client:
            resp = client.post(
                "/sync/ack",
                data=json.dumps({"change_id": 10}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
