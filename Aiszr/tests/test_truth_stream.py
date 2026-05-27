import time

from truth_stream import TruthStreamProcessor


def _msg(content: str, ts: float, source: str = "dom") -> dict:
    return {
        "type": "chat",
        "user_id": "u1",
        "nickname": "Tester",
        "content": content,
        "timestamp": ts,
        "time": "2026-01-01T00:00:00",
        "_source": source,
    }


def test_truth_event_shape_contains_required_fields():
    p = TruthStreamProcessor(room_id="r1", dedupe_window_ms=10, reorder_window_ms=0)
    out = p.ingest(_msg("hello", time.time()))
    assert len(out) == 1
    evt = out[0]
    for key in ("event_id", "room_id", "type", "nickname", "content", "source", "confidence", "ts_ms"):
        assert key in evt
    assert evt["room_id"] == "r1"
    assert evt["source"] == "dom"


def test_dedupe_within_window():
    now = time.time()
    p = TruthStreamProcessor(room_id="r1", dedupe_window_ms=800, reorder_window_ms=0)
    first = p.ingest(_msg("same", now))
    second = p.ingest(_msg("same", now + 0.2))
    assert len(first) == 1
    assert second == []


def test_short_window_reorder_by_timestamp():
    now = time.time()
    p = TruthStreamProcessor(room_id="r1", dedupe_window_ms=10, reorder_window_ms=5000)

    # Add newer first, older second.
    p.ingest(_msg("newer", now + 1))
    p.ingest(_msg("older", now))
    out = p.flush_all()

    assert [x["content"] for x in out] == ["older", "newer"]
