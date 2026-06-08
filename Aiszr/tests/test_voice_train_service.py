"""Tests for voice_train_service."""
import pytest
from voice_train_service import VoiceTrainService, TrainProgress


def test_train_progress_defaults():
    p = TrainProgress()
    assert p.step == ""
    assert p.percent == 0
    assert p.message == ""


def test_validate_audio_files_missing():
    ok, msg, dur = VoiceTrainService.validate_audio_files(["/nonexistent.wav"])
    assert not ok
    assert "不存在" in msg


def test_validate_audio_files_unsupported_format():
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello")
        path = f.name
    try:
        ok, msg, dur = VoiceTrainService.validate_audio_files([path])
        assert not ok
        assert "不支持" in msg
    finally:
        os.unlink(path)


def test_validate_audio_files_short_wav(tmp_path):
    import wave, struct
    wav_path = tmp_path / "short.wav"
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(32000)
        wf.writeframes(struct.pack("<h", 0) * 32000)
    ok, msg, dur = VoiceTrainService.validate_audio_files([str(wav_path)])
    assert not ok


def test_latest_file_finds_newest(tmp_path):
    (tmp_path / "old.ckpt").write_text("a")
    (tmp_path / "new.ckpt").write_text("b")
    import os, time
    os.utime(tmp_path / "new.ckpt", (time.time() + 10, time.time() + 10))
    result = VoiceTrainService._latest_file(tmp_path, ".ckpt")
    assert result is not None
    assert result.name == "new.ckpt"


def test_latest_file_returns_none_for_empty(tmp_path):
    assert VoiceTrainService._latest_file(tmp_path, ".ckpt") is None


def test_latest_file_returns_none_for_missing(tmp_path):
    missing = tmp_path / "nope"
    assert VoiceTrainService._latest_file(missing, ".ckpt") is None


def test_find_trained_weights_finds_file(tmp_path):
    log_dir = tmp_path / "logs_s2"
    log_dir.mkdir()
    ckpt = log_dir / "model.pth"
    ckpt.write_text("weights")
    result = VoiceTrainService._find_trained_weights(tmp_path, "logs_s2", ".pth")
    assert result is not None
    assert result.name == "model.pth"


def test_find_trained_weights_returns_none(tmp_path):
    assert VoiceTrainService._find_trained_weights(tmp_path, "logs_s2", ".pth") is None


def test_pretrained_s2g_path_v2pro(tmp_path):
    service = VoiceTrainService(tmp_path, "python")
    path = service._pretrained_s2g_path("v2Pro")
    assert "s2Gv2Pro.pth" in path


def test_pretrained_s2g_path_v2(tmp_path):
    service = VoiceTrainService(tmp_path, "python")
    path = service._pretrained_s2g_path("v2")
    assert "s2G488k.pth" in path
