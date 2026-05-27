"""JSONL replay logger for event/reply decision tracing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


class ReplayLogger:
    def __init__(self, path: str | Path = "replay_log.jsonl", max_bytes: int = 10_485_760):
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _rotate_if_needed(self) -> None:
        try:
            if self._path.stat().st_size < self._max_bytes:
                return
        except OSError:
            return
        backup = self._path.with_suffix(self._path.suffix + ".1")
        backup.unlink(missing_ok=True)
        self._path.rename(backup)

    def _append(self, item: dict) -> None:
        payload = dict(item)
        payload["_logged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self._rotate_if_needed()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_event(self, event: dict) -> None:
        self._append({"kind": "event", **event})

    def log_reply(self, reply: dict) -> None:
        self._append({"kind": "reply", **reply})

    def log_tts(self, speech: dict) -> None:
        self._append({"kind": "tts", **speech})
