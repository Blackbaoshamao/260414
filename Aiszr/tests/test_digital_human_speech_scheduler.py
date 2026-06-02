import asyncio
from pathlib import Path

import pytest

from digital_human_speech_scheduler import DigitalHumanSpeechScheduler


@pytest.mark.asyncio
async def test_insertion_waits_for_current_anchor_before_next_anchor():
    sent = []
    first_anchor_sent = asyncio.Event()
    insertion_sent = asyncio.Event()
    second_anchor_sent = asyncio.Event()

    async def send_wav(wav_path):
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
