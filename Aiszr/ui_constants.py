"""Constants and data normalization — extracted from ui.py."""

from voice_models import VoiceSettings


DEFAULT_DATA_SOURCE = "playwright"
_DATA_SOURCE_OPTIONS = [
    ("Playwright（推荐）", DEFAULT_DATA_SOURCE),
    ("开放平台 API", "open_api"),
]
_MESSAGE_FILTER_OPTIONS = [
    ("chat", "弹幕"),
    ("enter", "入场"),
    ("gift", "送礼"),
    ("follow", "关注"),
    ("share", "分享"),
    ("like", "点赞"),
]
_DEFAULT_MESSAGE_FILTERS = {key: True for key, _ in _MESSAGE_FILTER_OPTIONS}
DEFAULT_VOICE_SETTINGS = VoiceSettings()
_VOICE_PROVIDER_API_FIELDS = {
    "aliyun_bailian": {
        "hint": "阿里云百炼目前只需要填写 API Key。",
        "fields": {
            "api_key": ("API Key", "请输入阿里云百炼 API Key"),
        },
    },
    "local_voice": {
        "hint": "GPT-SoVITS 本地语音由软件自动配置并启动。",
        "fields": {},
    },
}
_SECRET_INPUT_FIELD_KEYS = {"api_key", "api_secret", "access_key_id", "access_key_secret"}
_BROKEN_TEXT_PLACEHOLDERS = {"?", "??", "???", "????", "?????", "??????"}
_MYSTERY_VIEWER_NAMES = {"神秘观众", "绁炵瑙備紬"}
_MOJIBAKE_MARKERS = ("闂", "鍥", "閸", "鈥", "鎱", "傚", "鏆", "顫", "锛")


def _is_broken_text(value: object) -> bool:
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    if not stripped or stripped in _BROKEN_TEXT_PLACEHOLDERS:
        return True
    return any(marker in stripped for marker in _MOJIBAKE_MARKERS)


def _text_or_default(value: object, default: str) -> str:
    return default if _is_broken_text(value) else value.strip()


def _normalize_display_nickname(value: object) -> str:
    if not isinstance(value, str):
        return ""
    nickname = value.strip()
    if not nickname or nickname in _MYSTERY_VIEWER_NAMES:
        return ""
    return nickname


def _normalize_data_source(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_DATA_SOURCE
    legacy_map = {
        "Playwright (推荐)": DEFAULT_DATA_SOURCE,
        "Playwright（推荐）": DEFAULT_DATA_SOURCE,
        "Playwright (??)": DEFAULT_DATA_SOURCE,
        "开放平台 API": "open_api",
        "???? API": "open_api",
    }
    if _is_broken_text(value):
        return DEFAULT_DATA_SOURCE
    return legacy_map.get(value, value)


def _normalize_message_filters(value: object) -> dict[str, bool]:
    filters = dict(_DEFAULT_MESSAGE_FILTERS)
    if not isinstance(value, dict):
        return filters
    for key, _ in _MESSAGE_FILTER_OPTIONS:
        if key in value:
            filters[key] = bool(value.get(key))
    return filters

# Card dimensions for video gallery
_CARD_W, _CARD_H = 140, 200
STREAM_CARD_H = 200
