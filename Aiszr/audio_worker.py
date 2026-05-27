"""Non-blocking audio playback queue using pygame.mixer."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from loguru import logger


class AudioWorker:
    """Threaded audio playback queue with status callbacks.

    Usage:
        worker = AudioWorker(on_status=my_status_handler)
        worker.start()
        worker.enqueue("path/to/audio.wav")
        # ... later
        worker.stop()
    """

    def __init__(self, on_status: Callable[[dict], None] | None = None):
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._on_status = on_status
        self._playing = False
        self._current_path = ""

    def start(self) -> None:
        if self._running:
            return
        try:
            import pygame
            pygame.mixer.init(frequency=16000, size=-16, channels=1, buffer=2048)
            pygame.mixer.music.set_volume(1.0)
        except Exception as e:
            logger.error("Failed to init pygame.mixer: {}", e)
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass

    def enqueue(self, path: str) -> bool:
        if not Path(path).exists():
            logger.warning("Audio file not found: {}", path)
            return False
        self._queue.put(path)
        return True

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_path(self) -> str:
        return self._current_path

    def _run(self) -> None:
        import pygame
        while self._running:
            item = self._queue.get()
            if item is None:
                break
            self._play(item)
        self._running = False

    def _play(self, path: str) -> None:
        import pygame
        self._current_path = path
        self._playing = True
        self._emit("playing", path)
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and self._running:
                time.sleep(0.05)
        except Exception as e:
            logger.error("Audio playback error: {}", e)
            self._emit("error", path, str(e))
        finally:
            self._playing = False
            self._current_path = ""
            self._emit("finished", path)

    def _emit(self, status: str, path: str, detail: str = "") -> None:
        if self._on_status:
            try:
                self._on_status({
                    "status": status,
                    "path": path,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                pass


async def play_with_audio_worker(worker: AudioWorker, path: str) -> None:
    """Async wrapper: enqueue audio and wait until playback finishes."""
    event = asyncio.Event()

    def on_status(msg: dict) -> None:
        if msg["status"] in ("finished", "error"):
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(event.set)
            except Exception:
                pass

    original_callback = worker._on_status

    def combined(msg: dict) -> None:
        on_status(msg)
        if original_callback:
            original_callback(msg)

    worker._on_status = combined
    worker.enqueue(path)
    await event.wait()
    worker._on_status = original_callback
