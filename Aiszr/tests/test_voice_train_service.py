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
