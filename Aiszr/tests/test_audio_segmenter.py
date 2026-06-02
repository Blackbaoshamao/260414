import math
import wave
from pathlib import Path

from audio_segmenter import AudioSegmenter, AudioSegmenterConfig


SAMPLE_RATE = 16000


def _tone_frames(duration_ms: int, frequency_hz: int = 440) -> bytes:
    frame_count = SAMPLE_RATE * duration_ms // 1000
    frames = bytearray()
    for index in range(frame_count):
        value = int(16000 * math.sin(2 * math.pi * frequency_hz * index / SAMPLE_RATE))
        frames.extend(value.to_bytes(2, "little", signed=True))
    return bytes(frames)


def _silence_frames(duration_ms: int) -> bytes:
    frame_count = SAMPLE_RATE * duration_ms // 1000
    return b"\x00\x00" * frame_count


def _write_wav(path: Path, frames: bytes) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(frames)
    return path


def test_segments_pcm16_wav_at_anchor_silence(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200) + _silence_frames(300) + _tone_frames(2300),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=100,
        )
    ).segment(src, output_dir)

    assert [path.name for path in result] == ["segment_0001.wav", "segment_0002.wav"]
    for path in result:
        assert path.exists()
        assert path.suffix == ".wav"


def test_returns_source_wav_when_no_splittable_silence(tmp_path):
    src = _write_wav(tmp_path / "source.wav", _tone_frames(2500))
    output_dir = tmp_path / "segments"

    result = AudioSegmenter().segment(src, output_dir)

    assert result == [src]
