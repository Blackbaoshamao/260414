import asyncio

import pytest

from tts_worker import TTSWorker


@pytest.mark.asyncio
async def test_tts_worker_emits_ok_event():
    emitted = []

    async def _on_speech(evt):
        emitted.append(evt)

    worker = TTSWorker(on_speech=_on_speech, timeout_ms=1000, queue_size=10)
    await worker.start()
    ok = worker.enqueue({"reply_id": "r1", "text": "hello", "priority": 5, "room_id": "x"})
    assert ok is True
    await asyncio.sleep(0.05)
    await worker.stop()

    assert emitted
    assert emitted[0]["type"] == "tts"
    assert emitted[0]["status"] in {"ok", "timeout", "interrupted"}


@pytest.mark.asyncio
async def test_tts_worker_timeout_fallback():
    emitted = []

    async def _on_speech(evt):
        emitted.append(evt)

    worker = TTSWorker(on_speech=_on_speech, timeout_ms=1, queue_size=10)

    async def _slow(reply):
        await asyncio.sleep(0.1)
        return {"type": "tts", "status": "ok", "reply_id": reply.get("reply_id", "")}

    worker._synthesize = _slow  # test override
    await worker.start()
    worker.enqueue({"reply_id": "r2", "text": "slow", "priority": 5, "room_id": "x"})
    await asyncio.sleep(0.05)
    await worker.stop()

    assert emitted
    assert emitted[0]["status"] == "timeout"
