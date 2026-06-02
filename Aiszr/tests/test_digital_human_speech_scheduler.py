import asyncio
import wave
from pathlib import Path

import pytest

from digital_human_speech_scheduler import DigitalHumanSpeechScheduler, wav_duration_sec


async def _wait_until_done(task: asyncio.Task, timeout: float = 0.5) -> None:
    async def _wait():
        while not task.done():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=timeout)


@pytest.mark.asyncio
async def test_insertion_waits_for_current_anchor_before_next_anchor():
    sent = []
    received_paths = []
    first_anchor_sent = asyncio.Event()
    insertion_sent = asyncio.Event()
    second_anchor_sent = asyncio.Event()

    async def send_wav(wav_path):
        received_paths.append(wav_path)
        filename = Path(wav_path).name
        sent.append(filename)
        if filename == "a.wav":
            first_anchor_sent.set()
        elif filename == "insert.wav":
            insertion_sent.set()
        elif filename == "b.wav":
            second_anchor_sent.set()

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav", "b.wav"],
        send_wav=send_wav,
        duration_fn=lambda _path: 0.02,
    )

    scheduler.start()
    try:
        await asyncio.wait_for(first_anchor_sent.wait(), timeout=0.5)

        assert scheduler.enqueue_insertion("insert.wav", text="keyword")

        await asyncio.wait_for(insertion_sent.wait(), timeout=0.5)
        await asyncio.wait_for(second_anchor_sent.wait(), timeout=0.5)
    finally:
        await scheduler.stop()

    assert sent[:3] == ["a.wav", "insert.wav", "b.wav"]
    assert all(isinstance(path, Path) for path in received_paths[:3])


def test_enqueue_insertion_returns_false_when_queue_is_full():
    async def send_wav(_wav_path):
        raise AssertionError("scheduler should not be started for this test")

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav"],
        send_wav=send_wav,
        duration_fn=lambda _path: 0.02,
        max_insertions=1,
    )

    assert scheduler.enqueue_insertion("first.wav", text="first")
    assert not scheduler.enqueue_insertion("second.wav", text="second")


def test_empty_anchor_segments_are_rejected():
    async def send_wav(_wav_path):
        return None

    with pytest.raises(ValueError):
        DigitalHumanSpeechScheduler(anchor_segments=[], send_wav=send_wav)


@pytest.mark.asyncio
async def test_start_is_idempotent_until_stopped():
    async def send_wav(_wav_path):
        return None

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav"],
        send_wav=send_wav,
        duration_fn=lambda _path: 0.05,
    )

    first = scheduler.start()
    second = scheduler.start()
    try:
        assert first is second
    finally:
        await scheduler.stop()
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_stop_cleans_up_after_anchor_failure_without_reraising():
    logs = []

    async def send_wav(_wav_path):
        raise RuntimeError("anchor failed")

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav"],
        send_wav=send_wav,
        log_callback=logs.append,
    )

    task = scheduler.start()
    await _wait_until_done(task)

    await scheduler.stop()

    assert scheduler._task is None
    assert any("anchor send failed" in message for message in logs)


@pytest.mark.asyncio
async def test_start_after_failed_task_replaces_and_consumes_old_task():
    calls = 0
    second_start_sent = asyncio.Event()

    async def send_wav(_wav_path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("anchor failed")
        second_start_sent.set()

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav"],
        send_wav=send_wav,
        duration_fn=lambda _path: 0.05,
    )

    failed_task = scheduler.start()
    await _wait_until_done(failed_task)
    restarted_task = scheduler.start()
    try:
        assert restarted_task is not failed_task
        await asyncio.wait_for(second_start_sent.wait(), timeout=0.5)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_insertion_failure_logs_and_scheduler_continues_to_anchor():
    sent = []
    logs = []
    second_anchor_sent = asyncio.Event()

    async def send_wav(wav_path):
        name = Path(wav_path).name
        sent.append(name)
        if name == "insert.wav":
            raise RuntimeError("insert failed")
        if name == "b.wav":
            second_anchor_sent.set()

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=["a.wav", "b.wav"],
        send_wav=send_wav,
        duration_fn=lambda _path: 0.02,
        log_callback=logs.append,
    )

    scheduler.enqueue_insertion("insert.wav", text="keyword")
    scheduler.start()
    try:
        await asyncio.wait_for(second_anchor_sent.wait(), timeout=0.5)
    finally:
        await scheduler.stop()

    assert sent[:3] == ["insert.wav", "a.wav", "b.wav"]
    assert any("insertion send failed" in message for message in logs)


def test_wav_duration_sec_reads_wav_and_falls_back_for_bad_files(tmp_path):
    wav_path = tmp_path / "sample.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 8000)

    assert wav_duration_sec(wav_path) == 0.5
    assert wav_duration_sec(tmp_path / "missing.wav") == 0.0
