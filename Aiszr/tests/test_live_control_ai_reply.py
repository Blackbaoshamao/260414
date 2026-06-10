import time
from unittest.mock import AsyncMock

import pytest

from ai_reply import (
    AIConfig,
    AIReplyEngine,
    build_live_control_system_prompt,
)
from live_control_config import LiveControlTemplate
from live_session_memory import LiveSessionMemory


def _msg(content="hello", nickname="user1", user_id="u1", msg_type="chat"):
    return {
        "content": content,
        "nickname": nickname,
        "user_id": user_id,
        "type": msg_type,
        "source": "wechat",
    }


def test_prompt_includes_live_control_sections_and_hard_rules():
    prompt = build_live_control_system_prompt(
        LiveControlTemplate(
            name="水果专场",
            product_info="苹果 5 斤装，页面标价为准",
            anchor_persona="专业但不夸张",
            forbidden_commitments="不承诺次日达",
        ),
        reply_char_limit=66,
        tone_style="professional",
    )

    assert "直播间 AI 场控助播" in prompt
    assert "每条回复不超过 66 个中文字符" in prompt
    assert "不要编造价格" in prompt
    assert "苹果 5 斤装" in prompt
    assert "不承诺次日达" in prompt
    assert "专业" in prompt


class TestLiveControlRuntime:
    def setup_method(self):
        self.config = AIConfig(
            api_key="test-key",
            auto_reply=True,
            reply_interval=0,
            user_cooldown_sec=5,
            reply_char_limit=20,
            live_control_template=LiveControlTemplate(product_info="苹果 5 斤装"),
            voice_reply_enabled=True,
            mention_user=False,
        )
        self.engine = AIReplyEngine(self.config)

    def test_should_reply_uses_configurable_user_cooldown(self):
        msg = _msg(user_id="u1")
        key = self.engine._cooldown_user_key(msg)
        self.engine._user_cooldowns[key] = time.time()

        assert self.engine._should_reply(msg) is False

        self.engine._user_cooldowns[key] = time.time() - 6
        assert self.engine._should_reply(msg) is True

    @pytest.mark.asyncio
    async def test_process_message_records_memory_even_when_throttled(self, tmp_path):
        memory = LiveSessionMemory(tmp_path / "session.sqlite3")
        self.engine.set_memory(memory)
        self.engine._last_reply_time = time.time()
        self.config.reply_interval = 30
        msg = _msg(content="包邮吗")

        result = await self.engine.process_message(msg)

        assert result is None
        assert "_live_user_memory" not in msg
        assert memory.count_users() == 1
        user = memory.get_user("user1", "wechat", "u1")
        assert user is not None
        assert user.last_message == "包邮吗"

    @pytest.mark.asyncio
    async def test_llm_prompt_includes_template_and_session_memory(self, tmp_path):
        memory = LiveSessionMemory(tmp_path / "session.sqlite3")
        user_memory = memory.record_message(
            "user1", "wechat", "多少钱", user_id="u1"
        )
        self.engine.set_memory(memory)
        self.engine._client.chat = AsyncMock(
            return_value="这是一个会被截断的很长很长很长回复，后面还有很多不能直接输出的内容"
        )

        reply = await self.engine._get_llm_reply(
            "user1", "多少钱", "u1", platform="wechat", memory=user_memory
        )

        messages = self.engine._client.chat.call_args.args[0]
        system_prompt = messages[0]["content"]
        assert "苹果 5 斤装" in system_prompt
        assert "本场直播用户记忆" in system_prompt
        assert "[平台:wechat][用户:user1]" in messages[1]["content"]
        assert reply.endswith("。")
        assert len(reply) <= 21

    @pytest.mark.asyncio
    async def test_process_message_returns_voice_and_mention_flags(self):
        self.engine._client.chat = AsyncMock(return_value="可以看一号链接")

        result = await self.engine.process_message(_msg())

        assert result is not None
        assert result.use_voice is True
        assert result.mention_user is False
        assert result.platform == "wechat"
