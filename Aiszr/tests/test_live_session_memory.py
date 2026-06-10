from live_session_memory import LiveSessionMemory, build_user_key


def test_build_user_key_prefers_platform_and_user_id():
    assert build_user_key("alice", "wechat", "u1") == "wechat:u1"
    assert build_user_key("alice", "douyin", "") == "douyin:alice"


def test_record_message_creates_and_updates_user(tmp_path):
    memory = LiveSessionMemory(tmp_path / "session.sqlite3")

    first = memory.record_message("alice", "wechat", "多少钱", user_id="u1", now=10)
    second = memory.record_message("alice", "wechat", "包邮吗", user_id="u1", now=20)

    assert first.is_first_message is True
    assert second.message_count == 2
    assert second.first_seen == 10
    assert second.last_seen == 20
    assert second.last_message == "包邮吗"
    assert memory.count_users() == 1


def test_update_reply_marks_welcomed_notes_and_topics(tmp_path):
    memory = LiveSessionMemory(tmp_path / "session.sqlite3")
    memory.record_message("bob", "douyin", "黑龙江几天到", user_id="u2")

    memory.update_reply(
        "bob",
        "douyin",
        "黑龙江大概三到四天",
        user_id="u2",
        welcomed=True,
        preference_note="关心黑龙江物流",
        explained_topic="物流",
    )

    user = memory.get_user("bob", "douyin", "u2")
    assert user is not None
    assert user.last_reply == "黑龙江大概三到四天"
    assert user.welcomed is True
    assert "关心黑龙江物流" in user.preference_notes
    assert user.explained_topics == ["物流"]


def test_format_for_prompt_includes_required_memory_fields(tmp_path):
    memory = LiveSessionMemory(tmp_path / "session.sqlite3")
    memory.record_message("carol", "wechat", "有售后吗", user_id="u3")
    memory.update_reply(
        "carol",
        "wechat",
        "按页面售后政策来",
        user_id="u3",
        explained_topic="售后",
    )

    text = memory.format_for_prompt(memory.get_user("carol", "wechat", "u3"))

    assert "本场直播用户记忆" in text
    assert "用户名: carol" in text
    assert "平台: wechat" in text
    assert "最近发言: 有售后吗" in text
    assert "最近回复内容: 按页面售后政策来" in text
    assert "售后" in text


def test_clear_removes_current_session_only(tmp_path):
    db_path = tmp_path / "session.sqlite3"
    first = LiveSessionMemory(db_path, session_id="one")
    second = LiveSessionMemory(db_path, session_id="two")
    first.record_message("alice", "wechat", "hello", user_id="u1")
    second.record_message("bob", "wechat", "hello", user_id="u2")

    first.clear()

    assert first.count_users() == 0
    assert second.count_users() == 1
