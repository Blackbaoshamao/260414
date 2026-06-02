import pytest
import wave

import voice_manager as voice_manager_module
from voice_manager import (
    DEFAULT_SPEED_RATIO,
    DEFAULT_VOLUME_RATIO,
    QWEN_TTS_VC_MODEL,
    AliyunBailianProvider,
    VoiceActionResult,
    VoiceManager,
    VoiceProviderApiConfig,
    _cached_synthesis_path,
    _is_valid_audio_cache,
    _write_generated_audio,
)
from voice_models import VoiceEntry, VoiceRoleConfig, VoiceSettings


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


@pytest.mark.asyncio
async def test_synthesize_role_to_file_uses_anchor_clone_without_playback(tmp_path, monkeypatch):
    settings = VoiceSettings()
    settings.voices.append(
        VoiceEntry(
            id="anchor-voice",
            name="anchor",
            clone_voice_id="clone-anchor",
            clone_status="ready",
        )
    )
    settings.anchor = VoiceRoleConfig(voice_id="anchor-voice", volume_gain=150)
    manager = VoiceManager(settings)
    keyword_wav = tmp_path / "keyword.wav"
    calls = []
    playback_calls = []

    class FakeProvider:
        async def synthesize(
            self,
            text,
            voice_id,
            output_dir,
            *,
            model_id="",
            speed=DEFAULT_SPEED_RATIO,
            volume=DEFAULT_VOLUME_RATIO,
        ):
            calls.append((text, voice_id, output_dir, model_id, speed, volume))
            return VoiceActionResult(True, "ok", output_path=str(keyword_wav))

    async def fail_playback(path):
        playback_calls.append(path)
        raise AssertionError("synthesize_role_to_file must not play audio")

    monkeypatch.setattr(manager, "provider", lambda: FakeProvider())
    monkeypatch.setattr(voice_manager_module, "play_wav_file", fail_playback)

    result = await manager.synthesize_role_to_file("keyword", "anchor")

    assert result.ok is True
    assert result.output_path == str(keyword_wav)
    assert calls[0][1] == "clone-anchor"
    assert calls[0][2].name == "generated"
    assert calls[0][2].parent.name == "anchor"
    assert calls[0][3] == settings.model_id
    assert calls[0][4] == DEFAULT_SPEED_RATIO
    assert calls[0][5] == pytest.approx(DEFAULT_VOLUME_RATIO * 1.5)
    assert result.clone_voice_id == "clone-anchor"
    assert result.clone_status == "ready"
    assert playback_calls == []


@pytest.mark.asyncio
async def test_synthesize_role_to_file_treats_training_clone_as_not_ready(monkeypatch):
    settings = VoiceSettings()
    voice = VoiceEntry(
        id="anchor-voice",
        name="anchor",
        clone_voice_id="clone-anchor",
        clone_status="training",
    )
    settings.voices.append(voice)
    settings.anchor = VoiceRoleConfig(voice_id="anchor-voice")
    manager = VoiceManager(settings)
    synth_calls = []

    class FakeProvider:
        async def resolve_clone(self, clone_voice_id):
            return VoiceActionResult(
                True,
                "clone is still training",
                clone_voice_id=clone_voice_id,
                clone_status="training",
            )

        async def synthesize(self, *args, **kwargs):
            synth_calls.append((args, kwargs))
            return VoiceActionResult(True, "unexpected")

    monkeypatch.setattr(manager, "provider", lambda: FakeProvider())

    result = await manager.synthesize_role_to_file("keyword", "anchor")

    assert result.ok is False
    assert result.clone_status == "training"
    assert voice.clone_status == "training"
    assert synth_calls == []


@pytest.mark.asyncio
async def test_synthesize_role_to_file_synthesizes_when_training_clone_becomes_ready(monkeypatch, tmp_path):
    settings = VoiceSettings()
    voice = VoiceEntry(
        id="anchor-voice",
        name="anchor",
        clone_voice_id="clone-anchor",
        clone_status="training",
    )
    settings.voices.append(voice)
    settings.anchor = VoiceRoleConfig(voice_id="anchor-voice")
    manager = VoiceManager(settings)
    out = tmp_path / "ready.wav"
    synth_calls = []

    class FakeProvider:
        async def resolve_clone(self, clone_voice_id):
            return VoiceActionResult(
                True,
                "clone is ready",
                clone_status="ready",
            )

        async def synthesize(self, text, voice_id, output_dir, *, model_id="", speed=1.0, volume=1.0):
            synth_calls.append((text, voice_id, output_dir, model_id, speed, volume))
            return VoiceActionResult(True, "ok", output_path=str(out))

    monkeypatch.setattr(manager, "provider", lambda: FakeProvider())

    result = await manager.synthesize_role_to_file("keyword", "anchor")

    assert result.ok is True
    assert result.output_path == str(out)
    assert voice.clone_voice_id == "clone-anchor"
    assert voice.clone_status == "ready"
    assert synth_calls[0][1] == "clone-anchor"
