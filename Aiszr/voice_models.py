"""Voice configuration models for AI voice page."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from local_voice_runtime import DEFAULT_LOCAL_VOICE_ENDPOINT


VOICE_PROVIDERS = ("aliyun_bailian", "local_voice")
VOICE_PROVIDER_LABELS = {
    "aliyun_bailian": "阿里云百炼",
    "local_voice": "GPT-SoVITS 本地语音",
}
VOICE_MODELS = {
    "aliyun_bailian": ("qwen3-tts-vc-2026-01-22",),
    "local_voice": ("gpt-sovits-v2",),
}
DEFAULT_VOICE_PROVIDER = "local_voice"
DEFAULT_VOICE_MODEL = VOICE_MODELS[DEFAULT_VOICE_PROVIDER][0]


@dataclass(slots=True)
class VoiceProviderApiConfig:
    app_id: str = ""
    api_key: str = ""
    api_secret: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    endpoint: str = ""
    region: str = ""
    reference_audio: str = ""
    prompt_text: str = ""
    prompt_lang: str = "zh"
    text_lang: str = "zh"

    @classmethod
    def from_dict(cls, value: object) -> "VoiceProviderApiConfig":
        if not isinstance(value, dict):
            return cls()
        return cls(
            app_id=str(value.get("app_id", "")).strip(),
            api_key=str(value.get("api_key", "")).strip(),
            api_secret=str(value.get("api_secret", "")).strip(),
            access_key_id=str(value.get("access_key_id", "")).strip(),
            access_key_secret=str(value.get("access_key_secret", "")).strip(),
            endpoint=str(value.get("endpoint", "")).strip(),
            region=str(value.get("region", "")).strip(),
            reference_audio=str(value.get("reference_audio", "")).strip(),
            prompt_text=str(value.get("prompt_text", "")).strip(),
            prompt_lang=str(value.get("prompt_lang", "zh")).strip() or "zh",
            text_lang=str(value.get("text_lang", "zh")).strip() or "zh",
        )

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "access_key_id": self.access_key_id,
            "access_key_secret": self.access_key_secret,
            "endpoint": self.endpoint,
            "region": self.region,
            "reference_audio": self.reference_audio,
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_lang,
            "text_lang": self.text_lang,
        }


def default_provider_api_config(provider: str) -> VoiceProviderApiConfig:
    cfg = VoiceProviderApiConfig()
    if provider == "local_voice":
        cfg.endpoint = DEFAULT_LOCAL_VOICE_ENDPOINT
        cfg.prompt_lang = "zh"
        cfg.text_lang = "zh"
    return cfg


def default_provider_api_configs() -> dict[str, VoiceProviderApiConfig]:
    return {name: default_provider_api_config(name) for name in VOICE_PROVIDERS}


def apply_provider_api_defaults(provider: str, cfg: VoiceProviderApiConfig) -> VoiceProviderApiConfig:
    if provider == "local_voice":
        cfg.endpoint = cfg.endpoint or DEFAULT_LOCAL_VOICE_ENDPOINT
        cfg.prompt_lang = cfg.prompt_lang or "zh"
        cfg.text_lang = cfg.text_lang or "zh"
    return cfg


@dataclass(slots=True)
class VoiceEntry:
    id: str = ""
    name: str = ""
    provider: str = ""
    sample_wav_path: str = ""
    clone_voice_id: str = ""
    clone_status: str = "idle"
    last_error: str = ""
    trained_model_dir: str = ""

    @classmethod
    def from_dict(cls, value: object) -> "VoiceEntry":
        if not isinstance(value, dict):
            return cls()
        return cls(
            id=str(value.get("id", "")).strip(),
            name=str(value.get("name", "")).strip(),
            provider=str(value.get("provider", "")).strip(),
            sample_wav_path=str(value.get("sample_wav_path", "")).strip(),
            clone_voice_id=str(value.get("clone_voice_id", "")).strip(),
            clone_status=str(value.get("clone_status", "idle")).strip() or "idle",
            last_error=str(value.get("last_error", "")).strip(),
            trained_model_dir=str(value.get("trained_model_dir", "")).strip(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "sample_wav_path": self.sample_wav_path,
            "clone_voice_id": self.clone_voice_id,
            "clone_status": self.clone_status,
            "last_error": self.last_error,
            "trained_model_dir": self.trained_model_dir,
        }

    @staticmethod
    def make_id() -> str:
        return uuid.uuid4().hex[:12]


def voice_entry_is_compatible(voice: VoiceEntry, provider: str) -> bool:
    if not voice.provider:
        return True
    return voice.provider == provider


def compatible_voice_entries(voices: list[VoiceEntry], provider: str) -> list[VoiceEntry]:
    return [voice for voice in voices if voice_entry_is_compatible(voice, provider)]


@dataclass(slots=True)
class VoiceRoleConfig:
    voice_id: str = ""
    speed: int = 100
    timbre_strength: int = 50
    volume_gain: int = 100
    enabled: bool = False

    @classmethod
    def from_dict(cls, value: object) -> "VoiceRoleConfig":
        if not isinstance(value, dict):
            return cls()
        return cls(
            voice_id=str(value.get("voice_id", "")).strip(),
            speed=max(50, min(150, int(value.get("speed", 100) or 100))),
            timbre_strength=max(0, min(100, int(value.get("timbre_strength", 50) or 50))),
            volume_gain=max(0, min(200, int(value.get("volume_gain", 100) or 100))),
            enabled=bool(value.get("enabled", False)),
        )

    def to_dict(self) -> dict:
        return {
            "voice_id": self.voice_id,
            "speed": self.speed,
            "timbre_strength": self.timbre_strength,
            "volume_gain": self.volume_gain,
            "enabled": self.enabled,
        }


@dataclass(slots=True)
class VoiceSettings:
    provider: str = DEFAULT_VOICE_PROVIDER
    model_id: str = DEFAULT_VOICE_MODEL
    api: dict[str, VoiceProviderApiConfig] = field(
        default_factory=default_provider_api_configs
    )
    voices: list[VoiceEntry] = field(default_factory=list)
    anchor: VoiceRoleConfig = field(default_factory=VoiceRoleConfig)
    copilot: VoiceRoleConfig = field(default_factory=VoiceRoleConfig)
    copilot_auto_broadcast: bool = False
    copilot_auto_reply: bool = False
    anchor_script: str = ""
    copywriting_api_key: str = ""
    copywriting_base_url: str = "https://api.deepseek.com/v1"
    copywriting_model: str = "deepseek-chat"

    @classmethod
    def from_dict(cls, value: object) -> "VoiceSettings":
        if not isinstance(value, dict):
            return cls()
        provider = str(value.get("provider", DEFAULT_VOICE_PROVIDER)).strip() or DEFAULT_VOICE_PROVIDER
        if provider not in VOICE_PROVIDERS:
            provider = DEFAULT_VOICE_PROVIDER
        model_id = str(value.get("model_id", "")).strip() or VOICE_MODELS[provider][0]
        if model_id not in VOICE_MODELS[provider]:
            model_id = VOICE_MODELS[provider][0]
        api_raw = value.get("api", {})
        api = {
            name: apply_provider_api_defaults(
                name,
                VoiceProviderApiConfig.from_dict(api_raw.get(name, {}) if isinstance(api_raw, dict) else {}),
            )
            for name in VOICE_PROVIDERS
        }
        voices_raw = value.get("voices", [])
        voices = [VoiceEntry.from_dict(v) for v in voices_raw] if isinstance(voices_raw, list) else []

        settings = cls(
            provider=provider,
            model_id=model_id,
            api=api,
            voices=voices,
            anchor=VoiceRoleConfig.from_dict(value.get("anchor", {})),
            copilot=VoiceRoleConfig.from_dict(value.get("copilot", {})),
            copilot_auto_broadcast=bool(value.get("copilot_auto_broadcast", False)),
            copilot_auto_reply=bool(value.get("copilot_auto_reply", False)),
            anchor_script=str(value.get("anchor_script", "")).strip(),
            copywriting_api_key=str(value.get("copywriting_api_key", "")).strip(),
            copywriting_base_url=str(value.get("copywriting_base_url", "https://api.deepseek.com/v1")).strip() or "https://api.deepseek.com/v1",
            copywriting_model=str(value.get("copywriting_model", "deepseek-chat")).strip() or "deepseek-chat",
        )

        # Migration: old settings without voices list
        if not voices:
            settings._migrate_from_legacy(value)

        return settings

    def _migrate_from_legacy(self, raw: dict) -> None:
        for role_key in ("anchor", "copilot"):
            role_raw = raw.get(role_key, {})
            if not isinstance(role_raw, dict):
                continue
            name = str(role_raw.get("display_name", "")).strip()
            sample = str(role_raw.get("sample_wav_path", "")).strip()
            clone_id = str(role_raw.get("clone_voice_id", "")).strip()
            status = str(role_raw.get("clone_status", "idle")).strip() or "idle"
            if not name and not sample and not clone_id:
                continue
            entry = VoiceEntry(
                id=VoiceEntry.make_id(),
                name=name or role_key,
                provider=self.provider,
                sample_wav_path=sample,
                clone_voice_id=clone_id,
                clone_status=status,
                last_error=str(role_raw.get("last_error", "")).strip(),
            )
            self.voices.append(entry)
            role_cfg = getattr(self, role_key)
            role_cfg.voice_id = entry.id

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "api": {name: cfg.to_dict() for name, cfg in self.api.items()},
            "voices": [v.to_dict() for v in self.voices],
            "anchor": self.anchor.to_dict(),
            "copilot": self.copilot.to_dict(),
            "copilot_auto_broadcast": self.copilot_auto_broadcast,
            "copilot_auto_reply": self.copilot_auto_reply,
            "anchor_script": self.anchor_script,
            "copywriting_api_key": self.copywriting_api_key,
            "copywriting_base_url": self.copywriting_base_url,
            "copywriting_model": self.copywriting_model,
        }

    def find_voice(self, voice_id: str) -> VoiceEntry | None:
        for v in self.voices:
            if v.id == voice_id:
                return v
        return None
