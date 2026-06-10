from live_control_config import (
    DEFAULT_TEMPLATE_NAME,
    LiveControlSettings,
    LiveControlTemplate,
    normalize_scheduled_scripts,
)


def test_live_control_settings_builds_default_template():
    settings = LiveControlSettings.from_settings({})

    assert settings.active_template == DEFAULT_TEMPLATE_NAME
    assert DEFAULT_TEMPLATE_NAME in settings.templates
    assert settings.reply_char_limit == 80
    assert settings.user_cooldown_sec == 60
    assert settings.global_cooldown_sec == 30


def test_live_control_settings_loads_templates_and_active_template():
    settings = LiveControlSettings.from_settings(
        {
            "api_key": "key",
            "live_control": {
                "active_template": "水果专场",
                "reply_char_limit": 120,
                "user_cooldown_sec": 15,
                "global_cooldown_sec": 8,
                "tone_style": "professional",
                "mention_user": False,
                "voice_reply_enabled": True,
                "templates": {
                    "水果专场": {
                        "product_info": "苹果 5 斤装",
                        "anchor_persona": "专业助播",
                    }
                },
            },
        }
    )

    assert settings.api_key == "key"
    assert settings.active_template == "水果专场"
    assert settings.get_active_template().product_info == "苹果 5 斤装"
    assert settings.reply_char_limit == 120
    assert settings.user_cooldown_sec == 15
    assert settings.global_cooldown_sec == 8
    assert settings.tone_style == "professional"
    assert settings.mention_user is False
    assert settings.voice_reply_enabled is True


def test_live_control_settings_payload_roundtrip():
    settings = LiveControlSettings(
        templates={
            "A": LiveControlTemplate(
                name="A",
                product_info="商品",
                forbidden_commitments="不承诺次日达",
                scheduled_scripts=["欢迎新进直播间的朋友。", "需要的点小黄车。"],
            )
        },
        active_template="A",
        reply_char_limit=90,
        scheduled_scripts_enabled=True,
        scheduled_scripts_interval_sec=180,
        scheduled_scripts_random_order=True,
        scheduled_scripts_random_space_enabled=True,
        scheduled_scripts_voice_enabled=True,
    )

    payload = settings.to_settings_payload()

    assert payload["active_template"] == "A"
    assert payload["templates"]["A"]["product_info"] == "商品"
    assert payload["templates"]["A"]["forbidden_commitments"] == "不承诺次日达"
    assert payload["templates"]["A"]["scheduled_scripts"] == [
        "欢迎新进直播间的朋友。",
        "需要的点小黄车。",
    ]
    assert payload["scheduled_scripts_enabled"] is True
    assert payload["scheduled_scripts_interval_sec"] == 180
    assert payload["scheduled_scripts_random_order"] is True
    assert payload["scheduled_scripts_random_space_enabled"] is True
    assert payload["scheduled_scripts_voice_enabled"] is True


def test_scheduled_scripts_are_normalized_from_text_and_deduplicated():
    scripts = normalize_scheduled_scripts(
        " 欢迎新朋友进直播间 \n\n\n需要的朋友点小黄车\n看清规格再拍\n\n欢迎新朋友进直播间"
    )

    assert scripts == [
        "欢迎新朋友进直播间",
        "需要的朋友点小黄车\n看清规格再拍",
    ]


def test_live_control_settings_loads_scheduled_script_controls():
    settings = LiveControlSettings.from_settings(
        {
            "live_control": {
                "active_template": "A",
                "scheduled_scripts_enabled": True,
                "scheduled_scripts_interval_sec": 3,
                "scheduled_scripts_random_order": True,
                "scheduled_scripts_random_space_enabled": True,
                "scheduled_scripts_voice_enabled": True,
                "templates": {
                    "A": {
                        "scheduled_scripts": ["欢迎", "看小黄车"],
                    }
                },
            }
        }
    )

    assert settings.scheduled_scripts_enabled is True
    assert settings.scheduled_scripts_interval_sec == 10
    assert settings.scheduled_scripts_random_order is True
    assert settings.scheduled_scripts_random_space_enabled is True
    assert settings.scheduled_scripts_voice_enabled is True
    assert settings.get_active_template().scheduled_scripts == ["欢迎", "看小黄车"]


def test_live_control_settings_migrates_legacy_persona_fields():
    settings = LiveControlSettings.from_settings(
        {
            "persona_role": "旧主播人设",
            "persona_strategy": "旧回复策略",
            "persona_taboo": "旧禁用内容",
        }
    )

    template = settings.get_active_template()
    assert template.anchor_persona == "旧主播人设"
    assert "旧回复策略" in template.reply_boundaries
    assert template.forbidden_commitments == "旧禁用内容"
