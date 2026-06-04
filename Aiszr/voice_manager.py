"""Aliyun Bailian voice cloning and synthesis orchestration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # Keep imports working until dependencies are installed.
    load_dotenv = None

from audio_output import play_wav_file, is_audio_stopped, reset_audio_stop
from local_voice_runtime import (
    DEFAULT_LOCAL_VOICE_ENDPOINT,
    ensure_local_voice_runtime,
)
from voice_models import (
    VOICE_MODELS,
    VoiceEntry,
    VoiceProviderApiConfig,
    VoiceRoleConfig,
    VoiceSettings,
    voice_entry_is_compatible,
)

from app_paths import app_dir
APP_DIR = app_dir()
VOICE_DATA_DIR = APP_DIR / "data" / "voice"

DEFAULT_SPEED_RATIO = 1.2
DEFAULT_VOLUME_RATIO = 1.2
LOCAL_VOICE_TEXT_SPLIT_METHOD = "cut0"
QWEN_VOICE_ENROLLMENT_MODEL = "qwen-voice-enrollment"
QWEN_TTS_VC_MODEL = "qwen3-tts-vc-2026-01-22"
QWEN_TTS_VC_MODELS = {QWEN_TTS_VC_MODEL}


def _load_env_files() -> None:
    if load_dotenv is None:
        return
    for env_path in (APP_DIR / ".env", APP_DIR.parent / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


_load_env_files()


@dataclass(slots=True)
class VoiceActionResult:
    ok: bool
    message: str
    clone_voice_id: str = ""
    output_path: str = ""
    clone_status: str = ""


def _short_detail(value: object, limit: int = 360) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _is_local_audio_file(path_or_url: str) -> bool:
    path = Path(path_or_url)
    return path.exists() and path.is_file()


def _safe_output_name(provider: str, text: str, suffix: str) -> str:
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    return f"{provider}_{digest}.{suffix.lstrip('.')}"


def _synthesis_cache_key(
    provider: str,
    model: str,
    voice_id: str,
    text: str,
    speed: float,
    volume: float | int,
) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "voice_id": voice_id,
            "text": text,
            "speed": round(float(speed), 4),
            "volume": round(float(volume), 4),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _cached_synthesis_path(
    output_dir: Path,
    provider: str,
    model: str,
    voice_id: str,
    text: str,
    speed: float,
    volume: float | int,
    suffix: str = "wav",
) -> Path:
    digest = _synthesis_cache_key(provider, model, voice_id, text, speed, volume)
    return output_dir / f"{provider}_{digest}.{suffix.lstrip('.')}"


# Public alias for cross-module use when locating cached WAVs.
cached_synthesis_path = _cached_synthesis_path


def _local_voice_cache_key(config: VoiceProviderApiConfig, ref_audio_path: str) -> str:
    return json.dumps(
        {
            "ref_audio_path": ref_audio_path,
            "prompt_text": str(config.prompt_text or "").strip(),
            "prompt_lang": str(config.prompt_lang or "zh").strip() or "zh",
            "text_split_method": LOCAL_VOICE_TEXT_SPLIT_METHOD,
            "text_lang": str(config.text_lang or "zh").strip() or "zh",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def default_anchor_wav_path(
    settings: "VoiceSettings", role_name: str = "anchor"
) -> Path | None:
    """Path to the cached TTS WAV of the configured anchor script.

    Returns None if the role lacks a clone voice or script — caller should treat
    that as "nothing to play". Always go through this helper so the cache-key
    derivation stays in one place.
    """
    if role_name == "anchor":
        role = settings.anchor
        text = settings.anchor_script
    else:
        role = settings.copilot
        text = ""
    if not text.strip():
        return None
    voice_id = synthesis_voice_id_for_role(settings, role)
    if voice_id is None or not voice_id:
        return None
    if settings.provider == "local_voice":
        api_cfg = settings.api.get("local_voice")
        if api_cfg is not None:
            voice_id = _local_voice_cache_key(api_cfg, voice_id)
    # Volume must mirror AliyunBailianProvider.synthesize: float ratio → int 0-100.
    # Otherwise the cache key diverges from the one used at synthesis time.
    volume_value = max(0, min(100, round(50 * _role_volume_ratio(role))))
    return cached_synthesis_path(
        output_dir=VOICE_DATA_DIR / role_name / "generated",
        provider=settings.provider,
        model=settings.model_id,
        voice_id=voice_id,
        text=text,
        speed=_role_speed_ratio(role),
        volume=volume_value,
    )


def provider_reference_audio(settings: "VoiceSettings") -> str:
    if settings.provider != "local_voice":
        return ""
    api_cfg = settings.api.get("local_voice")
    if api_cfg is None:
        return ""
    return str(api_cfg.reference_audio or "").strip()


def synthesis_voice_id_for_role(settings: "VoiceSettings", role: VoiceRoleConfig) -> str | None:
    voice = settings.find_voice(role.voice_id)
    if voice and not voice_entry_is_compatible(voice, settings.provider):
        return None
    if voice and settings.provider == "local_voice":
        if voice.clone_voice_id:
            return voice.clone_voice_id
        if voice.sample_wav_path:
            return voice.sample_wav_path
    if voice and voice.clone_voice_id:
        return voice.clone_voice_id
    if provider_reference_audio(settings):
        return ""
    return None


def _is_valid_audio_cache(path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 44:
        return False
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return _wav_header_is_consistent(path.read_bytes())
    return True


def _wav_header_is_consistent(audio: bytes) -> bool:
    if len(audio) <= 44 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        return False
    riff_size = int.from_bytes(audio[4:8], "little", signed=False)
    if riff_size + 8 != len(audio):
        return False
    data_offset, data_size = _find_wav_data_chunk(audio)
    return data_offset > 0 and data_size > 0 and data_offset + data_size <= len(audio)


def _find_wav_data_chunk(audio: bytes) -> tuple[int, int]:
    pos = 12
    while pos + 8 <= len(audio):
        chunk_id = audio[pos : pos + 4]
        chunk_size = int.from_bytes(audio[pos + 4 : pos + 8], "little", signed=False)
        chunk_data_offset = pos + 8
        if chunk_id == b"data":
            return chunk_data_offset, chunk_size
        pos = chunk_data_offset + chunk_size + (chunk_size % 2)
    return 0, 0


def _write_pcm16_mono_wav(path: Path, pcm: bytes, sample_rate: int = 24000) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return path


def _write_generated_audio(path: Path, audio: bytes, content_type: str = "") -> Path:
    header = audio[:12]
    lower_content_type = content_type.lower()
    if header.startswith(b"RIFF") and b"WAVE" in header:
        return _write_normalized_wav(path, audio)
    if header.startswith(b"ID3") or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or "mpeg" in lower_content_type:
        target = path.with_suffix(".mp3")
        target.write_bytes(audio)
        return target
    if header.startswith(b"OggS") or "ogg" in lower_content_type:
        target = path.with_suffix(".ogg")
        target.write_bytes(audio)
        return target
    if header.startswith(b"fLaC") or "flac" in lower_content_type:
        target = path.with_suffix(".flac")
        target.write_bytes(audio)
        return target
    return _write_pcm16_mono_wav(path, audio)


def _write_normalized_wav(path: Path, audio: bytes) -> Path:
    if _wav_header_is_consistent(audio):
        path.write_bytes(audio)
        return path

    data_offset, declared_size = _find_wav_data_chunk(audio)
    if data_offset <= 0:
        return _write_pcm16_mono_wav(path, audio)

    actual_size = max(0, len(audio) - data_offset)
    data_size = min(declared_size, actual_size) if declared_size <= actual_size else actual_size
    pcm = audio[data_offset : data_offset + data_size]
    sample_rate = int.from_bytes(audio[24:28], "little", signed=False) if len(audio) >= 28 else 24000
    channels = int.from_bytes(audio[22:24], "little", signed=False) if len(audio) >= 24 else 1
    sample_width = int.from_bytes(audio[34:36], "little", signed=False) // 8 if len(audio) >= 36 else 2
    if sample_rate <= 0:
        sample_rate = 24000
    if channels <= 0:
        channels = 1
    if sample_width <= 0:
        sample_width = 2

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return path


def _decode_audio_data(value: str) -> bytes:
    payload = value.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload)


def _role_speed_ratio(role: VoiceRoleConfig | None = None) -> float:
    multiplier = (role.speed / 100.0) if role else 1.0
    return max(0.0, min(2.0, DEFAULT_SPEED_RATIO * multiplier))


def _role_volume_ratio(role: VoiceRoleConfig | None = None) -> float:
    multiplier = (role.volume_gain / 100.0) if role else 1.0
    return max(0.0, min(2.0, DEFAULT_VOLUME_RATIO * multiplier))


def _friendly_provider_error(provider_label: str, detail: object) -> str:
    text = _short_detail(detail)
    lower = text.lower()
    quota_markers = (
        "balance",
        "quota",
        "not enough",
        "insufficient",
        "arrears",
        "over limit",
        "resource pack",
        "余额",
        "欠费",
        "额度",
        "未开通",
        "调用量",
    )
    auth_markers = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid api",
        "invalid token",
        "signature",
        "authentication",
        "permission",
        "权限",
        "鉴权",
        "签名",
        "token",
        "apikey",
        "api key",
    )
    if any(marker in lower for marker in quota_markers):
        return f"{provider_label}账号余额不足、额度耗尽，或未开通对应语音服务。请在阿里云百炼控制台开通语音克隆/语音合成服务后重试。详情：{text}"
    if any(marker in lower for marker in auth_markers):
        return f"{provider_label}鉴权失败。请检查 DashScope API Key 是否正确。详情：{text}"
    return f"{provider_label}请求失败：{text}"


def _extract_response_json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception:
        return {"raw": getattr(resp, "text", "")}


def _looks_like_audio_response(content: bytes, content_type: str) -> bool:
    lower_content_type = content_type.lower()
    if "json" in lower_content_type or content.lstrip().startswith((b"{", b"[")):
        return False
    header = content[:12]
    return (
        "audio" in lower_content_type
        or "octet-stream" in lower_content_type
        or header.startswith(b"RIFF")
        or header.startswith(b"ID3")
        or content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        or header.startswith(b"OggS")
        or header.startswith(b"fLaC")
    )


class VoiceProviderBase:
    provider_name = ""
    provider_label = "语音供应商"

    def __init__(self, config: VoiceProviderApiConfig):
        self.config = config

    def list_models(self) -> tuple[str, ...]:
        return VOICE_MODELS.get(self.provider_name, ())

    async def validate_credentials(self) -> VoiceActionResult:
        missing = self.missing_credentials()
        if missing:
            return VoiceActionResult(False, f"缺少必要凭据：{', '.join(missing)}")
        return VoiceActionResult(True, "凭据格式已通过基础校验")

    async def create_clone(
        self,
        wav_path: str,
        voice_id: str | None = None,
        *,
        model_id: str = "",
        requested_voice_id: str = "001",
    ) -> VoiceActionResult:
        return VoiceActionResult(False, f"{self.provider_label}尚未实现云端克隆", clone_status="error")

    async def resolve_clone(self, clone_voice_id: str) -> VoiceActionResult:
        return VoiceActionResult(True, "音色可直接使用", clone_voice_id=clone_voice_id, clone_status="ready")

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_dir: Path,
        *,
        model_id: str = "",
        speed: float = DEFAULT_SPEED_RATIO,
        volume: float = DEFAULT_VOLUME_RATIO,
    ) -> VoiceActionResult:
        return VoiceActionResult(False, f"{self.provider_label}尚未实现语音合成")

    def required_fields(self) -> set[str]:
        return set()

    def missing_credentials(self) -> list[str]:
        return sorted(field for field in self.required_fields() if not self.credential(field))

    def credential(self, field: str, *env_names: str) -> str:
        value = str(getattr(self.config, field, "") or "").strip()
        if value:
            return value
        for name in env_names:
            env_value = os.getenv(name, "").strip()
            if env_value:
                return env_value
        return ""


class AliyunBailianProvider(VoiceProviderBase):
    provider_name = "aliyun_bailian"
    provider_label = "阿里云百炼"
    CUSTOMIZATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    SYNTHESIS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"

    def required_fields(self) -> set[str]:
        return {"api_key"}

    def credential(self, field: str, *env_names: str) -> str:
        if field == "api_key":
            return super().credential(field, "DASHSCOPE_API_KEY", "ALIYUN_BAILIAN_API_KEY", "ALIYUN_API_KEY")
        return super().credential(field, *env_names)

    def _api_key(self) -> str:
        return self.credential("api_key")

    def _target_model(self, model_id: str = "") -> str:
        if model_id in QWEN_TTS_VC_MODELS:
            return model_id
        configured = self.credential("region", "ALIYUN_QWEN_TTS_VC_TARGET_MODEL").strip()
        if configured in QWEN_TTS_VC_MODELS:
            return configured
        return QWEN_TTS_VC_MODEL

    @staticmethod
    def _audio_data_uri(file_path: str) -> str:
        path = Path(file_path)
        mime = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
        }.get(path.suffix.lower(), "audio/wav")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _extract_clone_voice_id(result: object) -> str:
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            candidates = [result]
        else:
            candidates = []
            for attr in ("output", "data"):
                value = getattr(result, attr, None)
                if isinstance(value, dict):
                    candidates.append(value)
            if hasattr(result, "get"):
                try:
                    value = result.get("output")
                    if isinstance(value, dict):
                        candidates.append(value)
                except Exception:
                    pass
        for data in candidates:
            for key in ("voice_id", "voiceId", "custom_voice", "custom_voice_id", "voice", "id"):
                value = str(data.get(key, "")).strip()
                if value:
                    return value
        return ""

    def _create_qwen_voice_with_local_file(
        self,
        *,
        file_path: str,
        target_model: str,
        fixed_voice_id: str,
        api_key: str,
    ) -> tuple[bool, str, object]:
        try:
            import httpx
        except Exception as exc:
            return False, "", exc

        try:
            body = {
                "model": QWEN_VOICE_ENROLLMENT_MODEL,
                "input": {
                    "action": "create",
                    "target_model": target_model,
                    "preferred_name": fixed_voice_id,
                    "audio": {"data": self._audio_data_uri(file_path)},
                },
            }
            resp = httpx.post(
                self.CUSTOMIZATION_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=120.0,
            )
            data = _extract_response_json(resp)
            if resp.status_code != 200:
                return False, "", f"HTTP {resp.status_code}: {data}"
            clone_id = self._extract_clone_voice_id(data)
            if clone_id:
                return True, clone_id, ""
            output = data.get("output") if isinstance(data, dict) else None
            if isinstance(output, dict):
                for key in ("voice", "custom_voice", "custom_voice_id"):
                    value = str(output.get(key, "")).strip()
                    if value:
                        return True, value, ""
            return False, "", data.get("message") or data.get("code") or data
        except Exception as exc:
            return False, "", exc

    async def create_clone(
        self,
        wav_path: str,
        voice_id: str | None = None,
        *,
        model_id: str = "",
        requested_voice_id: str = "001",
    ) -> VoiceActionResult:
        missing = self.missing_credentials()
        if missing:
            return VoiceActionResult(False, f"阿里云百炼缺少必要凭据：{', '.join(missing)}", clone_status="error")

        if not _is_local_audio_file(wav_path):
            return VoiceActionResult(
                False,
                "新版阿里 CosyVoice 克隆只接受本地 wav 文件直传。请检查样本路径是否存在。",
                clone_status="error",
            )

        api_key = self._api_key()
        target_model = self._target_model(model_id)
        fixed_voice_id = "001"

        def _sdk_create() -> tuple[bool, str, object]:
            return self._create_qwen_voice_with_local_file(
                file_path=wav_path,
                target_model=target_model,
                fixed_voice_id=fixed_voice_id,
                api_key=api_key,
            )

        ok, clone_id, detail = await asyncio.to_thread(_sdk_create)
        if ok:
            return VoiceActionResult(True, "阿里云百炼声音复刻完成", clone_voice_id=clone_id, clone_status="ready")

        return VoiceActionResult(False, _friendly_provider_error("阿里云百炼", detail), clone_status="error")

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_dir: Path,
        *,
        model_id: str = "",
        speed: float = DEFAULT_SPEED_RATIO,
        volume: float = DEFAULT_VOLUME_RATIO,
    ) -> VoiceActionResult:
        if not voice_id:
            return VoiceActionResult(False, "当前音色没有阿里云 voice_id，请先完成克隆")

        output_dir.mkdir(parents=True, exist_ok=True)
        model = self._target_model(model_id)
        speed = float(speed or DEFAULT_SPEED_RATIO)
        volume_value = max(0, min(100, round(50 * volume)))
        output_path = _cached_synthesis_path(
            output_dir,
            self.provider_name,
            model,
            voice_id,
            text,
            speed,
            volume_value,
            "wav",
        )
        if _is_valid_audio_cache(output_path):
            return VoiceActionResult(True, "使用缓存试听音频", output_path=str(output_path))

        missing = self.missing_credentials()
        if missing:
            return VoiceActionResult(False, f"阿里云百炼缺少必要凭据：{', '.join(missing)}")

        api_key = self._api_key()

        if model in QWEN_TTS_VC_MODELS:
            def _qwen_synth() -> tuple[bool, object]:
                try:
                    import dashscope
                    import httpx

                    dashscope.api_key = api_key
                    response = dashscope.MultiModalConversation.call(
                        model=model,
                        api_key=api_key,
                        text=text,
                        voice=voice_id,
                        language_type="Chinese",
                        stream=False,
                    )
                    if getattr(response, "status_code", None) != 200:
                        return False, getattr(response, "message", "") or getattr(response, "code", "") or response
                    output = getattr(response, "output", None)
                    audio = getattr(output, "audio", None)
                    audio_data = getattr(audio, "data", None)
                    audio_url = getattr(audio, "url", None)
                    if audio_data:
                        actual_path = _write_generated_audio(output_path, _decode_audio_data(str(audio_data)))
                        return True, str(actual_path)
                    if audio_url:
                        audio_resp = httpx.get(str(audio_url), timeout=90.0)
                        if audio_resp.status_code == 200:
                            actual_path = _write_generated_audio(
                                output_path,
                                audio_resp.content,
                                audio_resp.headers.get("content-type", ""),
                            )
                            return True, str(actual_path)
                        return False, f"下载合成音频失败：HTTP {audio_resp.status_code}"
                    return False, f"Qwen TTS 未返回音频：{response}"
                except Exception as exc:
                    return False, exc

            ok, detail = await asyncio.to_thread(_qwen_synth)
            actual_path = Path(str(detail)) if ok and detail else output_path
            if ok and _is_valid_audio_cache(actual_path):
                return VoiceActionResult(True, "语音已生成并开始播放", output_path=str(actual_path))
            return VoiceActionResult(False, _friendly_provider_error("阿里云百炼", detail))

        def _sdk_synth() -> tuple[bool, object]:
            try:
                import dashscope
                from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

                dashscope.api_key = api_key
                synthesizer = SpeechSynthesizer(
                    model=model,
                    voice=voice_id,
                    format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                    speech_rate=speed,
                    volume=volume_value,
                )
                audio = synthesizer.call(text)
                if audio:
                    actual_path = _write_generated_audio(output_path, audio)
                    return True, str(actual_path)
                return False, "DashScope SDK 未返回音频"
            except TypeError:
                try:
                    import dashscope
                    from dashscope.audio.tts_v2 import SpeechSynthesizer

                    dashscope.api_key = api_key
                    synthesizer = SpeechSynthesizer(model=model, voice=voice_id, format="wav")
                    audio = synthesizer.call(text)
                    if audio:
                        actual_path = _write_generated_audio(output_path, audio)
                        return True, str(actual_path)
                    return False, "DashScope SDK 未返回音频"
                except Exception as exc:
                    return False, exc
            except Exception as exc:
                return False, exc

        ok, detail = await asyncio.to_thread(_sdk_synth)
        actual_path = Path(str(detail)) if ok and detail else output_path
        if ok and _is_valid_audio_cache(actual_path):
            return VoiceActionResult(True, "语音已生成并开始播放", output_path=str(actual_path))

        def _http_synth() -> tuple[bool, object]:
            try:
                import httpx

                body = {
                    "model": model,
                    "input": {
                        "text": text,
                        "voice": voice_id,
                        "format": "wav",
                        "sample_rate": 24000,
                        "rate": speed,
                        "volume": volume_value,
                    },
                }
                resp = httpx.post(
                    self.SYNTHESIS_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=90.0,
                )
                content_type = resp.headers.get("content-type", "")
                if resp.status_code == 200 and "audio" in content_type.lower():
                    actual_path = _write_generated_audio(output_path, resp.content, content_type)
                    return True, str(actual_path)
                data = _extract_response_json(resp)
                audio_url = str((data.get("output") or {}).get("audio_url") or "").strip()
                if resp.status_code == 200 and audio_url:
                    audio_resp = httpx.get(audio_url, timeout=90.0)
                    if audio_resp.status_code == 200:
                        actual_path = _write_generated_audio(
                            output_path,
                            audio_resp.content,
                            audio_resp.headers.get("content-type", ""),
                        )
                        return True, str(actual_path)
                return False, f"HTTP {resp.status_code}: {data}"
            except Exception as exc:
                return False, exc

        ok, http_detail = await asyncio.to_thread(_http_synth)
        actual_path = Path(str(http_detail)) if ok and http_detail else output_path
        if ok and _is_valid_audio_cache(actual_path):
            return VoiceActionResult(True, "语音已生成并开始播放", output_path=str(actual_path))
        return VoiceActionResult(False, _friendly_provider_error("阿里云百炼", http_detail or detail))


class LocalVoiceProvider(VoiceProviderBase):
    provider_name = "local_voice"
    provider_label = "GPT-SoVITS 本地语音"

    def required_fields(self) -> set[str]:
        return set()

    def credential(self, field: str, *env_names: str) -> str:
        if field == "endpoint":
            return (
                super().credential(field, "GPT_SOVITS_ENDPOINT", "LOCAL_VOICE_ENDPOINT")
                or DEFAULT_LOCAL_VOICE_ENDPOINT
            )
        return super().credential(field, *env_names)

    async def validate_credentials(self) -> VoiceActionResult:
        ready = await ensure_local_voice_runtime(self.credential("endpoint"))
        return VoiceActionResult(ready.ok, ready.message)

    async def create_clone(
        self,
        wav_path: str,
        voice_id: str | None = None,
        *,
        model_id: str = "",
        requested_voice_id: str = "001",
    ) -> VoiceActionResult:
        path = Path(wav_path).expanduser()
        if not path.is_file():
            return VoiceActionResult(
                False,
                f"GPT-SoVITS 参考音频不存在：{wav_path}",
                clone_status="error",
            )
        return VoiceActionResult(
            True,
            "GPT-SoVITS 参考音频已就绪",
            clone_voice_id=str(path.resolve()),
            clone_status="ready",
        )

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_dir: Path,
        *,
        model_id: str = "",
        speed: float = DEFAULT_SPEED_RATIO,
        volume: float = DEFAULT_VOLUME_RATIO,
    ) -> VoiceActionResult:
        text = str(text or "").strip()
        if not text:
            return VoiceActionResult(False, "合成文本为空")

        ref_audio_path = str(voice_id or self.config.reference_audio).strip()
        if not ref_audio_path:
            return VoiceActionResult(False, "GPT-SoVITS 缺少参考音频，请先完成主播音色克隆或填写参考音频路径")
        if not Path(ref_audio_path).expanduser().is_file():
            return VoiceActionResult(False, f"GPT-SoVITS 参考音频不存在：{ref_audio_path}")

        ready = await ensure_local_voice_runtime(self.credential("endpoint"))
        if not ready.ok:
            return VoiceActionResult(False, ready.message)

        missing = self.missing_credentials()
        if missing:
            return VoiceActionResult(False, f"GPT-SoVITS 缺少必要配置：{', '.join(missing)}")

        output_dir.mkdir(parents=True, exist_ok=True)
        model = model_id if model_id in self.list_models() else self.list_models()[0]
        speed_factor = max(0.1, min(3.0, float(speed or DEFAULT_SPEED_RATIO)))
        volume_value = max(0, min(100, round(50 * float(volume or DEFAULT_VOLUME_RATIO))))
        cache_voice_id = _local_voice_cache_key(self.config, ref_audio_path)
        output_path = _cached_synthesis_path(
            output_dir,
            self.provider_name,
            model,
            cache_voice_id,
            text,
            speed_factor,
            volume_value,
            "wav",
        )
        if _is_valid_audio_cache(output_path):
            return VoiceActionResult(True, "使用缓存试听音频", output_path=str(output_path))

        endpoint = self._tts_url(self.credential("endpoint"))
        payload = {
            "text": text,
            "text_lang": self.config.text_lang or "zh",
            "ref_audio_path": ref_audio_path,
            "prompt_text": self.config.prompt_text,
            "prompt_lang": self.config.prompt_lang or "zh",
            "text_split_method": LOCAL_VOICE_TEXT_SPLIT_METHOD,
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": speed_factor,
        }

        def _http_synth() -> tuple[bool, object]:
            try:
                import httpx

                resp = httpx.post(endpoint, json=payload, timeout=120.0)
                content_type = resp.headers.get("content-type", "")
                content = getattr(resp, "content", b"") or b""
                if resp.status_code == 200 and content and _looks_like_audio_response(content, content_type):
                    actual_path = _write_generated_audio(output_path, content, content_type)
                    return True, str(actual_path)
                data = _extract_response_json(resp)
                detail = data.get("message") or data.get("detail") or data.get("code") or data
                return False, f"HTTP {resp.status_code}: {detail}"
            except Exception as exc:
                return False, exc

        ok, detail = await asyncio.to_thread(_http_synth)
        actual_path = Path(str(detail)) if ok and detail else output_path
        if ok and _is_valid_audio_cache(actual_path):
            return VoiceActionResult(True, "GPT-SoVITS 语音已生成", output_path=str(actual_path))
        return VoiceActionResult(False, f"GPT-SoVITS 请求失败：{_short_detail(detail)}")

    @staticmethod
    def _tts_url(endpoint: str) -> str:
        normalized = endpoint.strip().rstrip("/")
        if normalized.lower().endswith("/tts"):
            return normalized
        return f"{normalized}/tts"


PROVIDER_TYPES = {
    "aliyun_bailian": AliyunBailianProvider,
    "local_voice": LocalVoiceProvider,
}


class VoiceManager:
    def __init__(self, settings: VoiceSettings):
        self.settings = settings

    def set_settings(self, settings: VoiceSettings) -> None:
        self.settings = settings

    def provider(self) -> VoiceProviderBase:
        provider_name = self.settings.provider
        provider_cls = PROVIDER_TYPES.get(provider_name)
        if provider_cls is None:
            raise KeyError(f"Unknown voice provider: {provider_name}")
        return provider_cls(self.settings.api[provider_name])

    def models(self) -> tuple[str, ...]:
        return self.provider().list_models()

    async def validate_provider(self) -> VoiceActionResult:
        return await self.provider().validate_credentials()

    def install_sample(self, voice_id: str, source_path: str) -> VoiceActionResult:
        src = Path(source_path)
        if src.suffix.lower() != ".wav":
            return VoiceActionResult(False, "仅支持上传 wav 文件")
        if not src.exists():
            return VoiceActionResult(False, "样本文件不存在")
        target_dir = VOICE_DATA_DIR / voice_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "sample.wav"
        shutil.copy2(src, target_path)
        return VoiceActionResult(True, "样本文件已保存", output_path=str(target_path))

    async def create_clone(self, voice_id: str) -> VoiceActionResult:
        voice = self._voice(voice_id)
        if voice is None:
            return VoiceActionResult(False, "声音不存在", clone_status="error")
        if not voice.sample_wav_path:
            return VoiceActionResult(False, "请先上传 wav 样本", clone_status="error")
        provider = self.provider()
        result = await provider.create_clone(
            voice.sample_wav_path,
            voice_id,
            model_id=self.settings.model_id,
        )
        if result.ok and result.clone_voice_id:
            if isinstance(provider, AliyunBailianProvider):
                self.settings.model_id = provider._target_model(self.settings.model_id)
            voice.provider = self.settings.provider
            voice.clone_voice_id = result.clone_voice_id
            voice.clone_status = result.clone_status or "ready"
            voice.last_error = "" if voice.clone_status != "error" else result.message
        elif not result.ok:
            voice.clone_status = result.clone_status or "error"
            voice.last_error = result.message
        return result

    async def synthesize_role_to_file(self, text: str, role_name: str) -> VoiceActionResult:
        if not text.strip():
            return VoiceActionResult(False, "试听文本为空")
        try:
            role = self._role(role_name)
        except KeyError:
            return VoiceActionResult(False, "试听角色无效")
        provider = self.provider()
        reference_audio = provider_reference_audio(self.settings)
        voice = self._voice(role.voice_id)
        if voice is not None and not voice_entry_is_compatible(voice, self.settings.provider):
            return VoiceActionResult(False, "当前音色不适用于所选语音供应商")
        if voice is None and not reference_audio:
            return VoiceActionResult(False, "当前角色未选择声音")
        synth_voice_id = synthesis_voice_id_for_role(self.settings, role)
        if synth_voice_id is None:
            synth_voice_id = ""
        if not synth_voice_id and not reference_audio:
            return VoiceActionResult(False, "当前音色还没有可用的音色引用，请先点击开始克隆")

        if voice and synth_voice_id and voice.clone_status == "training":
            resolved = await provider.resolve_clone(voice.clone_voice_id)
            if not resolved.ok:
                voice.clone_status = "error"
                voice.last_error = resolved.message
                return VoiceActionResult(
                    False,
                    resolved.message,
                    clone_voice_id=resolved.clone_voice_id or voice.clone_voice_id,
                    clone_status="error",
                )
            if resolved.clone_status == "ready":
                if resolved.clone_voice_id:
                    voice.clone_voice_id = resolved.clone_voice_id
                    synth_voice_id = resolved.clone_voice_id
                voice.clone_status = "ready"
                voice.last_error = ""
            elif resolved.clone_status == "training":
                voice.clone_status = "training"
                return VoiceActionResult(
                    False,
                    resolved.message,
                    clone_voice_id=resolved.clone_voice_id or voice.clone_voice_id,
                    clone_status="training",
                )
            elif resolved.clone_status:
                voice.clone_status = resolved.clone_status
                voice.last_error = resolved.message
                return VoiceActionResult(
                    False,
                    resolved.message,
                    clone_voice_id=resolved.clone_voice_id or voice.clone_voice_id,
                    clone_status=resolved.clone_status,
                )
            else:
                voice.clone_status = "training"
                return VoiceActionResult(
                    False,
                    resolved.message,
                    clone_voice_id=resolved.clone_voice_id or voice.clone_voice_id,
                    clone_status="training",
                )

        result = await provider.synthesize(
            text=text,
            voice_id=synth_voice_id,
            model_id=self.settings.model_id,
            output_dir=VOICE_DATA_DIR / role_name / "generated",
            speed=_role_speed_ratio(role),
            volume=_role_volume_ratio(role),
        )
        if not result.ok:
            return result
        result.clone_voice_id = synth_voice_id or reference_audio
        result.clone_status = voice.clone_status if voice and synth_voice_id else "ready"
        return result

    async def synthesize_and_play(self, text: str, role_name: str) -> VoiceActionResult:
        reset_audio_stop()
        result = await self.synthesize_role_to_file(text, role_name)
        if not result.ok or not result.output_path:
            return result
        if is_audio_stopped():
            return result
        try:
            await play_wav_file(result.output_path)
        except Exception as exc:
            return VoiceActionResult(False, f"播放失败：{exc}", output_path=result.output_path)
        if result.message != "使用缓存试听音频":
            result.message = "试听已播放"
        return result

    async def clone_and_synthesize(
        self,
        sample_audio_url: str,
        text: str,
        output_dir: Path | None = None,
    ) -> VoiceActionResult:
        """Register an Aliyun CosyVoice clone and synthesize text with it."""
        if not text.strip():
            return VoiceActionResult(False, "合成文本为空")
        provider = self.provider()
        clone_result = await provider.create_clone(
            sample_audio_url,
            model_id=self.settings.model_id,
        )
        if not clone_result.ok or not clone_result.clone_voice_id:
            return clone_result
        synth_result = await provider.synthesize(
            text=text,
            voice_id=clone_result.clone_voice_id,
            model_id=self.settings.model_id,
            output_dir=output_dir or (VOICE_DATA_DIR / "generated"),
            speed=DEFAULT_SPEED_RATIO,
            volume=DEFAULT_VOLUME_RATIO,
        )
        synth_result.clone_voice_id = clone_result.clone_voice_id
        synth_result.clone_status = clone_result.clone_status or "ready"
        return synth_result

    async def synthesize_for_tts_worker(self, reply: dict, role_name: str) -> dict:
        text = str(reply.get("text", "")).strip()
        result = await self.synthesize_and_play(text, role_name)
        now_ms = int(asyncio.get_running_loop().time() * 1000)
        return {
            "type": "tts",
            "reply_id": reply.get("reply_id", ""),
            "room_id": reply.get("room_id", ""),
            "text": text,
            "status": "ok" if result.ok else "error",
            "detail": result.message,
            "output_path": result.output_path,
            "timestamp": now_ms / 1000.0,
            "time": reply.get("time", ""),
            "ts_ms": now_ms,
        }

    @staticmethod
    def temp_text_wav() -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
            return fp.name

    def _role(self, role_name: str) -> VoiceRoleConfig:
        if role_name == "anchor":
            return self.settings.anchor
        if role_name == "copilot":
            return self.settings.copilot
        raise KeyError(f"Unknown voice role: {role_name}")

    def _voice(self, voice_id: str) -> VoiceEntry | None:
        if not voice_id:
            return None
        return self.settings.find_voice(voice_id)
