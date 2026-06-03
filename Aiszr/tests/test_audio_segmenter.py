import math
import wave
from pathlib import Path

import pytest

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


def _write_stereo_tone_and_silence(path: Path, rate: int = 22050) -> Path:
    frames = bytearray()
    for kind, duration_ms in [
        ("tone", 2200),
        ("silence", 300),
        ("tone", 2300),
    ]:
        frame_count = rate * duration_ms // 1000
        for index in range(frame_count):
            if kind == "tone":
                left = int(16000 * math.sin(2 * math.pi * 440 * index / rate))
                right = 0
            else:
                left = 0
                right = 0
            frames.extend(left.to_bytes(2, "little", signed=True))
            frames.extend(right.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(bytes(frames))
    return path


def _write_8bit_wav(path: Path, frame_count: int = 100) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"\x80" * frame_count)
    return path


def _duration_ms(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() * 1000 / wav_file.getframerate()


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


def test_default_config_does_not_split_three_second_anchor_phrases(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(3000) + _silence_frames(400) + _tone_frames(6500),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter().segment(src, output_dir)

    assert result == [src]


def test_default_config_splits_after_six_second_anchor_phrase(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(6500) + _silence_frames(400) + _tone_frames(6500),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter().segment(src, output_dir)

    assert [path.name for path in result] == ["segment_0001.wav", "segment_0002.wav"]
    assert 6800 <= _duration_ms(result[0]) <= 7000
    assert 6400 <= _duration_ms(result[1]) <= 6600


def test_stereo_wav_segments_and_preserves_params(tmp_path):
    src = _write_stereo_tone_and_silence(tmp_path / "source.wav")
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
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getnchannels() == 2
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 22050
            assert wav_file.getcomptype() == "NONE"


def test_trailing_silence_is_trimmed_to_retained_silence(tmp_path):
    src = _write_wav(tmp_path / "source.wav", _tone_frames(2200) + _silence_frames(3000))
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

    assert [path.name for path in result] == ["segment_0001.wav"]
    assert 2250 <= _duration_ms(result[0]) <= 2350


def test_short_tail_segment_is_not_emitted(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200) + _silence_frames(300) + _tone_frames(100),
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

    assert result == [src]
    assert all(_duration_ms(path) >= 2000 for path in result)
    assert not (output_dir / "segment_0002.wav").exists()


def test_short_tail_before_trailing_silence_is_merged(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200)
        + _silence_frames(300)
        + _tone_frames(100)
        + _silence_frames(3000),
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

    assert [path.name for path in result] == ["segment_0001.wav"]
    assert 2000 <= _duration_ms(result[0]) < 3000
    assert not (output_dir / "segment_0002.wav").exists()


def test_max_segments_cap_preserves_remaining_audio(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200)
        + _silence_frames(300)
        + _tone_frames(2200)
        + _silence_frames(300)
        + _tone_frames(2200)
        + _silence_frames(300)
        + _tone_frames(2200),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=100,
            max_segments=2,
        )
    ).segment(src, output_dir)

    assert [path.name for path in result] == ["segment_0001.wav", "segment_0002.wav"]
    assert 7150 <= _duration_ms(result[1]) <= 7250


def test_middle_long_silence_drops_excess_from_next_segment(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200) + _silence_frames(3000) + _tone_frames(2200),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=1000,
        )
    ).segment(src, output_dir)

    assert [path.name for path in result] == ["segment_0001.wav", "segment_0002.wav"]
    assert 3150 <= _duration_ms(result[0]) <= 3250
    assert 2150 <= _duration_ms(result[1]) <= 2250


def test_short_tail_merge_does_not_restore_trimmed_middle_silence(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200)
        + _silence_frames(3000)
        + _tone_frames(100)
        + _silence_frames(3000),
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

    assert [path.name for path in result] == ["segment_0001.wav"]
    assert result != [src]
    assert 2400 <= _duration_ms(result[0]) <= 2600
    assert not (output_dir / "segment_0002.wav").exists()


def test_middle_long_silence_trim_allows_single_merged_segment(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200)
        + _silence_frames(3000)
        + _tone_frames(100),
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

    assert [path.name for path in result] == ["segment_0001.wav"]
    assert result != [src]
    assert 2400 <= _duration_ms(result[0]) <= 2600


def test_material_trim_threshold_controls_single_segment_fallback(tmp_path):
    output_dir = tmp_path / "segments"
    below_threshold = _write_wav(
        tmp_path / "below.wav",
        _tone_frames(2200)
        + _silence_frames(2090)
        + _tone_frames(100),
    )
    at_threshold = _write_wav(
        tmp_path / "at.wav",
        _tone_frames(2200)
        + _silence_frames(2100)
        + _tone_frames(100),
    )
    segmenter = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=100,
        )
    )

    assert segmenter.segment(below_threshold, output_dir / "below") == [below_threshold]
    result = segmenter.segment(at_threshold, output_dir / "at")

    assert [path.name for path in result] == ["segment_0001.wav"]
    assert result != [at_threshold]


def test_max_segments_one_disables_segmentation_and_returns_source_wav(tmp_path):
    src = _write_wav(
        tmp_path / "source.wav",
        _tone_frames(2200) + _silence_frames(300) + _tone_frames(2300),
    )
    output_dir = tmp_path / "segments"

    result = AudioSegmenter(AudioSegmenterConfig(max_segments=1)).segment(src, output_dir)

    assert result == [src]


def test_non_pcm16_wav_raises_value_error(tmp_path):
    src = _write_8bit_wav(tmp_path / "source.wav")

    with pytest.raises(ValueError):
        AudioSegmenter().segment(src, tmp_path / "segments")
