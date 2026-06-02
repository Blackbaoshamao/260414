import asyncio
import contextlib
import inspect
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable


MIN_WAIT_SEC = 0.05


@dataclass(frozen=True)
class _Insertion:
    wav_path: str
    text: str


def wav_duration_sec(path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                return 0.0
            return wav_file.getnframes() / frame_rate
    except Exception:
        return 0.0


class DigitalHumanSpeechScheduler:
    def __init__(
        self,
        *,
        anchor_segments: Iterable[str | Path],
        send_wav: Callable[[str | Path], Awaitable[None]],
        duration_fn: Callable[[str | Path], float] | None = None,
        log_callback: Callable[[str], object] | None = None,
        max_insertions: int = 20,
    ):
        self._anchor_segments = list(anchor_segments)
        if not self._anchor_segments:
            raise ValueError("anchor_segments must not be empty")

        self._send_wav = send_wav
        self._duration_fn = duration_fn or wav_duration_sec
        self._log_callback = log_callback
        self._insertions: asyncio.Queue[_Insertion] = asyncio.Queue(
            maxsize=max(1, int(max_insertions))
        )
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._anchor_index = 0

    def start(self) -> asyncio.Task[None]:
        if self._task is not None:
            if not self._task.done():
                return self._task
            self._consume_done_task_exception(self._task)

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name="digital-human-speech-scheduler"
        )
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return

        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def enqueue_insertion(self, wav_path, *, text: str = "") -> bool:
        # Same-event-loop API: callers from other threads must marshal this call
        # via the scheduler loop, for example with loop.call_soon_threadsafe().
        insertion = _Insertion(str(wav_path), text)
        try:
            self._insertions.put_nowait(insertion)
        except asyncio.QueueFull:
            self._log(
                f"digital human insertion queue is full; dropped {insertion.wav_path}"
            )
            return False
        return True

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            insertion = self._next_insertion()
            if insertion is not None:
                await self._send_insertion(insertion)
                continue

            anchor_path = self._next_anchor_path()
            try:
                await self._send_wav(anchor_path)
            except Exception as exc:
                self._log(f"digital human anchor send failed: {anchor_path}: {exc}")
                raise

            await self._wait_for_duration(anchor_path)

    def _next_insertion(self) -> _Insertion | None:
        try:
            return self._insertions.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _next_anchor_path(self):
        path = self._anchor_segments[self._anchor_index]
        self._anchor_index = (self._anchor_index + 1) % len(self._anchor_segments)
        return path

    async def _send_insertion(self, insertion: _Insertion) -> None:
        try:
            await self._send_wav(insertion.wav_path)
        except Exception as exc:
            self._log(
                f"digital human insertion send failed: {insertion.wav_path}: {exc}"
            )
            return

        await self._wait_for_duration(insertion.wav_path)

    async def _wait_for_duration(self, wav_path) -> None:
        duration = self._duration_sec(wav_path)
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=max(duration, MIN_WAIT_SEC)
            )
        except asyncio.TimeoutError:
            pass

    def _duration_sec(self, wav_path) -> float:
        try:
            return float(self._duration_fn(wav_path))
        except Exception as exc:
            self._log(f"digital human wav duration failed: {wav_path}: {exc}")
            return 0.0

    def _log(self, message: str) -> None:
        if self._log_callback is None:
            return

        try:
            result = self._log_callback(message)
        except Exception:
            return

        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                return
            loop.create_task(result)

    def _consume_done_task_exception(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.exception()
