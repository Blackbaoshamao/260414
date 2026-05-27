"""Tests for memory_manager — save, load, hash, format."""
import json
import pytest
from pathlib import Path

from memory_manager import MemoryManager, _user_key, MAX_HISTORY_LEN


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path / "user_history"


@pytest.fixture
def mm(tmp_root):
    return MemoryManager(data_root=tmp_root)


class TestUserKey:
    def test_stable_hash(self):
        assert _user_key("alice", "123") == _user_key("alice", "123")

    def test_different_users(self):
        assert _user_key("alice", "1") != _user_key("bob", "2")

    def test_empty_user_id(self):
        key = _user_key("alice", "")
        assert len(key) == 16

    def test_none_user_id(self):
        key = _user_key("bob", None)
        assert len(key) == 16


class TestSaveAndLoad:
    @pytest.mark.asyncio
    async def test_save_creates_files(self, mm, tmp_root):
        await mm.save_interaction("alice", "u1", "hello", "hi there")
        udir = mm._user_dir("alice", "u1")
        assert (udir / "profile.json").exists()
        assert (udir / "chat_log.jsonl").exists()

    @pytest.mark.asyncio
    async def test_profile_content(self, mm):
        await mm.save_interaction("alice", "u1", "hello", "hi there")
        udir = mm._user_dir("alice", "u1")
        profile = json.loads((udir / "profile.json").read_text(encoding="utf-8"))
        assert profile["nickname"] == "alice"
        assert profile["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_load_returns_empty_for_new_user(self, mm):
        result = await mm.load_recent_history("unknown", "nobody")
        assert result == []

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, mm):
        await mm.save_interaction("alice", "u1", "hello", "hi")
        history = await mm.load_recent_history("alice", "u1")
        assert len(history) == 1
        assert history[0]["user_msg"] == "hello"
        assert history[0]["ai_reply"] == "hi"

    @pytest.mark.asyncio
    async def test_load_respects_limit(self, mm):
        for i in range(10):
            await mm.save_interaction("alice", "u1", f"msg{i}", f"reply{i}")
        history = await mm.load_recent_history("alice", "u1", limit=3)
        assert len(history) == 3
        assert history[0]["user_msg"] == "msg7"
        assert history[2]["user_msg"] == "msg9"

    @pytest.mark.asyncio
    async def test_default_limit_is_max_history_len(self, mm):
        for i in range(10):
            await mm.save_interaction("alice", "u1", f"msg{i}", f"reply{i}")
        history = await mm.load_recent_history("alice", "u1")
        assert len(history) == MAX_HISTORY_LEN

    @pytest.mark.asyncio
    async def test_append_mode(self, mm):
        await mm.save_interaction("alice", "u1", "first", "r1")
        await mm.save_interaction("alice", "u1", "second", "r2")
        history = await mm.load_recent_history("alice", "u1", limit=10)
        assert len(history) == 2


class TestFormatHistory:
    def test_empty_returns_empty_string(self, mm):
        assert mm.format_history_for_prompt([]) == ""

    def test_formats_correctly(self, mm):
        history = [
            {"user_msg": "hello", "ai_reply": "hi"},
            {"user_msg": "how are you", "ai_reply": "fine"},
        ]
        result = mm.format_history_for_prompt(history)
        assert "### 历史对话摘要 ###" in result
        assert "观众: hello" in result
        assert "你: hi" in result
        assert "观众: how are you" in result


class TestRobustness:
    @pytest.mark.asyncio
    async def test_load_failure_returns_empty(self, tmp_path):
        mm = MemoryManager(data_root=tmp_path / "nonexistent")
        result = await mm.load_recent_history("alice", "u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_save_failure_does_not_raise(self, tmp_path):
        mm = MemoryManager(data_root=Path("/nonexistent/path/that/cannot/be/created"))
        await mm.save_interaction("alice", "u1", "hello", "hi")


class TestRoomIsolation:
    @pytest.mark.asyncio
    async def test_different_rooms_isolated(self, tmp_root):
        mm_a = MemoryManager(data_root=tmp_root, room_id="room_a")
        mm_b = MemoryManager(data_root=tmp_root, room_id="room_b")
        await mm_a.save_interaction("alice", "u1", "hello from A", "reply A")
        await mm_b.save_interaction("alice", "u1", "hello from B", "reply B")
        history_a = await mm_a.load_recent_history("alice", "u1")
        history_b = await mm_b.load_recent_history("alice", "u1")
        assert len(history_a) == 1
        assert history_a[0]["user_msg"] == "hello from A"
        assert len(history_b) == 1
        assert history_b[0]["user_msg"] == "hello from B"

    @pytest.mark.asyncio
    async def test_no_room_id_backward_compat(self, tmp_root):
        mm_no_room = MemoryManager(data_root=tmp_root)
        await mm_no_room.save_interaction("alice", "u1", "hello", "hi")
        history = await mm_no_room.load_recent_history("alice", "u1")
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_room_paths_are_separated(self, tmp_root):
        mm_a = MemoryManager(data_root=tmp_root, room_id="room_a")
        mm_b = MemoryManager(data_root=tmp_root, room_id="room_b")
        await mm_a.save_interaction("alice", "u1", "hi", "hey")
        dir_a = mm_a._user_dir("alice", "u1")
        dir_b = mm_b._user_dir("alice", "u1")
        assert "room_a" in str(dir_a)
        assert "room_b" in str(dir_b)
        assert dir_a != dir_b
