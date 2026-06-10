"""Live copilot/control configuration helpers.

This module keeps the structured live-room settings separate from the UI so
tests, persistence, and the reply engine use the same defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_TEMPLATE_NAME = "默认场控模板"
TONE_STYLES = ("natural", "warm", "professional", "sales", "after_sales")
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


TONE_STYLE_LABELS = {
    "natural": "自然",
    "warm": "热情",
    "professional": "专业",
    "sales": "催单",
    "after_sales": "售后解释",
}


def normalize_scheduled_scripts(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace("\r\n", "\n").split("\n\n")
    elif isinstance(value, (list, tuple)):
        candidates = [str(item or "") for item in value]
    else:
        candidates = []

    scripts: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = "\n".join(line.strip() for line in str(item or "").splitlines()).strip()
        if not text or text in seen:
            continue
        scripts.append(text)
        seen.add(text)
    return scripts


@dataclass(slots=True)
class LiveControlTemplate:
    name: str = DEFAULT_TEMPLATE_NAME
    product_info: str = ""
    anchor_persona: str = ""
    after_sales_policy: str = ""
    forbidden_commitments: str = ""
    reply_boundaries: str = ""
    platform_rules: str = ""
    faq: str = ""
    scheduled_scripts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None, name: str = "") -> "LiveControlTemplate":
        data = data or {}
        return cls(
            name=str(data.get("name") or name or DEFAULT_TEMPLATE_NAME).strip()
            or DEFAULT_TEMPLATE_NAME,
            product_info=str(data.get("product_info") or ""),
            anchor_persona=str(data.get("anchor_persona") or ""),
            after_sales_policy=str(data.get("after_sales_policy") or ""),
            forbidden_commitments=str(data.get("forbidden_commitments") or ""),
            reply_boundaries=str(data.get("reply_boundaries") or ""),
            platform_rules=str(data.get("platform_rules") or ""),
            faq=str(data.get("faq") or ""),
            scheduled_scripts=normalize_scheduled_scripts(
                data.get("scheduled_scripts", data.get("scheduled_scripts_text", ""))
            ),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "product_info": self.product_info,
            "anchor_persona": self.anchor_persona,
            "after_sales_policy": self.after_sales_policy,
            "forbidden_commitments": self.forbidden_commitments,
            "reply_boundaries": self.reply_boundaries,
            "platform_rules": self.platform_rules,
            "faq": self.faq,
            "scheduled_scripts": normalize_scheduled_scripts(self.scheduled_scripts),
        }


@dataclass(slots=True)
class LiveControlSettings:
    templates: dict[str, LiveControlTemplate] = field(default_factory=dict)
    active_template: str = DEFAULT_TEMPLATE_NAME
    auto_reply: bool = False
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    reply_char_limit: int = 80
    user_cooldown_sec: int = 60
    global_cooldown_sec: int = 30
    tone_style: str = "natural"
    mention_user: bool = True
    voice_reply_enabled: bool = False
    scheduled_scripts_enabled: bool = False
    scheduled_scripts_interval_sec: int = 120
    scheduled_scripts_random_order: bool = False
    scheduled_scripts_random_space_enabled: bool = False
    scheduled_scripts_voice_enabled: bool = False

    @classmethod
    def from_settings(cls, data: dict | None) -> "LiveControlSettings":
        data = dict(data or {})
        live_data = data.get("live_control")
        if isinstance(live_data, dict):
            templates_raw = live_data.get("templates") or {}
            active = str(live_data.get("active_template") or "").strip()
            source = live_data
        else:
            templates_raw = {}
            active = ""
            source = data

        templates: dict[str, LiveControlTemplate] = {}
        if isinstance(templates_raw, dict):
            for key, value in templates_raw.items():
                template = LiveControlTemplate.from_dict(
                    value if isinstance(value, dict) else {}, name=str(key)
                )
                templates[template.name] = template

        if not templates:
            templates[DEFAULT_TEMPLATE_NAME] = _template_from_legacy(data)

        if not active or active not in templates:
            active = next(iter(templates.keys()), DEFAULT_TEMPLATE_NAME)

        return cls(
            templates=templates,
            active_template=active,
            auto_reply=bool(data.get("auto_reply", source.get("auto_reply", False))),
            api_key=str(data.get("api_key", source.get("api_key", "")) or ""),
            base_url=_str_or_default(
                data.get("base_url", source.get("base_url", DEFAULT_BASE_URL)),
                DEFAULT_BASE_URL,
            ),
            model=_str_or_default(
                data.get("model", source.get("model", DEFAULT_MODEL)),
                DEFAULT_MODEL,
            ),
            reply_char_limit=_clamp_int(source.get("reply_char_limit", 80), 20, 500),
            user_cooldown_sec=_clamp_int(source.get("user_cooldown_sec", 60), 0, 36000),
            global_cooldown_sec=_clamp_int(
                source.get("global_cooldown_sec", data.get("reply_interval", 30)),
                0,
                36000,
            ),
            tone_style=_normalize_tone_style(source.get("tone_style", "natural")),
            mention_user=bool(source.get("mention_user", True)),
            voice_reply_enabled=bool(source.get("voice_reply_enabled", False)),
            scheduled_scripts_enabled=bool(source.get("scheduled_scripts_enabled", False)),
            scheduled_scripts_interval_sec=_clamp_int(
                source.get("scheduled_scripts_interval_sec", 120),
                10,
                36000,
            ),
            scheduled_scripts_random_order=bool(
                source.get("scheduled_scripts_random_order", False)
            ),
            scheduled_scripts_random_space_enabled=bool(
                source.get("scheduled_scripts_random_space_enabled", False)
            ),
            scheduled_scripts_voice_enabled=bool(
                source.get("scheduled_scripts_voice_enabled", False)
            ),
        )

    def to_settings_payload(self) -> dict:
        return {
            "templates": {
                name: template.to_dict()
                for name, template in self.templates.items()
            },
            "active_template": self.active_template,
            "reply_char_limit": self.reply_char_limit,
            "user_cooldown_sec": self.user_cooldown_sec,
            "global_cooldown_sec": self.global_cooldown_sec,
            "tone_style": self.tone_style,
            "mention_user": self.mention_user,
            "voice_reply_enabled": self.voice_reply_enabled,
            "scheduled_scripts_enabled": self.scheduled_scripts_enabled,
            "scheduled_scripts_interval_sec": self.scheduled_scripts_interval_sec,
            "scheduled_scripts_random_order": self.scheduled_scripts_random_order,
            "scheduled_scripts_random_space_enabled": self.scheduled_scripts_random_space_enabled,
            "scheduled_scripts_voice_enabled": self.scheduled_scripts_voice_enabled,
        }

    def get_active_template(self) -> LiveControlTemplate:
        return self.templates.get(self.active_template) or next(iter(self.templates.values()))


def _template_from_legacy(data: dict) -> LiveControlTemplate:
    template = LiveControlTemplate(name=DEFAULT_TEMPLATE_NAME)
    if any(str(data.get(key) or "").strip() for key in (
        "persona_role",
        "persona_strategy",
        "persona_scene",
        "persona_tone",
        "persona_limit",
        "persona_taboo",
    )):
        template.anchor_persona = str(data.get("persona_role") or "")
        template.reply_boundaries = "\n\n".join(
            value for value in (
                str(data.get("persona_strategy") or "").strip(),
                str(data.get("persona_scene") or "").strip(),
                str(data.get("persona_tone") or "").strip(),
                str(data.get("persona_limit") or "").strip(),
            )
            if value
        )
        template.forbidden_commitments = str(data.get("persona_taboo") or "")
    return template


def _str_or_default(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(parsed, maximum))


def _normalize_tone_style(value: object) -> str:
    text = str(value or "").strip()
    return text if text in TONE_STYLES else "natural"
