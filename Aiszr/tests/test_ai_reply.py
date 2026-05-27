"""Tests for AI reply engine — throttle, history, dedup, fallback."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ai_reply import (
    AIConfig,
    AIReplyEngine,
    DeepSeekClient,
    RuleBasedFallback,
    is_short_message,
    guess_intent,
)


def _msg(content="hello", nickname="user1", user_id="u1", msg_type="chat"):
    return {
        "content": content,
        "nickname": nickname,
        "user_id": user_id,
        "type": msg_type,
    }


# --- is_short_message / guess_intent ---

class TestShortMessage:
    def test_short_string(self):
        assert is_short_message("hi") is True

    def test_repeated_char(self):
        assert is_short_message("111") is True
        assert is_short_message("...") is True

    def test_normal_message(self):
        assert is_short_message("你好呀，今天心情怎么样") is False

    def test_digit_only(self):
        assert is_short_message("666") is True

    def test_punct_only(self):
        assert is_short_message("？？？") is True


class TestGuessIntent:
    def test_digits(self):
        assert "刷屏" in guess_intent("111") or "测试" in guess_intent("111")

    def test_question_marks(self):
        assert "问号" in guess_intent("？？？")


# --- _should_reply throttle ---

class TestShouldReply:
    def setup_method(self):
        self.config = AIConfig(
            api_key="test-key",
            auto_reply=True,
            reply_interval=10,
        )
        self.engine = AIReplyEngine(self.config)

    def test_first_message_passes(self):
        assert self.engine._should_reply(_msg()) is True

    def test_second_message_blocked_within_interval(self):
        self.engine._last_reply_time = time.time()
        assert self.engine._should_reply(_msg()) is False

    def test_message_passes_after_interval(self):
        self.engine._last_reply_time = time.time() - 11
        assert self.engine._should_reply(_msg()) is True

    def test_non_chat_type_rejected(self):
        # _should_reply only checks content/throttle; type is filtered in process_message
        assert self.engine._should_reply(_msg(msg_type="gift")) is True

    def test_empty_content_rejected(self):
        assert self.engine._should_reply(_msg(content="")) is False

    def test_auto_reply_off_rejected(self):
        self.config.auto_reply = False
        assert self.engine._should_reply(_msg()) is False

    def test_blocked_word_rejected(self):
        self.config.blocked_words = ["bad"]
        assert self.engine._should_reply(_msg(content="this is bad")) is False

    def test_user_cooldown(self):
        self.engine._user_cooldowns["u1"] = time.time()
        self.engine._last_reply_time = time.time() - 20
        assert self.engine._should_reply(_msg(user_id="u1")) is False


class TestRuntimeStatus:
    def test_status_disabled_when_auto_reply_off(self):
        config = AIConfig(api_key="test-key", auto_reply=False, reply_interval=12)
        engine = AIReplyEngine(config)

        status = engine.describe_status()

        assert status["state"] == "disabled"
        assert status["short_text"] == "已关闭"
        assert status["interval_sec"] == 12

    def test_status_cooldown_reports_remaining_time(self):
        config = AIConfig(api_key="test-key", auto_reply=True, reply_interval=20)
        engine = AIReplyEngine(config)
        engine._last_reply_time = time.time() - 5

        status = engine.describe_status()

        assert status["state"] == "cooldown"
        assert status["short_text"] == "冷却中"
        assert status["next_ready_at"] is not None


# --- Conversation history ---

class TestConversationHistory:
    def setup_method(self):
        self.config = AIConfig(
            api_key="test-key",
            reply_interval=10,
        )
        self.engine = AIReplyEngine(self.config)

    @pytest.mark.asyncio
    async def test_history_trimmed_to_max(self):
        self.engine._client.chat = AsyncMock(return_value="reply")
        # Each call adds 1 user + 1 assistant = 2 entries
        # After trim (keeps last 4) + assistant append, max is 5
        for i in range(10):
            await self.engine._get_llm_reply("user", f"msg{i}")
        assert len(self.engine._conversation_history) <= 5

    @pytest.mark.asyncio
    async def test_history_includes_user_and_assistant(self):
        self.engine._client.chat = AsyncMock(return_value="hello back")
        await self.engine._get_llm_reply("user", "hello")
        roles = [h["role"] for h in self.engine._conversation_history]
        assert "user" in roles
        assert "assistant" in roles


# --- process_message integration ---

class TestProcessMessage:
    def setup_method(self):
        self.config = AIConfig(
            api_key="test-key",
            auto_reply=True,
            reply_interval=10,
        )
        self.engine = AIReplyEngine(self.config)

    @pytest.mark.asyncio
    async def test_returns_none_for_non_chat(self):
        result = await self.engine.process_message(_msg(msg_type="gift"))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_throttled(self):
        self.engine._last_reply_time = time.time()
        result = await self.engine.process_message(_msg())
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_reply_returned(self):
        self.engine._client.chat = AsyncMock(return_value="hey there")
        result = await self.engine.process_message(_msg())
        assert result is not None
        assert result.reply == "hey there"
        assert result.target_user == "user1"

    @pytest.mark.asyncio
    async def test_fallback_when_no_api_key(self):
        self.config.api_key = ""
        result = await self.engine.process_message(_msg(content="你好"))
        assert result is not None
        assert "你好" in result.reply or "user1" in result.reply

    @pytest.mark.asyncio
    async def test_throttle_updates_after_reply(self):
        self.engine._client.chat = AsyncMock(return_value="reply")
        await self.engine.process_message(_msg())
        assert self.engine._last_reply_time > 0

    @pytest.mark.asyncio
    async def test_pending_message_stored_on_throttle(self):
        self.engine._last_reply_time = time.time()
        await self.engine.process_message(_msg())
        assert self.engine._pending_message is not None

    @pytest.mark.asyncio
    async def test_deliver_reply_awaits_async_callback(self):
        self.engine._client.chat = AsyncMock(return_value="hey there")
        result = await self.engine.process_message(_msg())
        assert result is not None

        called = {"ok": False}

        async def _cb(reply_result):
            called["ok"] = True
            assert reply_result.reply == "hey there"

        self.engine._on_reply = _cb
        await self.engine._deliver_reply(result)
        assert called["ok"] is True


# --- RuleBasedFallback ---

class TestFallback:
    def setup_method(self):
        self.fb = RuleBasedFallback()

    def test_greeting(self):
        r = self.fb.reply("user", "你好")
        assert r is not None

    def test_short_message(self):
        r = self.fb.reply("user", "111")
        assert r is not None

    def test_unknown_returns_none(self):
        r = self.fb.reply("user", "这是一条非常普通的消息没什么关键词")
        assert r is None


class TestClientTimeout:
    @pytest.mark.asyncio
    async def test_chat_uses_reply_interval_capped_timeout(self):
        config = AIConfig(api_key="test-key", reply_interval=10)
        client = DeepSeekClient(config)
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp.raise_for_status.return_value = None
        mock_http.post = AsyncMock(return_value=mock_resp)
        client._client = mock_http

        await client.chat([{"role": "user", "content": "hello"}])

        assert mock_http.post.call_args.kwargs["timeout"] == 10

    @pytest.mark.asyncio
    async def test_chat_uses_configured_generation_params(self):
        config = AIConfig(
            api_key="test-key",
            reply_interval=10,
            max_tokens=180,
            temperature=1.25,
        )
        client = DeepSeekClient(config)
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp.raise_for_status.return_value = None
        mock_http.post = AsyncMock(return_value=mock_resp)
        client._client = mock_http

        await client.chat([{"role": "user", "content": "hello"}])

        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 180
        assert payload["temperature"] == 1.25


# --- Anti-repetition prompt ---

class TestAntiRepetition:
    def test_default_prompt_contains_no_repeat_instruction(self):
        from ai_reply import build_system_prompt
        prompt = build_system_prompt()
        assert "不要重复" in prompt


class TestManagedPrompt:
    def test_managed_prompt_includes_knowledge_blocks_and_limit(self):
        from ai_reply import build_system_prompt

        prompt = build_system_prompt(
            live_position="直播间名称：榴莲大魔王",
            product_info="1号链接是榴莲",
            shipping="发顺丰",
            talk_style="热情口语化",
            reply_char_limit=88,
        )

        assert "每条回复不超过 88 个中文字符" in prompt
        assert "直播间名称：榴莲大魔王" in prompt
        assert "1号链接是榴莲" in prompt
        assert "发顺丰" in prompt

    def test_managed_prompt_respects_intentionally_empty_blocks(self):
        from ai_reply import build_system_prompt

        prompt = build_system_prompt(
            live_position="水果直播间",
            product_info="",
            product_selling_points="",
            shipping="",
            campaign="",
            talk_style="",
            question_notes="",
            answer_examples="",
        )

        assert "水果直播间" in prompt
        assert "1号链接是榴莲" not in prompt
