import os
from pathlib import Path

import pytest

from livetalking_runtime import (
    LiveTalkingRuntime,
    LiveTalkingRuntimeConfig,
    default_mediamtx_exe_path,
    default_wav2lip384_weight_path,
    find_single_wav,
)


def _patch_startup(monkeypatch, runtime: LiveTalkingRuntime, config: LiveTalkingRuntimeConfig) -> None:
    async def fake_ensure_avatar(_config):
        return "avatar-test"

    async def fake_start_mediamtx(_config):
        return None

    async def fake_start_livetalking(_config, _avatar_id):
        return None

    async def fake_wait_until_ready(_port):
        return None

    monkeypatch.setattr(runtime, "_resolve_config", lambda _config: config)
    monkeypatch.setattr(runtime, "_validate_config", lambda _config: None)
    monkeypatch.setattr(runtime, "_ensure_avatar", fake_ensure_avatar)
    monkeypatch.setattr(runtime, "_start_mediamtx", fake_start_mediamtx)
    monkeypatch.setattr(runtime, "_start_livetalking", fake_start_livetalking)
    monkeypatch.setattr(runtime, "_wait_until_ready", fake_wait_until_ready)


def test_find_single_wav_returns_newest_wav(tmp_path):
    (tmp_path / "b.wav").write_bytes(b"")
    (tmp_path / "a.WAV").write_bytes(b"")
    (tmp_path / "ignore.mp3").write_bytes(b"")
    os.utime(tmp_path / "a.WAV", (100, 100))
    os.utime(tmp_path / "b.wav", (200, 200))

    assert find_single_wav(str(tmp_path)) == tmp_path / "b.wav"


def test_find_single_wav_returns_none_for_missing_or_empty(tmp_path):
    assert find_single_wav("") is None
    assert find_single_wav(str(tmp_path / "missing")) is None
    assert find_single_wav(str(tmp_path)) is None


def test_resolve_config_uses_local_default_paths():
    runtime = LiveTalkingRuntime()

    resolved = runtime._resolve_config(LiveTalkingRuntimeConfig())

    assert Path(resolved.mediamtx_exe_path) == default_mediamtx_exe_path()
    assert Path(resolved.wav2lip384_weight_path) == default_wav2lip384_weight_path()
    assert resolved.push_url == "rtmp://127.0.0.1:1935/live/aiszr"
    assert resolved.listen_port == 8010
    assert resolved.modelres == 384


async def test_start_can_skip_fixed_audio_loop(monkeypatch):
    runtime = LiveTalkingRuntime()
    config = LiveTalkingRuntimeConfig(
        listen_port=8123,
        push_url="rtmp://127.0.0.1:1935/live/test",
        avatar_id="avatar-test",
    )
    send_calls = []

    async def fake_send_audio_once(port, wav_path, *, client=None):
        send_calls.append((port, wav_path, client))

    _patch_startup(monkeypatch, runtime, config)
    monkeypatch.setattr(runtime, "send_audio_once", fake_send_audio_once, raising=False)

    result = await runtime.start(config, None, loop_audio=False)

    assert result["ok"] is True
    assert result["listen_port"] == 8123
    assert send_calls == []
    assert runtime._audio_loop_task is None


async def test_start_sends_audio_once_without_loop_when_loop_audio_false(monkeypatch, tmp_path):
    runtime = LiveTalkingRuntime()
    source = tmp_path / "source.wav"
    normalized = tmp_path / "normalized.wav"
    source.write_bytes(b"RIFFxxxxWAVE")
    normalized.write_bytes(b"RIFFxxxxWAVE")
    config = LiveTalkingRuntimeConfig(listen_port=8124, avatar_id="avatar-test")
    send_calls = []

    async def fake_normalize(wav_path, _config):
        assert wav_path == str(source)
        return normalized

    async def fake_send_audio_once(port, wav_path, *, client=None):
        send_calls.append((port, wav_path, client))

    _patch_startup(monkeypatch, runtime, config)
    monkeypatch.setattr(runtime, "_normalize_wav", fake_normalize)
    monkeypatch.setattr(runtime, "send_audio_once", fake_send_audio_once, raising=False)

    result = await runtime.start(config, str(source), loop_audio=False)

    assert result["ok"] is True
    assert result["audio_path"] == str(normalized)
    assert send_calls == [(8124, normalized, None)]
    assert runtime._audio_loop_task is None


async def test_start_does_not_treat_empty_wav_path_as_no_audio(monkeypatch):
    runtime = LiveTalkingRuntime()
    config = LiveTalkingRuntimeConfig(listen_port=8125, avatar_id="avatar-test")

    async def fake_normalize(wav_path, _config):
        raise FileNotFoundError(f"WAV not found: {wav_path!r}")

    _patch_startup(monkeypatch, runtime, config)
    monkeypatch.setattr(runtime, "_normalize_wav", fake_normalize)

    with pytest.raises(FileNotFoundError):
        await runtime.start(config, "", loop_audio=False)
