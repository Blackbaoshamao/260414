"""Runtime config loader (ENV first, optional YAML override)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app_paths import app_dir


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


@dataclass
class RuntimeConfig:
    room_id: str = ""
    model_name: str = "rule-based"
    dedupe_window_ms: int = 700
    reorder_window_ms: int = 600
    latency_target_ms: int = 3000
    reply_min_interval_ms: int = 1200
    reply_user_cooldown_ms: int = 2500
    reply_max_chars: int = 80
    persona_name: str = "助手"
    persona_style: str = "热情、简洁、直播互动口吻"
    blacklist_keywords: list[str] = field(default_factory=lambda: ["政治敏感", "暴力细节"])
    whitelist_keywords: list[str] = field(default_factory=list)
    replay_log_path: str = "replay_log.jsonl"
    tts_timeout_ms: int = 3000
    tts_queue_size: int = 200


def _maybe_load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        logger.warning("Config file exists but PyYAML is unavailable: {}", path)
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.warning("Failed to parse YAML config {}: {}", path, e)
        return {}


def load_runtime_config(path: str | Path = "runtime.yaml") -> RuntimeConfig:
    cfg = RuntimeConfig()
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = app_dir() / config_path
    yaml_data = _maybe_load_yaml(config_path)

    def env_or_yaml(env_key: str, yaml_key: str, default: str) -> str:
        if env_key in os.environ:
            return os.environ[env_key]
        if yaml_key in yaml_data and yaml_data[yaml_key] is not None:
            return str(yaml_data[yaml_key])
        return default

    cfg.room_id = env_or_yaml("DYDM_ROOM_ID", "room_id", cfg.room_id)
    cfg.model_name = env_or_yaml("DYDM_MODEL", "model_name", cfg.model_name)
    cfg.dedupe_window_ms = int(
        env_or_yaml("DYDM_DEDUPE_WINDOW_MS", "dedupe_window_ms", str(cfg.dedupe_window_ms))
    )
    cfg.reorder_window_ms = int(
        env_or_yaml("DYDM_REORDER_WINDOW_MS", "reorder_window_ms", str(cfg.reorder_window_ms))
    )
    cfg.latency_target_ms = int(
        env_or_yaml("DYDM_LATENCY_TARGET_MS", "latency_target_ms", str(cfg.latency_target_ms))
    )
    cfg.reply_min_interval_ms = int(
        env_or_yaml("DYDM_REPLY_MIN_INTERVAL_MS", "reply_min_interval_ms", str(cfg.reply_min_interval_ms))
    )
    cfg.reply_user_cooldown_ms = int(
        env_or_yaml("DYDM_REPLY_USER_COOLDOWN_MS", "reply_user_cooldown_ms", str(cfg.reply_user_cooldown_ms))
    )
    cfg.reply_max_chars = int(
        env_or_yaml("DYDM_REPLY_MAX_CHARS", "reply_max_chars", str(cfg.reply_max_chars))
    )
    cfg.persona_name = env_or_yaml("DYDM_PERSONA_NAME", "persona_name", cfg.persona_name)
    cfg.persona_style = env_or_yaml("DYDM_PERSONA_STYLE", "persona_style", cfg.persona_style)
    cfg.replay_log_path = env_or_yaml("DYDM_REPLAY_LOG_PATH", "replay_log_path", cfg.replay_log_path)
    cfg.tts_timeout_ms = int(
        env_or_yaml("DYDM_TTS_TIMEOUT_MS", "tts_timeout_ms", str(cfg.tts_timeout_ms))
    )
    cfg.tts_queue_size = int(
        env_or_yaml("DYDM_TTS_QUEUE_SIZE", "tts_queue_size", str(cfg.tts_queue_size))
    )

    blacklist = env_or_yaml("DYDM_BLACKLIST", "blacklist_keywords", "")
    if blacklist:
        if blacklist.startswith("[") and blacklist.endswith("]"):
            # Handle yaml-style list string fallback.
            blacklist = blacklist.strip("[]").replace("'", "").replace('"', "")
        cfg.blacklist_keywords = _split_csv(blacklist)
    elif isinstance(yaml_data.get("blacklist_keywords"), list):
        cfg.blacklist_keywords = [str(x) for x in yaml_data["blacklist_keywords"] if str(x)]

    whitelist = env_or_yaml("DYDM_WHITELIST", "whitelist_keywords", "")
    if whitelist:
        cfg.whitelist_keywords = _split_csv(whitelist)
    elif isinstance(yaml_data.get("whitelist_keywords"), list):
        cfg.whitelist_keywords = [str(x) for x in yaml_data["whitelist_keywords"] if str(x)]

    return cfg
