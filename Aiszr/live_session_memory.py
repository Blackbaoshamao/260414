"""Ephemeral user memory for the current live session."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app_paths import app_dir


DATA_ROOT = app_dir() / "data"
DEFAULT_DB_PATH = DATA_ROOT / "live_session_memory.sqlite3"


@dataclass(slots=True)
class LiveUserMemory:
    user_key: str
    username: str
    platform: str
    first_seen: float
    last_seen: float
    last_message: str
    message_count: int
    last_reply: str = ""
    preference_notes: str = ""
    welcomed: bool = False
    explained_topics: list[str] | None = None

    @property
    def is_first_message(self) -> bool:
        return self.message_count <= 1


def build_user_key(username: str, platform: str, user_id: str = "") -> str:
    platform = str(platform or "unknown").strip() or "unknown"
    identity = str(user_id or username or "anonymous").strip() or "anonymous"
    return f"{platform}:{identity}"


class LiveSessionMemory:
    """SQLite store that is cleared when the current live session ends."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, session_id: str = "current"):
        self._db_path = Path(db_path)
        self._session_id = str(session_id or "current")
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_user_memory (
                    session_id TEXT NOT NULL,
                    user_key TEXT NOT NULL,
                    username TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    last_message TEXT NOT NULL DEFAULT '',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    last_reply TEXT NOT NULL DEFAULT '',
                    preference_notes TEXT NOT NULL DEFAULT '',
                    welcomed INTEGER NOT NULL DEFAULT 0,
                    explained_topics TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (session_id, user_key)
                )
                """
            )
            self._conn.commit()

    def record_message(
        self,
        username: str,
        platform: str,
        message: str,
        user_id: str = "",
        now: float | None = None,
    ) -> LiveUserMemory:
        now = float(time.time() if now is None else now)
        user_key = build_user_key(username, platform, user_id)
        username = str(username or "").strip()
        platform = str(platform or "unknown").strip() or "unknown"
        message = str(message or "")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM live_user_memory
                WHERE session_id = ? AND user_key = ?
                """,
                (self._session_id, user_key),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO live_user_memory (
                        session_id, user_key, username, platform, first_seen,
                        last_seen, last_message, message_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        self._session_id,
                        user_key,
                        username,
                        platform,
                        now,
                        now,
                        message,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE live_user_memory
                    SET username = ?, platform = ?, last_seen = ?,
                        last_message = ?, message_count = message_count + 1
                    WHERE session_id = ? AND user_key = ?
                    """,
                    (
                        username,
                        platform,
                        now,
                        message,
                        self._session_id,
                        user_key,
                    ),
                )
            self._conn.commit()
        return self.get_user(username, platform, user_id) or LiveUserMemory(
            user_key=user_key,
            username=username,
            platform=platform,
            first_seen=now,
            last_seen=now,
            last_message=message,
            message_count=1,
            explained_topics=[],
        )

    def update_reply(
        self,
        username: str,
        platform: str,
        reply: str,
        user_id: str = "",
        welcomed: bool | None = None,
        preference_note: str = "",
        explained_topic: str = "",
    ) -> None:
        memory = self.get_user(username, platform, user_id)
        if memory is None:
            memory = self.record_message(username, platform, "", user_id=user_id)

        notes = _append_unique_line(memory.preference_notes, preference_note)
        topics = list(memory.explained_topics or [])
        topic = str(explained_topic or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
        was_welcomed = memory.welcomed if welcomed is None else bool(welcomed)

        with self._lock:
            self._conn.execute(
                """
                UPDATE live_user_memory
                SET last_reply = ?, preference_notes = ?, welcomed = ?,
                    explained_topics = ?
                WHERE session_id = ? AND user_key = ?
                """,
                (
                    str(reply or ""),
                    notes,
                    1 if was_welcomed else 0,
                    json.dumps(topics, ensure_ascii=False),
                    self._session_id,
                    memory.user_key,
                ),
            )
            self._conn.commit()

    def get_user(
        self,
        username: str,
        platform: str,
        user_id: str = "",
    ) -> LiveUserMemory | None:
        user_key = build_user_key(username, platform, user_id)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM live_user_memory
                WHERE session_id = ? AND user_key = ?
                """,
                (self._session_id, user_key),
            ).fetchone()
        return _row_to_memory(row) if row is not None else None

    def count_users(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM live_user_memory WHERE session_id = ?",
                (self._session_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def clear(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM live_user_memory WHERE session_id = ?",
                (self._session_id,),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def format_for_prompt(self, memory: LiveUserMemory | None) -> str:
        if memory is None:
            return ""
        topics = "、".join(memory.explained_topics or []) or "无"
        notes = memory.preference_notes.strip() or "无"
        last_reply = memory.last_reply.strip() or "无"
        return "\n".join(
            [
                "### 本场直播用户记忆",
                f"用户名: {memory.username or '未知'}",
                f"平台: {memory.platform or 'unknown'}",
                f"是否第一次出现: {'是' if memory.is_first_message else '否'}",
                f"本场发言次数: {memory.message_count}",
                f"最近发言: {memory.last_message or '无'}",
                f"最近回复内容: {last_reply}",
                f"用户提到的偏好/问题: {notes}",
                f"是否已经欢迎过: {'是' if memory.welcomed else '否'}",
                f"是否已经解释过某个问题: {topics}",
            ]
        )


def _row_to_memory(row: sqlite3.Row) -> LiveUserMemory:
    try:
        topics = json.loads(row["explained_topics"] or "[]")
    except json.JSONDecodeError:
        topics = []
    if not isinstance(topics, list):
        topics = []
    return LiveUserMemory(
        user_key=row["user_key"],
        username=row["username"],
        platform=row["platform"],
        first_seen=float(row["first_seen"]),
        last_seen=float(row["last_seen"]),
        last_message=row["last_message"],
        message_count=int(row["message_count"]),
        last_reply=row["last_reply"],
        preference_notes=row["preference_notes"],
        welcomed=bool(row["welcomed"]),
        explained_topics=[str(topic) for topic in topics],
    )


def _append_unique_line(existing: str, new_line: str) -> str:
    new_line = str(new_line or "").strip()
    if not new_line:
        return str(existing or "")
    lines = [line.strip() for line in str(existing or "").splitlines() if line.strip()]
    if new_line not in lines:
        lines.append(new_line)
    return "\n".join(lines)
