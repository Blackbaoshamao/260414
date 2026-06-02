import asyncio

import pytest

from keyword_engine import KeywordRule, MatchResult
from voice_manager import VoiceActionResult


class FakeVoiceManager:
    def __init__(self, result=None, error=None):
        self.result = result or VoiceActionResult(True, "ok", output_path="keyword.wav")
        self.error = error
        self.calls = []

    async def synthesize_role_to_file(self, text, role):
        self.calls.append((text, role))
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
    assert worker._voice_manager.calls == [("keyword reply", "anchor")]
    assert worker._digital_human_pipeline.calls == [(wav_path, "keyword reply")]


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
    assert worker._voice_manager.calls == [("keyword reply", "anchor")]
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


async def test_dispatch_keyword_reply_attempts_voice_before_comment_and_signal(worker):
    events = []

    async def enqueue_voice(reply):
        events.append(("voice", reply))
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

    await worker._dispatch_keyword_reply(
        "deal", "keyword reply", "buyer", 2, generate_voice=True
    )

    assert events == [
        ("voice", "keyword reply"),
        ("comment", "keyword reply"),
        ("signal", ("deal", "keyword reply", "buyer", 2, True)),
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
        "deal", "keyword reply", "buyer", 2, generate_voice=False
    )

    assert events == [("signal", ("deal", "keyword reply", "buyer", 2, False))]


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

    async def fake_dispatch(keyword, reply, nickname, count, generate_voice=False):
        calls.append((keyword, reply, nickname, count, generate_voice))

    worker._wechat = None
    worker._keyword_auto_reply_enabled = True
    worker._keyword_engine = FakeEngine()
    worker._keyword_global_cooldown_sec = 0
    worker._keyword_rate_limit_per_min = 20
    worker._keyword_last_hit = {}
    worker._keyword_hit_log.clear()
    worker._keyword_hit_count = {}
    worker._replay_logger = FakeReplayLogger()
    worker._truth_stream = FakeTruthStream()
    worker._dispatch_keyword_reply = fake_dispatch

    await worker._on_message(
        {"type": "chat", "content": "need deal", "nickname": "buyer"}
    )
    await asyncio.sleep(0)

    assert calls == [("deal", "keyword reply", "buyer", 1, True)]
