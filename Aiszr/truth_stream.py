"""Truth stream processor: normalize, dedupe, short-window reorder."""

from __future__ import annotations

import heapq
import time
import uuid
from dataclasses import dataclass, field


@dataclass(order=True)
class _BufferedEvent:
    ts_ms: int
    idx: int
    event: dict = field(compare=False)


class TruthStreamProcessor:
    """Normalize upstream messages into stable truth events."""

    def __init__(
        self,
        room_id: str = "",
        dedupe_window_ms: int = 700,
        reorder_window_ms: int = 600,
        max_buffer: int = 2048,
    ):
        self._room_id = room_id
        self._dedupe_window_ms = dedupe_window_ms
        self._reorder_window_ms = reorder_window_ms
        self._max_buffer = max_buffer
        self._buf: list[_BufferedEvent] = []
        self._seen: dict[str, int] = {}
        self._seq = 0

    def set_room_id(self, room_id: str) -> None:
        self._room_id = room_id or self._room_id

    @staticmethod
    def _ts_ms_from_message(msg: dict) -> int:
        ts = msg.get("timestamp")
        if isinstance(ts, (float, int)):
            if ts > 10_000_000_000:
                return int(ts)
            return int(float(ts) * 1000)
        return int(time.time() * 1000)

    @staticmethod
    def _dedupe_key(msg: dict, room_id: str) -> str:
        msg_type = str(msg.get("type", ""))
        nickname = str(msg.get("nickname", "")).strip().lower()
        content = str(msg.get("content", "")).strip().lower()
        gift = str(msg.get("gift_name", "")).strip().lower()
        gift_count = str(msg.get("gift_count", ""))
        return "|".join([room_id, msg_type, nickname, content, gift, gift_count])

    @staticmethod
    def _confidence(msg: dict, source: str) -> float:
        msg_type = msg.get("type", "")
        user_id = str(msg.get("user_id", "")).strip()
        nickname = str(msg.get("nickname", "")).strip()
        fallback = bool(msg.get("_fallback", False))
        if source == "dom" and msg_type == "chat":
            return 1.0
        conf = 0.92 if (user_id and nickname) else 0.72
        if fallback:
            conf -= 0.25
        return max(0.1, min(1.0, conf))

    def _normalize(self, msg: dict, source: str) -> dict:
        ts_ms = self._ts_ms_from_message(msg)
        evt = dict(msg)
        evt["event_id"] = evt.get("event_id") or uuid.uuid4().hex
        evt["room_id"] = self._room_id
        evt["source"] = source
        evt["confidence"] = self._confidence(evt, source)
        evt["ts_ms"] = ts_ms
        return evt

    def ingest(self, msg: dict) -> list[dict]:
        source = str(msg.get("_source") or "ws")
        event = self._normalize(msg, source=source)
        key = self._dedupe_key(event, event.get("room_id", ""))
        ts_ms = int(event["ts_ms"])
        last = self._seen.get(key)
        if last is not None and (ts_ms - last) <= self._dedupe_window_ms:
            return []
        self._seen[key] = ts_ms
        if len(self._seen) > self._max_buffer:
            cutoff = ts_ms - self._dedupe_window_ms
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        self._seq += 1
        heapq.heappush(self._buf, _BufferedEvent(ts_ms=ts_ms, idx=self._seq, event=event))
        return self._drain_ready()

    def _drain_ready(self) -> list[dict]:
        now_ms = int(time.time() * 1000)
        watermark = now_ms - self._reorder_window_ms
        out: list[dict] = []
        while self._buf and (self._buf[0].ts_ms <= watermark or len(self._buf) > self._max_buffer):
            out.append(heapq.heappop(self._buf).event)
        return out

    def drain_ready(self) -> list[dict]:
        """Drain events that are older than reorder window."""
        return self._drain_ready()

    def flush_all(self) -> list[dict]:
        out = [heapq.heappop(self._buf).event for _ in range(len(self._buf))]
        return out
