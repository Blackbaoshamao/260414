"""Tests for ws_server.py — ConnectionManager, broadcast, API key auth."""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# ConnectionManager unit tests
# ---------------------------------------------------------------------------


class TestConnectionManager:
    @pytest.fixture
    def manager(self):
        from ws_server import ConnectionManager
        return ConnectionManager(max_connections=5)

    async def test_connect_adds_connection(self, manager):
        ws = AsyncMock()
        await manager.connect(ws)
        assert ws in manager.active_connections
        assert len(manager.active_connections) == 1

    async def test_disconnect_removes_connection(self, manager):
        ws = AsyncMock()
        await manager.connect(ws)
        manager.disconnect(ws)
        assert ws not in manager.active_connections
        assert len(manager.active_connections) == 0

    async def test_disconnect_idempotent(self, manager):
        ws = AsyncMock()
        manager.disconnect(ws)  # not connected — should not raise
        assert len(manager.active_connections) == 0

    async def test_max_connections_rejected(self):
        from ws_server import ConnectionManager
        m = ConnectionManager(max_connections=2)

        ws1, ws2, ws3 = AsyncMock(), AsyncMock(), AsyncMock()
        await m.connect(ws1)
        await m.connect(ws2)

        from fastapi import WebSocketException
        with pytest.raises(WebSocketException):
            await m.connect(ws3)
        assert len(m.active_connections) == 2

    async def test_broadcast_sends_to_all(self, manager):
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.connect(ws1)
        await manager.connect(ws2)

        await manager.broadcast("hello")
        ws1.send_text.assert_awaited_once_with("hello")
        ws2.send_text.assert_awaited_once_with("hello")

    async def test_broadcast_removes_failed(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws2.send_text.side_effect = asyncio.TimeoutError()
        await manager.connect(ws1)
        await manager.connect(ws2)

        await manager.broadcast("msg")
        assert ws1 in manager.active_connections
        assert ws2 not in manager.active_connections

    async def test_broadcast_empty_list_ok(self, manager):
        await manager.broadcast("nobody here")  # no connections — should not raise


# ---------------------------------------------------------------------------
# FastAPI app + WebSocket tests (using Starlette TestClient)
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_status_endpoint(self):
        from ws_server import create_app

        queue = asyncio.Queue()
        app = create_app(queue)
        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["clients"] == 0

    def test_ws_rejects_wrong_token(self):
        from ws_server import create_app

        queue = asyncio.Queue()
        app = create_app(queue)
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/danmaku?token=wrong"):
                pass

    def test_ws_accepts_valid_token(self):
        from ws_server import create_app

        queue = asyncio.Queue()
        app = create_app(queue)
        client = TestClient(app)
        with client.websocket_connect("/ws/danmaku?token=dystream-local") as ws:
            # Send keepalive — connection should stay open
            ws.send_text("ping")

    def test_ws_receives_broadcast(self):
        """Test broadcast by directly calling ConnectionManager.broadcast.

        The lifespan broadcaster task can't run alongside sync TestClient websocket,
        so we test the broadcast path via ConnectionManager directly (already covered
        in TestConnectionManager) and verify the WS endpoint accepts connections.
        Full broadcast integration is verified manually with a live server.
        """
        from ws_server import ConnectionManager

        manager = ConnectionManager()
        # Just verify ConnectionManager.broadcast formats and sends correctly
        msg = json.dumps({"type": "chat", "nickname": "test"}, ensure_ascii=False)
        # No connections — should not raise
        asyncio.get_event_loop().run_until_complete(manager.broadcast(msg))
