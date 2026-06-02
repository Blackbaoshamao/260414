import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import digital_human_pipeline as dhp
import livetalking_runtime
from digital_human_pipeline import DigitalHumanPipeline, PipelineConfig
from voice_manager import VoiceActionResult


class _VoiceManager:
    pass


def _pipeline(logs=None):
    if logs is None:
        logs = []
    return DigitalHumanPipeline(_VoiceManager(), log_callback=logs.append)


@pytest.mark.asyncio
async def test_prepare_anchor_segments_resolves_audio_and_segments_it(monkeypatch, tmp_path):
    pipeline = _pipeline()
    source = tmp_path / "anchor.wav"
    source.write_bytes(b"RIFFxxxxWAVE")
    segments = [tmp_path / "segment_0001.wav", tmp_path / "segment_0002.wav"]
    segment_calls = []

    async def fake_resolve(config):
        assert config.audio_folder_path == "configured-audio"
        return VoiceActionResult(True, "ok", output_path=str(source))

    class FakeSegmenter:
        def segment(self, wav_path, segment_dir):
            segment_calls.append((wav_path, segment_dir))
            return segments

    monkeypatch.setattr(pipeline, "_resolve_livetalking_audio", fake_resolve)
    monkeypatch.setattr(dhp, "AudioSegmenter", FakeSegmenter, raising=False)
    config = PipelineConfig(
        output_dir=str(tmp_path / "out"),
        audio_folder_path="configured-audio",
    )

    result = await pipeline._prepare_anchor_segments(config)

    assert result.ok is True
    assert result.source_path == source
    assert result.segments == segments
    assert segment_calls == [
        (source, Path(config.resolve_output_dir()) / "anchor_segments")
    ]


@pytest.mark.asyncio
async def test_prepare_anchor_segments_falls_back_to_full_wav_when_segmenter_fails(
    monkeypatch, tmp_path
):
    logs = []
    pipeline = _pipeline(logs)
    source = tmp_path / "anchor.wav"
    source.write_bytes(b"RIFFxxxxWAVE")

    async def fake_resolve(_config):
        return VoiceActionResult(True, "ok", output_path=str(source))

    class FailingSegmenter:
        def segment(self, _wav_path, _segment_dir):
            raise ValueError("bad wav")

    monkeypatch.setattr(pipeline, "_resolve_livetalking_audio", fake_resolve)
    monkeypatch.setattr(dhp, "AudioSegmenter", FailingSegmenter, raising=False)

    result = await pipeline._prepare_anchor_segments(PipelineConfig(output_dir=str(tmp_path)))

    assert result.ok is True
    assert result.source_path == source
    assert result.segments == [source]
    assert any("segment" in message.lower() for message in logs)


@pytest.mark.asyncio
async def test_run_livetalking_starts_runtime_without_audio_loop_and_starts_scheduler(
    monkeypatch, tmp_path
):
    pipeline = _pipeline()
    video = tmp_path / "anchor.mp4"
    segment = tmp_path / "segment.wav"
    second_segment = tmp_path / "segment_2.wav"
    manual = tmp_path / "manual.wav"
    video.write_bytes(b"video")
    segment.write_bytes(b"RIFFxxxxWAVE")
    second_segment.write_bytes(b"RIFFxxxxWAVE")
    manual.write_bytes(b"RIFFxxxxWAVE")
    normalized = tmp_path / "normalized.wav"
    normalized.write_bytes(b"RIFFxxxxWAVE")
    configured_streams = []
    runtime_instances = []
    scheduler_instances = []

    class FakeRuntime:
        def __init__(self, log_callback=None, error_callback=None):
            self.log_callback = log_callback
            self.error_callback = error_callback
            self.start_calls = []
            self.sent_audio = []
            runtime_instances.append(self)

        async def start(self, runtime_config, wav_path=None, *, loop_audio=True):
            self.start_calls.append((runtime_config, wav_path, loop_audio))
            return {
                "ok": True,
                "rtmp_url": "rtmp://127.0.0.1:1935/live/test",
                "listen_port": "9009",
            }

        async def _normalize_wav(self, wav_path, runtime_config):
            assert Path(wav_path) in {segment, manual}
            assert runtime_config.listen_port == 8012
            return normalized

        async def send_audio_once(self, port, wav_path):
            self.sent_audio.append((port, wav_path))

        async def stop(self):
            return None

    class FakeScheduler:
        def __init__(
            self,
            *,
            anchor_segments,
            send_wav,
            log_callback=None,
            initial_delay_path=None,
        ):
            self.anchor_segments = anchor_segments
            self.send_wav = send_wav
            self.log_callback = log_callback
            self.initial_delay_path = initial_delay_path
            self.started = False
            scheduler_instances.append(self)

        def start(self):
            self.started = True
            return SimpleNamespace(
                done=lambda: False,
                cancelled=lambda: False,
                exception=lambda: None,
                add_done_callback=lambda _callback: None,
            )

        async def stop(self):
            return None

    async def fake_prepare(config):
        assert config.video_path == str(video)
        return SimpleNamespace(
            ok=True,
            message="ok",
            source_path=tmp_path / "full.wav",
            segments=[segment, second_segment],
        )

    async def fake_configure_obs(_config, stream_url):
        assert runtime_instances[0].sent_audio == [(9009, normalized)]
        configured_streams.append(stream_url)
        return {"ok": True}

    monkeypatch.setattr(pipeline, "_prepare_anchor_segments", fake_prepare)
    monkeypatch.setattr(pipeline, "_configure_obs", fake_configure_obs)
    monkeypatch.setattr(livetalking_runtime, "LiveTalkingRuntime", FakeRuntime)
    monkeypatch.setattr(dhp, "DigitalHumanSpeechScheduler", FakeScheduler, raising=False)

    result = await pipeline._run_livetalking(
        PipelineConfig(
            video_path=str(video),
            output_dir=str(tmp_path / "out"),
            livetalking_listen_port=8012,
        )
    )

    assert result["ok"] is True
    assert configured_streams == ["rtmp://127.0.0.1:1935/live/test"]
    runtime = runtime_instances[0]
    runtime_config, wav_path, loop_audio = runtime.start_calls[0]
    assert runtime_config.listen_port == 8012
    assert wav_path is None
    assert loop_audio is False
    scheduler = scheduler_instances[0]
    assert scheduler.anchor_segments == [second_segment, segment]
    assert scheduler.log_callback is pipeline._log
    assert scheduler.initial_delay_path == segment
    assert scheduler.started is True

    await scheduler.send_wav(manual)

    assert runtime.sent_audio == [(9009, normalized), (9009, normalized)]


@pytest.mark.asyncio
async def test_run_livetalking_cleans_up_runtime_when_first_audio_send_fails(
    monkeypatch, tmp_path
):
    pipeline = _pipeline()
    video = tmp_path / "anchor.mp4"
    segment = tmp_path / "segment.wav"
    video.write_bytes(b"video")
    segment.write_bytes(b"RIFFxxxxWAVE")
    events = []

    class FakeRuntime:
        async def start(self, _runtime_config, wav_path=None, *, loop_audio=True):
            return {"ok": True, "rtmp_url": "rtmp://127.0.0.1:1935/live/test", "listen_port": 9011}

        async def _normalize_wav(self, wav_path, _runtime_config):
            return Path(wav_path)

        async def send_audio_once(self, port, wav_path):
            events.append(("send", port, wav_path))
            raise RuntimeError("upload failed")

        async def stop(self):
            events.append("runtime.stop")

    async def fake_prepare(_config):
        return SimpleNamespace(ok=True, message="ok", source_path=segment, segments=[segment])

    monkeypatch.setattr(pipeline, "_prepare_anchor_segments", fake_prepare)
    monkeypatch.setattr(livetalking_runtime, "LiveTalkingRuntime", lambda **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError, match="upload failed"):
        await pipeline._run_livetalking(PipelineConfig(video_path=str(video), output_dir=str(tmp_path)))

    assert events == [("send", 9011, segment), "runtime.stop"]
    assert pipeline._speech_scheduler is None
    assert pipeline._livetalking_runtime is None


@pytest.mark.asyncio
async def test_run_livetalking_cleans_up_runtime_when_cancelled_after_start(
    monkeypatch, tmp_path
):
    pipeline = _pipeline()
    video = tmp_path / "anchor.mp4"
    segment = tmp_path / "segment.wav"
    video.write_bytes(b"video")
    segment.write_bytes(b"RIFFxxxxWAVE")
    events = []

    class FakeRuntime:
        async def start(self, _runtime_config, wav_path=None, *, loop_audio=True):
            pipeline._cancel_event.set()
            return {"ok": True, "rtmp_url": "rtmp://127.0.0.1:1935/live/test", "listen_port": 9012}

        async def stop(self):
            events.append("runtime.stop")

    async def fake_prepare(_config):
        return SimpleNamespace(ok=True, message="ok", source_path=segment, segments=[segment])

    monkeypatch.setattr(pipeline, "_prepare_anchor_segments", fake_prepare)
    monkeypatch.setattr(livetalking_runtime, "LiveTalkingRuntime", lambda **_kwargs: FakeRuntime())

    result = await pipeline._run_livetalking(PipelineConfig(video_path=str(video), output_dir=str(tmp_path)))

    assert result["ok"] is False
    assert events == ["runtime.stop"]
    assert pipeline._livetalking_runtime is None


@pytest.mark.asyncio
async def test_run_livetalking_cleans_up_runtime_when_start_is_cancelled(
    monkeypatch, tmp_path
):
    pipeline = _pipeline()
    video = tmp_path / "anchor.mp4"
    segment = tmp_path / "segment.wav"
    video.write_bytes(b"video")
    segment.write_bytes(b"RIFFxxxxWAVE")
    events = []

    class FakeRuntime:
        async def start(self, _runtime_config, wav_path=None, *, loop_audio=True):
            raise asyncio.CancelledError()

        async def stop(self):
            events.append("runtime.stop")

    async def fake_prepare(_config):
        return SimpleNamespace(ok=True, message="ok", source_path=segment, segments=[segment])

    monkeypatch.setattr(pipeline, "_prepare_anchor_segments", fake_prepare)
    monkeypatch.setattr(livetalking_runtime, "LiveTalkingRuntime", lambda **_kwargs: FakeRuntime())

    with pytest.raises(asyncio.CancelledError):
        await pipeline._run_livetalking(PipelineConfig(video_path=str(video), output_dir=str(tmp_path)))

    assert events == ["runtime.stop"]
    assert pipeline._livetalking_runtime is None


@pytest.mark.asyncio
async def test_speech_scheduler_done_callback_sets_error_and_cleans_runtime():
    pipeline = _pipeline()
    events = []

    class FakeScheduler:
        async def stop(self):
            events.append("scheduler.stop")

    class FakeRuntime:
        async def stop(self):
            events.append("runtime.stop")

    async def fail_scheduler():
        raise RuntimeError("scheduler failed")

    pipeline._speech_scheduler = FakeScheduler()
    pipeline._livetalking_runtime = FakeRuntime()

    task = asyncio.create_task(fail_scheduler())
    await asyncio.sleep(0)
    pipeline._on_speech_scheduler_done(task)

    for _ in range(20):
        if events:
            break
        await asyncio.sleep(0.01)

    assert pipeline.state is dhp.PipelineState.ERROR
    assert events == ["scheduler.stop", "runtime.stop"]
    assert pipeline._speech_scheduler is None
    assert pipeline._livetalking_runtime is None


@pytest.mark.asyncio
async def test_run_livetalking_cleans_up_scheduler_and_runtime_when_obs_config_raises(
    monkeypatch, tmp_path
):
    pipeline = _pipeline()
    video = tmp_path / "anchor.mp4"
    segment = tmp_path / "segment.wav"
    normalized = tmp_path / "normalized.wav"
    video.write_bytes(b"video")
    segment.write_bytes(b"RIFFxxxxWAVE")
    normalized.write_bytes(b"RIFFxxxxWAVE")
    events = []

    class FakeRuntime:
        async def start(self, _runtime_config, wav_path=None, *, loop_audio=True):
            return {"ok": True, "rtmp_url": "rtmp://127.0.0.1:1935/live/test", "listen_port": 9010}

        async def _normalize_wav(self, wav_path, _runtime_config):
            return normalized

        async def send_audio_once(self, port, wav_path):
            events.append(("send", port, wav_path))

        async def stop(self):
            events.append("runtime.stop")

    class FakeScheduler:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            events.append("scheduler.start")
            return SimpleNamespace(
                done=lambda: False,
                cancelled=lambda: False,
                exception=lambda: None,
                add_done_callback=lambda _callback: None,
            )

        async def stop(self):
            events.append("scheduler.stop")

    async def fake_prepare(_config):
        return SimpleNamespace(ok=True, message="ok", source_path=segment, segments=[segment])

    async def failing_configure_obs(_config, _stream_url):
        raise RuntimeError("obs failed")

    monkeypatch.setattr(pipeline, "_prepare_anchor_segments", fake_prepare)
    monkeypatch.setattr(pipeline, "_configure_obs", failing_configure_obs)
    monkeypatch.setattr(livetalking_runtime, "LiveTalkingRuntime", lambda **_kwargs: FakeRuntime())
    monkeypatch.setattr(dhp, "DigitalHumanSpeechScheduler", FakeScheduler, raising=False)

    with pytest.raises(RuntimeError, match="obs failed"):
        await pipeline._run_livetalking(PipelineConfig(video_path=str(video), output_dir=str(tmp_path)))

    assert events == [
        ("send", 9010, normalized),
        "scheduler.start",
        "scheduler.stop",
        "runtime.stop",
    ]
    assert pipeline._speech_scheduler is None
    assert pipeline._livetalking_runtime is None


@pytest.mark.asyncio
async def test_stop_stops_speech_scheduler_before_livetalking_runtime():
    pipeline = _pipeline()
    events = []

    class FakeScheduler:
        async def stop(self):
            events.append("scheduler.stop")

    class FakeRuntime:
        async def stop(self):
            events.append("runtime.stop")

    pipeline._speech_scheduler = FakeScheduler()
    pipeline._livetalking_runtime = FakeRuntime()

    await pipeline.stop()

    assert events == ["scheduler.stop", "runtime.stop"]
    assert pipeline._speech_scheduler is None
    assert pipeline._livetalking_runtime is None


def test_enqueue_insertion_audio_returns_false_without_scheduler_and_forwards_with_scheduler(
    tmp_path,
):
    pipeline = _pipeline()
    wav_path = tmp_path / "insert.wav"

    assert pipeline.enqueue_insertion_audio(wav_path, text="keyword") is False

    class FakeScheduler:
        def __init__(self):
            self.calls = []

        def enqueue_insertion(self, path, *, text=""):
            self.calls.append((path, text))
            return True

    scheduler = FakeScheduler()
    pipeline._speech_scheduler = scheduler

    assert pipeline.enqueue_insertion_audio(wav_path, text="keyword") is True
    assert scheduler.calls == [(wav_path, "keyword")]
