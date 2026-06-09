from keyword_engine import KeywordEngine


def _make_engine(rule: dict) -> KeywordEngine:
    engine = KeywordEngine()
    engine.load_templates(
        {
            "keyword_templates": {
                "默认": {
                    "rules": [rule],
                },
            },
        }
    )
    engine.set_active("默认")
    return engine


def test_contains_match_splits_keywords_by_comma_and_dash():
    engine = _make_engine(
        {
            "keyword": "快递，售后,运费-破损，",
            "reply": "111",
            "match_mode": "contains",
        }
    )

    for text in ("快递多久到", "我要售后", "运费怎么算", "商品破损了"):
        result = engine.match(text)
        assert result.matched
        assert result.rule
        assert result.rule.reply == "111"

    assert not engine.match("价格多少").matched


def test_exact_match_splits_keywords_by_comma_and_dash():
    engine = _make_engine(
        {
            "keyword": "确认,收到-可以，",
            "reply": "111",
            "match_mode": "exact",
        }
    )

    assert engine.match("确认").matched
    assert engine.match("收到").matched
    assert engine.match("可以").matched
    assert not engine.match("确认一下").matched


def test_regex_match_keeps_keyword_as_one_pattern():
    engine = _make_engine(
        {
            "keyword": "快递，售后",
            "reply": "111",
            "match_mode": "regex",
        }
    )

    assert engine.match("快递，售后").matched
    assert not engine.match("快递").matched
