"""Keyword match engine for auto-reply.

Matches incoming danmaku against template rules, returns first matching rule.
Supports three match modes: exact (精确匹配), contains (包含匹配), regex (正则匹配).

Usage:
    engine = KeywordEngine()
    engine.load_templates(settings_dict)
    reply = engine.match("多少钱")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class KeywordRule:
    keyword: str
    reply: str
    generate_voice: bool = False
    match_mode: str = "contains"  # exact | contains | regex


@dataclass(slots=True)
class KeywordTemplate:
    name: str
    rules: list[KeywordRule] = field(default_factory=list)


@dataclass(slots=True)
class MatchResult:
    matched: bool
    rule: KeywordRule | None = None
    template_name: str = ""


class KeywordEngine:
    def __init__(self):
        self._templates: dict[str, KeywordTemplate] = {}
        self._active_template: str = ""

    def load_templates(self, data: dict | None):
        self._templates.clear()
        if not data:
            return
        templates = data.get("keyword_templates", {})
        for name, tmpl_data in templates.items():
            rules = [
                KeywordRule(
                    keyword=r.get("keyword", ""),
                    reply=r.get("reply", ""),
                    generate_voice=r.get("generate_voice", False),
                    match_mode=r.get("match_mode", "contains"),
                )
                for r in tmpl_data.get("rules", [])
            ]
            self._templates[name] = KeywordTemplate(name=name, rules=rules)

    def set_active(self, name: str):
        self._active_template = name

    def get_template_names(self) -> list[str]:
        return list(self._templates.keys())

    def get_template(self, name: str | None = None) -> KeywordTemplate | None:
        name = name or self._active_template
        return self._templates.get(name)

    def match(self, text: str, template_name: str | None = None) -> MatchResult:
        tmpl = self.get_template(template_name)
        if not tmpl:
            return MatchResult(False)
        for rule in tmpl.rules:
            if self._match_rule(text, rule):
                return MatchResult(True, rule, tmpl.name)
        return MatchResult(False)

    def _match_rule(self, text: str, rule: KeywordRule) -> bool:
        mode = rule.match_mode
        kw = rule.keyword
        if not kw or not text:
            return False
        if mode == "exact":
            return text.strip() == kw.strip()
        if mode == "regex":
            try:
                return re.search(kw, text) is not None
            except re.error:
                return False
        return kw in text  # contains (default)
