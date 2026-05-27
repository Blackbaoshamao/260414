import pytest
import wave

from voice_manager import (
    DEFAULT_SPEED_RATIO,
    DEFAULT_VOLUME_RATIO,
    QWEN_TTS_VC_MODEL,
    AliyunBailianProvider,
    VoiceProviderApiConfig,
    _cached_synthesis_path,
    _is_valid_audio_cache,
    _write_generated_audio,
)


def test_cached_synthesis_path_changes_with_text_and_voice(tmp_path):
    base = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "欢迎来到直播间",
        DEFAULT_SPEED_RATIO,
        60,
    )
    same = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "欢迎来到直播间",
        DEFAULT_SPEED_RATIO,
        60,
    )
    changed_text = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "今天价格更划算",
        DEFAULT_SPEED_RATIO,
        60,
    )
    changed_voice = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-b",
        "欢迎来到直播间",
        DEFAULT_SPEED_RATIO,
        60,
    )
    changed_speed = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "欢迎来到直播间",
        1.35,
        60,
    )
    changed_volume = _cached_synthesis_path(
        tmp_path,
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "欢迎来到直播间",
        DEFAULT_SPEED_RATIO,
        80,
    )

    assert base == same
    assert base != changed_text
    assert base != changed_voice
    assert base != changed_speed
    assert base != changed_volume


@pytest.mark.asyncio
async def test_synthesize_returns_valid_cache_before_credentials(tmp_path):
    provider = AliyunBailianProvider(VoiceProviderApiConfig())
    volume_value = round(50 * DEFAULT_VOLUME_RATIO)
    cached_path = _cached_synthesis_path(
        tmp_path,
        provider.provider_name,
        QWEN_TTS_VC_MODEL,
        "voice-a",
        "欢迎来到直播间",
        DEFAULT_SPEED_RATIO,
        volume_value,
    )
    with wave.open(str(cached_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 32)

    result = await provider.synthesize(
        "欢迎来到直播间",
        "voice-a",
        tmp_path,
        model_id=QWEN_TTS_VC_MODEL,
    )

    assert result.ok is True
    assert result.message == "使用缓存试听音频"
    assert result.output_path == str(cached_path)


def test_invalid_qwen_wav_header_is_rejected_and_normalized(tmp_path):
    raw = bytearray()
    raw.extend(b"RIFF")
    raw.extend((0x7FFFFFFF).to_bytes(4, "little"))
    raw.extend(b"WAVEfmt ")
    raw.extend((16).to_bytes(4, "little"))
    raw.extend((1).to_bytes(2, "little"))
    raw.extend((1).to_bytes(2, "little"))
    raw.extend((24000).to_bytes(4, "little"))
    raw.extend((48000).to_bytes(4, "little"))
    raw.extend((2).to_bytes(2, "little"))
    raw.extend((16).to_bytes(2, "little"))
    raw.extend(b"data")
    raw.extend((0x7FFFFFFF).to_bytes(4, "little"))
    raw.extend(b"\x00\x00" * 64)

    broken = tmp_path / "broken.wav"
    broken.write_bytes(bytes(raw))
    assert _is_valid_audio_cache(broken) is False

    fixed = _write_generated_audio(tmp_path / "fixed.wav", bytes(raw))
    assert fixed.suffix == ".wav"
    assert _is_valid_audio_cache(fixed) is True
