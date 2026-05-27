import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def capture_worker(monkeypatch):
    from ui import CaptureWorker

    worker = CaptureWorker()
    worker._async_set_obs_action_settings = AsyncMock()
    worker._async_set_voice_settings = AsyncMock()

    async def fake_ws_server(_queue):
        return None

    monkeypatch.setattr("ws_server.run_ws_server", fake_ws_server)
    return worker


async def test_worker_init_does_not_start_browser(capture_worker, monkeypatch):
    import fetcher

    capture_worker._loop = asyncio.get_running_loop()
    startup = AsyncMock()
    monkeypatch.setattr(fetcher, "startup", startup)

    await capture_worker._async_init()

    startup.assert_not_called()
    assert capture_worker._pw is None
    assert capture_worker._context is None
    assert capture_worker._live_capture_state == "disabled"

    if capture_worker._ws_task:
        capture_worker._ws_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await capture_worker._ws_task


async def test_enabling_live_capture_starts_browser(capture_worker, monkeypatch):
    import fetcher

    capture_worker._loop = asyncio.get_running_loop()
    pw = AsyncMock()
    context = AsyncMock()
    startup = AsyncMock(return_value=(pw, context))
    monkeypatch.setattr(fetcher, "startup", startup)

    await capture_worker._async_set_live_capture_enabled(True)

    startup.assert_awaited_once()
    assert capture_worker._pw is pw
    assert capture_worker._context is context
    assert capture_worker._live_capture_enabled is True
    assert capture_worker._live_capture_state == "ready"

    await capture_worker._async_set_live_capture_enabled(False)

    context.close.assert_awaited_once()
    pw.stop.assert_awaited_once()
    assert capture_worker._context is None
    assert capture_worker._pw is None
    assert capture_worker._live_capture_state == "disabled"
