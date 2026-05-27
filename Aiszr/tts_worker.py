"""Async TTS queue worker with priority, interrupt and timeout fallback."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass(order=True)
class _TTSJob:
    sort_key: tuple[int, float]
    reply: dict = field(compare=False)
    interrupt: bool = field(default=False, compare=False)


class TTSWorker:
    def __init__(
        self,
        on_speech: Callable[[dict], Awaitable[None]],
        synthesizer: Callable[[dict], Awaitable[dict]] | None = None,
        timeout_ms: int = 3000,
        queue_size: int = 200,
    ):
        self._on_speech = on_speech
        self._synthesizer = synthesizer
        self._timeout_ms = timeout_ms
        self._queue_size = queue_size
        self._queue: asyncio.PriorityQueue[_TTSJob] = asyncio.PriorityQueue(maxsize=queue_size)
        self._runner: asyncio.Task | None = None
        self._current: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._current and not self._current.done():
            self._current.cancel()
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except BaseException:
                pass

    def enqueue(self, reply: dict, interrupt: bool = False) -> bool:
        """Enqueue reply for TTS. Must be called from the same asyncio event loop thread."""
        if self._queue.full():
            return False
        prio = int(reply.get("priority", 5))
        # Lower sort value => higher priority.
        sort_key = (-prio, time.time())
        if interrupt and self._current and not self._current.done():
            self._current.cancel()
        self._queue.put_nowait(_TTSJob(sort_key=sort_key, reply=reply, interrupt=interrupt))
        return True

    async def _synthesize(self, reply: dict) -> dict:
        if self._synthesizer is not None:
            return await self._synthesizer(reply)
        # Placeholder TTS synthesis: keep text path alive for MVP.
        text = str(reply.get("text", "")).strip()
        if len(text) > 120:
            text = text[:119] + "…"
        await asyncio.sleep(0.01)
        now_ms = int(time.time() * 1000)
        return {
            "type": "tts",
            "speak_id": uuid.uuid4().hex,
            "reply_id": reply.get("reply_id", ""),
            "room_id": reply.get("room_id", ""),
            "text": text,
            "status": "ok",
            "timestamp": now_ms / 1000.0,
            "time": reply.get("time", ""),
            "ts_ms": now_ms,
        }

    async def _emit_timeout(self, reply: dict) -> None:
        now_ms = int(time.time() * 1000)
        await self._on_speech(
            {
                "type": "tts",
                "speak_id": uuid.uuid4().hex,
                "reply_id": reply.get("reply_id", ""),
                "room_id": reply.get("room_id", ""),
                "text": str(reply.get("text", ""))[:60],
                "status": "timeout",
                "timestamp": now_ms / 1000.0,
                "time": reply.get("time", ""),
                "ts_ms": now_ms,
            }
        )

    async def _emit_interrupted(self, reply: dict) -> None:
        now_ms = int(time.time() * 1000)
        await self._on_speech(
            {
                "type": "tts",
                "speak_id": uuid.uuid4().hex,
                "reply_id": reply.get("reply_id", ""),
                "room_id": reply.get("room_id", ""),
                "text": str(reply.get("text", ""))[:60],
                "status": "interrupted",
                "timestamp": now_ms / 1000.0,
                "time": reply.get("time", ""),
                "ts_ms": now_ms,
            }
        )

    async def _run(self) -> None:
        try:
            while self._running:
                job = await self._queue.get()
                try:
                    self._current = asyncio.create_task(self._synthesize(job.reply))
                    speech = await asyncio.wait_for(self._current, timeout=self._timeout_ms / 1000.0)
                    await self._on_speech(speech)
                except asyncio.TimeoutError:
                    await self._emit_timeout(job.reply)
                except asyncio.CancelledError:
                    await self._emit_interrupted(job.reply)
                except Exception:
                    await self._emit_timeout(job.reply)
                finally:
                    self._current = None
        except asyncio.CancelledError:
            return
