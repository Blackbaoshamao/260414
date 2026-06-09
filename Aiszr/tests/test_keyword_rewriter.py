import pytest

import keyword_rewriter
from keyword_rewriter import (
    KeywordRewriteError,
    expand_keyword_reply_template,
    rewrite_keyword_reply,
)


class FakeDeepSeekClient:
    instances = []

    def __init__(self, config):
        self.config = config
        self.messages = None
        self.closed = False
        FakeDeepSeekClient.instances.append(self)

    async def chat(self, messages):
        self.messages = messages
        return (
            "我们支持七天无理由，配有运费险，退货包运费，放心拍放心选购哦\n"
            "我们有七天无理由，自带运费险，退货全包运费，放心拍放心带走哦\n"
            "我们享有七天无理由，含运费险，退货承担运费，放心拍放心入手哦\n\n"
            "我们[支持,有,享有]七天无理由，[配有,自带,含]运费险，退货[包,承担,全包,]运费，放心拍放心[带走,入手,选购,]哦"
        )

    async def close(self):
        self.closed = True


class TruncatedDeepSeekClient(FakeDeepSeekClient):
    async def chat(self, messages):
        self.messages = messages
        return "我们[支持,有]七天无理由，[配有,自带"


@pytest.mark.asyncio
async def test_rewrite_keyword_reply_uses_deepseek_and_cleans_result(monkeypatch):
    FakeDeepSeekClient.instances.clear()
    monkeypatch.setattr(keyword_rewriter, "DeepSeekClient", FakeDeepSeekClient)

    result = await rewrite_keyword_reply(
        "我们支持七天无理由，配有运费险，退货包运费，放心拍放心选购哦",
        {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
    )

    assert result == (
        "我们[支持,有,享有]七天无理由，[配有,自带,含]运费险，"
        "退货[包,承担,全包,]运费，放心拍放心[带走,入手,选购,]哦"
    )
    assert FakeDeepSeekClient.instances[0].closed is True
    assert FakeDeepSeekClient.instances[0].config.max_tokens >= 480
    assert "前3行分别输出3条微改写示例" in FakeDeepSeekClient.instances[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_rewrite_keyword_reply_requires_api_key():
    with pytest.raises(KeywordRewriteError, match="DeepSeek API Key"):
        await rewrite_keyword_reply("支持七天无理由", {})


@pytest.mark.asyncio
async def test_rewrite_keyword_reply_rejects_incomplete_template(monkeypatch):
    FakeDeepSeekClient.instances.clear()
    monkeypatch.setattr(keyword_rewriter, "DeepSeekClient", TruncatedDeepSeekClient)

    with pytest.raises(KeywordRewriteError, match="不完整"):
        await rewrite_keyword_reply(
            "我们支持七天无理由，配有运费险，退货包运费，放心拍放心选购哦",
            {
                "api_key": "test-key",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
            },
        )


@pytest.mark.asyncio
async def test_rewrite_keyword_reply_expands_existing_template_before_rewriting(monkeypatch):
    FakeDeepSeekClient.instances.clear()
    monkeypatch.setattr(keyword_rewriter, "DeepSeekClient", FakeDeepSeekClient)

    await rewrite_keyword_reply(
        "我们[支持,有,享有]七天无理由，[配有,自带,含]运费险",
        {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
    )

    user_prompt = FakeDeepSeekClient.instances[0].messages[1]["content"]
    assert "[" not in user_prompt
    assert "]" not in user_prompt
    assert "七天无理由" in user_prompt


def test_expand_keyword_reply_template_selects_one_option_per_group():
    class PickLast:
        def choice(self, options):
            return options[-1]

    result = expand_keyword_reply_template(
        "我们[支持,有,享有]七天无理由，退货【包,承担,全包,】运费",
        rng=PickLast(),
    )

    assert result == "我们享有七天无理由，退货全包运费"
