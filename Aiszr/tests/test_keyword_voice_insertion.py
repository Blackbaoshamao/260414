import asyncio

import pytest

from keyword_engine import KeywordRule, MatchResult
from ai_reply import ReplyResult
from voice_manager import VoiceActionResult


class FakeVoiceManager:
    def __init__(self, result=None, error=None):
        self.result = result or VoiceActionResult(True, "ok", output_path="keyword.wav")
        self.error = error
        self.calls = []

    async def synthesize_role_to_file(self, text, role, *, provider_name=""):
        self.calls.append((text, role, provider_name))
        if self.error:
            raise self.error
        return self.result


class FakePipeline:
    def __init__(self, accepted=True, error=None):
        self.accepted = accepted
        self.error = error
        self.calls = []

    def enqueue_insertion_audio(self, path, *, text=""):
        self.calls.append((path, text))
        if self.error:
            raise self.error
        return self.accepted


@pytest.fixture
def worker():
    from ui import CaptureWorker

    return CaptureWorker()


async def test_enqueue_keyword_voice_insertion_synthesizes_anchor_and_forwards_to_pipeline(
    worker, tmp_path
):
    wav_path = str(tmp_path / "keyword.wav")
    worker._voice_manager = FakeVoiceManager(
        VoiceActionResult(True, "ok", output_path=wav_path)
    )
    worker._digital_human_pipeline = FakePipeline()

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is True
    assert worker._voice_manager.calls == [("keyword reply", "anchor", "local_voice")]
    assert worker._digital_human_pipeline.calls == [(wav_path, "keyword reply")]


async def test_enqueue_keyword_voice_insertion_uses_configured_voice_provider(
    worker, tmp_path
):
    wav_path = str(tmp_path / "keyword.wav")
    worker._voice_manager = FakeVoiceManager(
        VoiceActionResult(True, "ok", output_path=wav_path)
    )
    worker._digital_human_pipeline = FakePipeline()
    worker.set_keyword_reply_config(
        enabled=True,
        templates={},
        voice_provider="aliyun_bailian",
    )

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is True
    assert worker._voice_manager.calls == [
        ("keyword reply", "anchor", "aliyun_bailian")
    ]


async def test_enqueue_keyword_voice_insertion_returns_false_for_empty_reply(worker):
    worker._voice_manager = FakeVoiceManager()
    worker._digital_human_pipeline = FakePipeline()

    result = await worker._enqueue_keyword_voice_insertion("   ")

    assert result is False
    assert worker._voice_manager.calls == []
    assert worker._digital_human_pipeline.calls == []


async def test_enqueue_keyword_voice_insertion_returns_false_without_pipeline(worker):
    worker._voice_manager = FakeVoiceManager()
    worker._digital_human_pipeline = None

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is False
    assert worker._voice_manager.calls == []


@pytest.mark.parametrize(
    "synthesis_result",
    [
        VoiceActionResult(False, "failed", output_path="keyword.wav"),
        VoiceActionResult(True, "missing path", output_path=""),
    ],
)
async def test_enqueue_keyword_voice_insertion_returns_false_when_synthesis_fails(
    worker, synthesis_result
):
    worker._voice_manager = FakeVoiceManager(synthesis_result)
    worker._digital_human_pipeline = FakePipeline()

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is False
    assert worker._voice_manager.calls == [("keyword reply", "anchor", "local_voice")]
    assert worker._digital_human_pipeline.calls == []


async def test_enqueue_keyword_voice_insertion_returns_false_when_synthesis_raises(worker):
    worker._voice_manager = FakeVoiceManager(error=RuntimeError("boom"))
    worker._digital_human_pipeline = FakePipeline()

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is False
    assert worker._digital_human_pipeline.calls == []


async def test_enqueue_keyword_voice_insertion_returns_false_when_pipeline_rejects(worker):
    worker._voice_manager = FakeVoiceManager(
        VoiceActionResult(True, "ok", output_path="keyword.wav")
    )
    worker._digital_human_pipeline = FakePipeline(accepted=False)

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is False
    assert worker._digital_human_pipeline.calls == [("keyword.wav", "keyword reply")]


async def test_enqueue_keyword_voice_insertion_returns_false_when_pipeline_raises(worker):
    worker._voice_manager = FakeVoiceManager(
        VoiceActionResult(True, "ok", output_path="keyword.wav")
    )
    worker._digital_human_pipeline = FakePipeline(error=RuntimeError("full"))

    result = await worker._enqueue_keyword_voice_insertion("keyword reply")

    assert result is False
    assert worker._digital_human_pipeline.calls == [("keyword.wav", "keyword reply")]


async def test_dispatch_keyword_reply_starts_voice_without_blocking_comment_and_signal(worker):
    events = []
    voice_started = asyncio.Event()
    release_voice = asyncio.Event()

    async def enqueue_voice(reply):
        voice_started.set()
        await release_voice.wait()
        events.append(("voice_done", reply))
        return True

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    worker._enqueue_keyword_voice_insertion = enqueue_voice
    worker._wechat = FakeWechat()
    worker.keyword_reply_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    await asyncio.wait_for(
        worker._dispatch_keyword_reply(
            "deal", "keyword reply", "buyer", generate_voice=True
        ),
        timeout=0.05,
    )

    await asyncio.wait_for(voice_started.wait(), timeout=0.1)
    assert events == [
        ("signal", ("deal", "keyword reply", "buyer", False)),
    ]
    release_voice.set()
    for _ in range(20):
        if ("voice_done", "keyword reply") in events:
            break
        await asyncio.sleep(0.01)
    assert ("voice_done", "keyword reply") in events


async def test_dispatch_keyword_reply_sends_comment_when_generate_voice_false(worker):
    events = []

    async def fail_enqueue_voice(reply):
        raise AssertionError("voice insertion should not be attempted")

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    worker._enqueue_keyword_voice_insertion = fail_enqueue_voice
    worker._wechat = FakeWechat()
    worker.keyword_reply_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    await worker._dispatch_keyword_reply(
        "deal", "keyword reply", "buyer", generate_voice=False
    )

    assert events == [
        ("comment", "@buyer keyword reply"),
        ("signal", ("deal", "keyword reply", "buyer", True)),
    ]


async def test_dispatch_keyword_reply_skips_voice_when_generate_voice_false(worker):
    events = []

    async def fail_enqueue_voice(reply):
        raise AssertionError("voice insertion should not be attempted")

    worker._enqueue_keyword_voice_insertion = fail_enqueue_voice
    worker._wechat = None
    worker.keyword_reply_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    await worker._dispatch_keyword_reply(
        "deal", "keyword reply", "buyer", generate_voice=False
    )

    assert events == [("signal", ("deal", "keyword reply", "buyer", False))]


def test_next_scheduled_script_rotates_in_order(worker):
    worker.set_scheduled_script_config(
        enabled=True,
        scripts=["第一段", "第二段"],
        interval_sec=30,
        random_order=False,
    )

    assert worker._next_scheduled_script() == "第一段"
    assert worker._next_scheduled_script() == "第二段"
    assert worker._next_scheduled_script() == "第一段"


async def test_dispatch_scheduled_script_sends_wechat_text_without_mention(worker):
    events = []

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    worker._wechat = FakeWechat()
    worker.set_scheduled_script_config(
        enabled=True,
        scripts=["欢迎来到直播间"],
        interval_sec=30,
        voice_enabled=False,
    )
    worker.scheduled_script_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    delivered = await worker._dispatch_scheduled_script()

    assert delivered is True
    assert events == [
        ("comment", "欢迎来到直播间"),
        ("signal", ("欢迎来到直播间", True)),
    ]


async def test_dispatch_scheduled_script_inserts_random_space_when_enabled(worker, monkeypatch):
    events = []

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    monkeypatch.setattr("ui.random.choice", lambda _choices: 2)
    worker._wechat = FakeWechat()
    worker.set_scheduled_script_config(
        enabled=True,
        scripts=["欢迎来到直播间"],
        interval_sec=30,
        random_space_enabled=True,
        voice_enabled=False,
    )
    worker.scheduled_script_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    delivered = await worker._dispatch_scheduled_script()

    assert delivered is True
    assert worker._scheduled_scripts == ["欢迎来到直播间"]
    assert events == [
        ("comment", "欢迎 来到直播间"),
        ("signal", ("欢迎 来到直播间", True)),
    ]


async def test_dispatch_scheduled_script_voice_skips_wechat_text(worker):
    events = []

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    async def enqueue_voice(reply):
        events.append(("voice", reply))
        return True

    worker._wechat = FakeWechat()
    worker._enqueue_keyword_voice_insertion = enqueue_voice
    worker.set_scheduled_script_config(
        enabled=True,
        scripts=["主播正在讲一号链接"],
        interval_sec=30,
        voice_enabled=True,
    )
    worker.scheduled_script_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    delivered = await worker._dispatch_scheduled_script()

    assert delivered is True
    assert events == [
        ("voice", "主播正在讲一号链接"),
        ("signal", ("主播正在讲一号链接", True)),
    ]


async def test_dispatch_scheduled_script_reports_not_delivered_without_channel(worker):
    events = []
    worker._wechat = None
    worker.set_scheduled_script_config(
        enabled=True,
        scripts=["欢迎来到直播间"],
        interval_sec=30,
        voice_enabled=False,
    )
    worker.scheduled_script_fired.connect(
        lambda *args: events.append(("signal", args))
    )

    delivered = await worker._dispatch_scheduled_script()

    assert delivered is False
    assert events == [("signal", ("欢迎来到直播间", False))]


async def test_dispatch_ai_reply_sends_wechat_text_with_mention(worker):
    events = []

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    worker._wechat = FakeWechat()
    worker.ai_reply_ready.connect(
        lambda *args: events.append(("signal", args))
    )

    await worker._dispatch_ai_reply(
        ReplyResult(
            target_user="buyer",
            target_msg="多少钱",
            reply="看一号链接",
            platform="wechat",
            use_voice=False,
            mention_user=False,
        )
    )

    assert events == [
        ("signal", ("buyer", "多少钱", "看一号链接")),
        ("comment", "@buyer 看一号链接"),
    ]


async def test_dispatch_ai_reply_voice_does_not_send_wechat_text(worker):
    events = []

    class FakeWechat:
        async def send_comment(self, text):
            events.append(("comment", text))
            return True

    class FakeTtsWorker:
        def enqueue(self, payload, *, interrupt=False):
            events.append(("tts", payload["text"], interrupt))

    worker._wechat = FakeWechat()
    worker._tts_worker = FakeTtsWorker()
    worker.ai_reply_ready.connect(
        lambda *args: events.append(("signal", args))
    )

    await worker._dispatch_ai_reply(
        ReplyResult(
            target_user="buyer",
            target_msg="多少钱",
            reply="看一号链接",
            platform="wechat",
            use_voice=True,
            mention_user=True,
        )
    )

    assert events == [
        ("signal", ("buyer", "多少钱", "看一号链接")),
        ("tts", "看一号链接", False),
    ]


def test_clear_live_session_memory_delegates_to_ai_engine(worker):
    calls = []

    class FakeAIEngine:
        def clear_memory(self):
            calls.append("clear")

    worker._ai_engine = FakeAIEngine()

    worker._clear_live_session_memory()

    assert calls == ["clear"]


async def test_on_message_passes_rule_generate_voice_to_dispatch_for_any_platform(worker):
    calls = []

    class FakeEngine:
        def match(self, text):
            assert text == "need deal"
            return MatchResult(
                True,
                KeywordRule(
                    keyword="deal",
                    reply="keyword reply",
                    generate_voice=True,
                ),
                "active",
            )

    class FakeReplayLogger:
        def log_event(self, event):
            pass

    class FakeTruthStream:
        def ingest(self, msg):
            return []

    async def fake_dispatch(keyword, reply, nickname, generate_voice=False):
        calls.append((keyword, reply, nickname, generate_voice))

    worker._wechat = None
    worker._keyword_auto_reply_enabled = True
    worker._keyword_engine = FakeEngine()
    worker._keyword_global_cooldown_sec = 0
    worker._keyword_rate_limit_per_min = 20
    worker._keyword_last_hit = {}
    worker._keyword_hit_log.clear()
    worker._replay_logger = FakeReplayLogger()
    worker._truth_stream = FakeTruthStream()
    worker._dispatch_keyword_reply = fake_dispatch

    await worker._on_message(
        {"type": "chat", "content": "need deal", "nickname": "buyer"}
    )
    await asyncio.sleep(0)

    assert calls == [("deal", "keyword reply", "buyer", True)]


async def test_on_message_non_wechat_text_only_keyword_falls_through_to_ai(worker):
    dispatch_calls = []
    ai_calls = []

    class FakeEngine:
        def match(self, text):
            assert text == "need deal"
            return MatchResult(
                True,
                KeywordRule(
                    keyword="deal",
                    reply="keyword reply",
                    generate_voice=False,
                ),
                "active",
            )

    class FakeAIEngine:
        async def process_message(self, msg):
            ai_calls.append(msg.copy())
            return None

    class FakeReplayLogger:
        def log_event(self, event):
            pass

    class FakeTruthStream:
        def ingest(self, msg):
            return []

    async def fake_dispatch(*args, **kwargs):
        dispatch_calls.append((args, kwargs))

    worker._wechat = None
    worker._keyword_auto_reply_enabled = True
    worker._keyword_engine = FakeEngine()
    worker._keyword_global_cooldown_sec = 0
    worker._keyword_rate_limit_per_min = 20
    worker._keyword_last_hit = {}
    worker._keyword_hit_log.clear()
    worker._replay_logger = FakeReplayLogger()
    worker._truth_stream = FakeTruthStream()
    worker._dispatch_keyword_reply = fake_dispatch
    worker._ai_engine = FakeAIEngine()

    await worker._on_message(
        {"type": "chat", "content": "need deal", "nickname": "buyer"}
    )
    await asyncio.sleep(0)

    assert dispatch_calls == []
    assert len(ai_calls) == 1
