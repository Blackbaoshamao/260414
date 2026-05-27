"""
DyStream-Watcher WebSocket Broadcast Server (万能接口)

Universal data port for external consumers (digital human, third-party programs, etc.).

Endpoint: ws://localhost:8765/ws/danmaku?token=dystream-local
Protocol: Server pushes JSON strings (one per danmaku message). Client only receives.
Auth: API key via `token` query parameter. Default: "dystream-local" (configurable via DYSTREAM_API_KEY env var).

JSON format (identical to Phase 2 decoder output):
  {"type": "chat",     "user_id": "...", "nickname": "...", "timestamp": 1744567234.5, "time": "2026-04-13T20:30:34", "content": "..."}
  {"type": "gift",     "user_id": "...", "nickname": "...", "timestamp": ..., "time": "...", "gift_name": "...", "gift_count": 1, "gift_value": 1, "gift_icon": "...", "gift_total": 1}
  {"type": "like",     "user_id": "...", "nickname": "...", "timestamp": ..., "time": "..."}
  {"type": "follow",   "user_id": "...", "nickname": "...", "timestamp": ..., "time": "..."}

Usage for third-party consumers:
  1. Connect: ws://localhost:8765/ws/danmaku?token=dystream-local
  2. Receive: JSON strings pushed in real-time
  3. No commands needed — server pushes only
"""

import asyncio
import json
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, WebSocketException, status
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger


API_KEY = os.environ.get("DYSTREAM_API_KEY", "dystream-local")


class ConnectionManager:
    """Manages active WebSocket connections for broadcast."""

    def __init__(self, max_connections: int = 20):
        self.active_connections: list[WebSocket] = []
        self._max_connections = max_connections

    async def connect(self, ws: WebSocket) -> None:
        if len(self.active_connections) >= self._max_connections:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Max connections reached")
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Max connections")
        await ws.accept()
        self.active_connections.append(ws)
        logger.info("WS client connected (total: {})", len(self.active_connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active_connections:
            self.active_connections.remove(ws)
            logger.info("WS client disconnected (total: {})", len(self.active_connections))

    async def broadcast(self, message: str) -> None:
        disconnected = []
        for conn in list(self.active_connections):
            try:
                await asyncio.wait_for(conn.send_text(message), timeout=5.0)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.disconnect(conn)


def create_app(queue: asyncio.Queue) -> FastAPI:
    """Create FastAPI app with WebSocket broadcast endpoint.

    Args:
        queue: asyncio.Queue consumed by the broadcaster background task.
    """
    manager = ConnectionManager()
    broadcaster_task = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal broadcaster_task
        async def broadcaster():
            while True:
                msg = await queue.get()
                json_str = json.dumps(msg, ensure_ascii=False)
                await manager.broadcast(json_str)

        broadcaster_task = asyncio.create_task(broadcaster())
        yield
        if broadcaster_task:
            broadcaster_task.cancel()

    app = FastAPI(title="DyStream-Watcher", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws/danmaku")
    async def ws_danmaku(websocket: WebSocket, token: str = Query(...)):
        if token != API_KEY:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
            return
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(websocket)

    @app.get("/api/status")
    async def status_endpoint():
        return {"status": "running", "clients": len(manager.active_connections)}

    return app


async def run_ws_server(queue: asyncio.Queue, host: str = "0.0.0.0", port: int = 8765) -> None:
    """Run FastAPI WebSocket server consuming from queue.

    This is a long-running coroutine meant to be started as an asyncio task.
    """
    import uvicorn

    app = create_app(queue)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("WebSocket server starting on ws://{}:{}", host, port)
    await server.serve()
