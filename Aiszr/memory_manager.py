"""User long-term memory for Aiszr.

Persists per-user chat history as JSONL files under data/user_history/.
Each user gets a directory named by sha256(nickname + user_id).

Public API:
  - load_recent_history(nickname, user_id, limit) -> list[dict]
  - save_interaction(nickname, user_id, user_msg, ai_reply)
"""

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

from loguru import logger

from app_paths import app_dir

DATA_ROOT = app_dir() / "data" / "user_history"
MAX_HISTORY_LEN = 5


def _user_key(nickname: str, user_id: str) -> str:
    raw = f"{nickname or ''}:{user_id or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class MemoryManager:
    def __init__(self, data_root: Path = DATA_ROOT, room_id: str = ""):
        self._root = Path(data_root)
        self._room_id = room_id or ""
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _user_dir(self, nickname: str, user_id: str) -> Path:
        key = _user_key(nickname, user_id)
        if self._room_id:
            return self._root / self._room_id / key
        return self._root / key

    async def save_interaction(
        self,
        nickname: str,
        user_id: str,
        user_msg: str,
        ai_reply: str,
    ) -> None:
        key = _user_key(nickname, user_id)
        lock = self._locks[key]
        async with lock:
            try:
                await asyncio.to_thread(
                    self._write_sync, nickname, user_id, user_msg, ai_reply,
                )
            except Exception as e:
                logger.warning("Memory save failed for {}: {}", nickname, e)

    def _write_sync(
        self,
        nickname: str,
        user_id: str,
        user_msg: str,
        ai_reply: str,
    ) -> None:
        udir = self._user_dir(nickname, user_id)
        udir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        profile = {
            "nickname": nickname,
            "user_id": user_id or "",
            "last_interaction": now,
        }
        (udir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8",
        )

        entry = {
            "timestamp": now,
            "user_msg": user_msg,
            "ai_reply": ai_reply,
        }
        with open(udir / "chat_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def load_recent_history(
        self,
        nickname: str,
        user_id: str,
        limit: int = MAX_HISTORY_LEN,
    ) -> list[dict]:
        try:
            return await asyncio.to_thread(
                self._read_sync, nickname, user_id, limit,
            )
        except Exception as e:
            logger.warning("Memory load failed for {}: {}", nickname, e)
            return []

    def _read_sync(
        self,
        nickname: str,
        user_id: str,
        limit: int,
    ) -> list[dict]:
        log_path = self._user_dir(nickname, user_id) / "chat_log.jsonl"
        if not log_path.exists():
            return []

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        recent = lines[-limit:] if lines else []
        results = []
        for line in recent:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results

    def format_history_for_prompt(self, history: list[dict]) -> str:
        if not history:
            return ""
        parts = ["### 历史对话摘要 ###"]
        for h in history:
            user_msg = h.get("user_msg", "")
            ai_reply = h.get("ai_reply", "")
            parts.append(f"观众: {user_msg}")
            parts.append(f"你: {ai_reply}")
        return "\n".join(parts)
