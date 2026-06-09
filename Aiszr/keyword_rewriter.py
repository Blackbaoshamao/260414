"""Keyword reply micro-rewrite helper backed by the configured DeepSeek API."""

from __future__ import annotations

import re
import random
from difflib import SequenceMatcher

from ai_reply import AIConfig, DeepSeekClient


DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class KeywordRewriteError(RuntimeError):
    pass


_VARIANT_GROUP_RE = re.compile(r"(\[[^\[\]]+\]|【[^【】]+】)")


def _variant_groups_balanced(text: str) -> bool:
    expected_closers = {"[": "]", "【": "】"}
    closers = set(expected_closers.values())
    stack: list[str] = []
    for char in str(text or ""):
        if char in expected_closers:
            stack.append(expected_closers[char])
        elif char in closers:
            if not stack or stack[-1] != char:
                return False
            stack.pop()
    return not stack


def _clean_rewrite_response(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^```(?:text|txt)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    cleaned = []
    for line in lines:
        line = re.sub(r"^\s*(?:\d+[\.、)]|[-*])\s*", "", line).strip()
        line = re.sub(r"^(改写|结果|回复|文案|模板)\s*[:：]\s*", "", line).strip()
        line = line.strip("“”\"'")
        if line:
            cleaned.append(line)
    for line in reversed(cleaned):
        if _VARIANT_GROUP_RE.search(line) and _variant_groups_balanced(line):
            return line
    return cleaned[-1] if cleaned else ""


def _template_surface_text(text: str) -> str:
    def replace(match: re.Match) -> str:
        group = match.group(0)[1:-1]
        options = _split_variant_options(group)
        return options[0] if options else ""

    return _VARIANT_GROUP_RE.sub(replace, str(text or ""))


def _split_variant_options(group: str) -> list[str]:
    return [
        option.strip()
        for option in re.split(r"[，,、]", str(group or ""))
        if option.strip()
    ]


def expand_keyword_reply_template(text: str, rng: object | None = None) -> str:
    chooser = getattr(rng, "choice", None) if rng is not None else random.choice

    def replace(match: re.Match) -> str:
        group = match.group(0)[1:-1]
        options = _split_variant_options(group)
        if not options:
            return match.group(0)
        return chooser(options)

    return _VARIANT_GROUP_RE.sub(replace, str(text or ""))


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _rewrite_similarity(original: str, rewritten: str) -> float:
    left = _compact_text(original)
    right = _compact_text(rewritten)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _rewrite_drift_too_large(original: str, rewritten: str) -> bool:
    left = _compact_text(original)
    right = _compact_text(_template_surface_text(rewritten))
    if not left or not right:
        return True
    if len(right) > len(left) * 1.45 or len(right) < len(left) * 0.55:
        return True
    return _rewrite_similarity(left, right) < 0.55


async def rewrite_keyword_reply(text: str, settings: dict | None) -> str:
    original = str(text or "").strip()
    if not original:
        raise KeywordRewriteError("回复话术为空")
    original_is_template = _VARIANT_GROUP_RE.search(original) is not None
    source_text = expand_keyword_reply_template(original) if original_is_template else original

    settings = settings or {}
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key:
        raise KeywordRewriteError("请先在 AI 设置中配置 DeepSeek API Key")

    config = AIConfig(
        api_key=api_key,
        base_url=str(settings.get("base_url") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        model=str(settings.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        max_tokens=max(480, min(1200, len(source_text) * 10)),
        temperature=0.85,
    )
    client = DeepSeekClient(config)
    try:
        result = await client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是直播带货关键词回复的话术微改写助手。"
                        "只输出四行中文，不要标题，不要解释，不要编号。"
                        "前3行分别输出3条微改写示例。"
                        "第4行输出可随机组合的模板句，把可替换词写成[原词,替换词1,替换词2]。"
                        "必须保持原句事实、数字、承诺、活动、售后政策、运费政策完全不变。"
                        "只替换少量可替换词、语气词或轻微调整短语顺序。"
                        "改写幅度不要超过20%，整体相似度保持在80%以上。"
                        "不要新增优惠、不要删减关键信息、不要改变语气用途。"
                        "第4行模板必须能直接作为一句话朗读，除了可替换词组外不要出现其它说明。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请按固定格式输出3条微改写示例，并在最后一行输出可随机组合模板：\n"
                        f"{source_text}"
                    ),
                },
            ]
        )
    finally:
        await client.close()

    rewritten = _clean_rewrite_response(result or "")
    if not rewritten:
        raise KeywordRewriteError("DeepSeek 未返回有效改写")
    if not _variant_groups_balanced(rewritten):
        raise KeywordRewriteError("DeepSeek 返回的随机模板不完整，请重试")
    if not _VARIANT_GROUP_RE.search(rewritten):
        raise KeywordRewriteError("DeepSeek 未返回可随机组合模板")
    if rewritten == original and not original_is_template:
        raise KeywordRewriteError("改写结果与原文相同")
    if original_is_template:
        drift_source = _template_surface_text(original)
        if _rewrite_similarity(drift_source, _template_surface_text(rewritten)) < 0.45:
            raise KeywordRewriteError("改写变化过大，已保留原文")
    elif _rewrite_drift_too_large(source_text, rewritten):
        raise KeywordRewriteError("改写变化过大，已保留原文")
    return rewritten
