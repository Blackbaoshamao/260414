"""Embedded localhost RTMP server for digital human streaming pipeline."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from loguru import logger


@dataclass(slots=True)
class RTMPServerConfig:
    host: str = "127.0.0.1"
    port: int = 1935
    app_name: str = "live"
    stream_key: str = "digital_human"

    @property
    def rtmp_url(self) -> str:
        return f"rtmp://{self.host}:{self.port}/{self.app_name}/{self.stream_key}"


class _PublisherController:
    """Subclass of pyrtmp's SimpleRTMPController that tracks publish state."""

    def __init__(self, on_publish_cb, on_unpublish_cb):
        self._on_publish_cb = on_publish_cb
        self._on_unpublish_cb = on_unpublish_cb

    def create_controller(self):
        from pyrtmp.rtmp import SimpleRTMPController

        parent = self

        class _Controller(SimpleRTMPController):
            async def on_ns_publish(self_inner, session, message):
                await super().on_ns_publish(session, message)
                await parent._on_publish_cb(message.publishing_name)

            async def on_stream_closed(self_inner, session, exception):
                await parent._on_unpublish_cb()
                await super().on_stream_closed(session, exception)

        return _Controller()


class DigitalHumanRTMPServer:
    """Manages a pyrtmp-based RTMP server on localhost.

    Usage:
        server = DigitalHumanRTMPServer()
        await server.start()
        # ffmpeg pushes to server.rtmp_url
        # OBS Media Source reads from server.rtmp_url
        await server.stop()
    """

    def __init__(self, config: RTMPServerConfig | None = None):
        self._config = config or RTMPServerConfig()
        self._server: asyncio.Server | None = None
        self._running = False
        self._publisher_connected = False

    @property
    def rtmp_url(self) -> str:
        return self._config.rtmp_url

    @property
    def is_running(self) -> bool:
        return self._running and self._server is not None

    @property
    def has_publisher(self) -> bool:
        return self._publisher_connected

    async def start(self) -> None:
        if self._running:
            return

        try:
            from pyrtmp.rtmp import RTMPProtocol
        except ImportError:
            raise ImportError("pyrtmp 未安装。请运行: pip install pyrtmp")

        handler = _PublisherController(
            on_publish_cb=self._on_publish,
            on_unpublish_cb=self._on_unpublish,
        )
        controller = handler.create_controller()

        self._server = await asyncio.get_event_loop().create_server(
            lambda: RTMPProtocol(controller),
            self._config.host,
            self._config.port,
        )
        self._running = True
        logger.info("RTMP server started at {}", self.rtmp_url)

    async def stop(self) -> None:
        self._running = False
        self._publisher_connected = False
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        logger.info("RTMP server stopped")

    async def _on_publish(self, stream_name: str):
        logger.info("RTMP publisher connected: {}", stream_name)
        self._publisher_connected = True

    async def _on_unpublish(self):
        logger.info("RTMP publisher disconnected")
        self._publisher_connected = False

