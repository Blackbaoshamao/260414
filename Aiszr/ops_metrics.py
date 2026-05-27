"""Thread-safe operational metrics for ingest/reply/live pipeline."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Deque


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(0.95 * (len(ordered) - 1))
    return ordered[idx]


@dataclass
class LossCounters:
    decode_fail: int = 0
    fallback_drop: int = 0
    queue_drop: int = 0
    render_drop: int = 0

    def to_dict(self) -> dict:
        return {
            "decode_fail": self.decode_fail,
            "fallback_drop": self.fallback_drop,
            "queue_drop": self.queue_drop,
            "render_drop": self.render_drop,
        }


@dataclass
class OpsMetrics:
    """Central metrics store shared across worker components."""

    qps_window_sec: int = 30
    _loss: LossCounters = field(default_factory=LossCounters)
    _input_ts: Deque[float] = field(default_factory=deque)
    _reply_ts: Deque[float] = field(default_factory=deque)
    _latency_ms: Deque[float] = field(default_factory=deque)
    _errors: int = 0
    _reply_total: int = 0
    _lock: Lock = field(default_factory=Lock)

    def _trim(self, now: float) -> None:
        deadline = now - self.qps_window_sec
        while self._input_ts and self._input_ts[0] < deadline:
            self._input_ts.popleft()
        while self._reply_ts and self._reply_ts[0] < deadline:
            self._reply_ts.popleft()
        # Keep latency history bounded to avoid unbounded memory growth.
        while len(self._latency_ms) > 2048:
            self._latency_ms.popleft()

    def record_input(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._trim(now)
            self._input_ts.append(now)

    def record_reply(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._trim(now)
            self._reply_ts.append(now)
            self._reply_total += 1

    def observe_latency_ms(self, value: float) -> None:
        with self._lock:
            self._latency_ms.append(max(0.0, float(value)))

    def inc_error(self, amount: int = 1) -> None:
        with self._lock:
            self._errors += max(0, int(amount))

    def inc_loss(self, metric: str, amount: int = 1) -> None:
        delta = max(0, int(amount))
        with self._lock:
            if metric == "decode_fail":
                self._loss.decode_fail += delta
            elif metric == "fallback_drop":
                self._loss.fallback_drop += delta
            elif metric == "queue_drop":
                self._loss.queue_drop += delta
            elif metric == "render_drop":
                self._loss.render_drop += delta

    def merge_loss(self, values: dict[str, int]) -> None:
        for key in ("decode_fail", "fallback_drop", "queue_drop", "render_drop"):
            self.inc_loss(key, int(values.get(key, 0)))

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            self._trim(now)
            input_qps = len(self._input_ts) / max(1, self.qps_window_sec)
            reply_qps = len(self._reply_ts) / max(1, self.qps_window_sec)
            p95 = _p95(list(self._latency_ms))
            return {
                "window_sec": self.qps_window_sec,
                "input_qps": round(input_qps, 3),
                "reply_qps": round(reply_qps, 3),
                "p95_latency_ms": round(p95, 2),
                "errors": self._errors,
                "reply_total": self._reply_total,
                "loss": self._loss.to_dict(),
            }
