"""Local audio playback helpers for generated voice files."""

from __future__ import annotations

import asyncio
import contextlib
import platform
import struct
import threading
import time
from pathlib import Path

_stop_event = threading.Event()


def stop_all_audio() -> None:
    _stop_event.set()
    if platform.system().lower() == "windows":
        import winsound
        with contextlib.suppress(Exception):
            winsound.PlaySound(None, winsound.SND_PURGE)


def is_audio_stopped() -> bool:
    return _stop_event.is_set()


def _wav_duration(path: str) -> float:
    try:
        with open(path, "rb") as f:
            riff = f.read(12)
            if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return 0.0
            channels = sample_rate = bits = 0
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    break
                cid = chunk[:4]
                size = struct.unpack_from("<I", chunk, 4)[0]
                if cid == b"fmt ":
                    data = f.read(min(size, 40))
                    if len(data) >= 16:
                        channels = struct.unpack_from("<H", data, 2)[0]
                        sample_rate = struct.unpack_from("<I", data, 4)[0]
                        bits = struct.unpack_from("<H", data, 14)[0]
                    else:
                        f.seek(size - len(data), 1)
                elif cid == b"data":
                    byte_rate = channels * sample_rate * (bits // 8)
                    if byte_rate > 0:
                        return size / byte_rate
                    break
                else:
                    f.seek(size, 1)
    except Exception:
        pass
    return 0.0


def _play_with_pygame(target: str) -> None:
    import pygame

    pygame.mixer.init()
    try:
        pygame.mixer.music.load(target)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy() and not _stop_event.is_set():
            time.sleep(0.05)
    finally:
        pygame.mixer.music.stop()
        pygame.mixer.quit()


async def play_wav_file(path: str | Path) -> None:
    _stop_event.clear()
    target_path = Path(path)
    target = str(target_path)
    if platform.system().lower() == "windows" and target_path.suffix.lower() == ".wav":
        import winsound

        winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC)
        duration = _wav_duration(target)
        if duration <= 0:
            duration = 30.0
        await asyncio.to_thread(_wait_until_done, duration)
        return

    await asyncio.to_thread(_play_with_pygame, target)


def _wait_until_done(duration_sec: float) -> None:
    deadline = time.monotonic() + duration_sec + 0.3
    while time.monotonic() < deadline and not _stop_event.is_set():
        time.sleep(0.05)
