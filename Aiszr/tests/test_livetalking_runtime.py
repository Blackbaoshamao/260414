import os
from pathlib import Path

from livetalking_runtime import (
    LiveTalkingRuntime,
    LiveTalkingRuntimeConfig,
    default_mediamtx_exe_path,
    default_wav2lip384_weight_path,
    find_single_wav,
)


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
