"""Tests for decoder.py - DanmakuDecoder decode pipeline and ACK construction."""
import gzip
import time
from datetime import datetime, timezone

import pytest

from proto_defs import (
    ChatMessage,
    EmojiChatMessage,
    GiftMessage,
    GiftStruct,
    LikeMessage,
    Message,
    MemberMessage,
    PushFrame,
    Response,
    RoomUserSeqMessage,
    SocialMessage,
    User,
)


# ---------------------------------------------------------------------------
# Helpers: build test fixtures using protobuf message classes
# ---------------------------------------------------------------------------


def _make_user(user_id: int = 123, nickname: str = "testuser") -> User:
    """Create a User protobuf message."""
    u = User()
    u.id = user_id
    u.nickName = nickname
    return u


def _make_user_with_id_str(user_id: str = "1234567890", nickname: str = "testuser") -> User:
    """Create a User protobuf message using string-style id field."""
    u = User()
    u.idStr = user_id
    u.nickName = nickname
    return u


def _build_chat_payload(user_id: int = 123, nickname: str = "testuser",
                        content: str = "hello") -> bytes:
    """Serialize a ChatMessage to bytes."""
    chat = ChatMessage()
    chat.user.CopyFrom(_make_user(user_id, nickname))
    chat.content = content
    return chat.SerializeToString()


def _build_chat_payload_with_id_str(user_id: str = "1234567890", nickname: str = "testuser",
                                    content: str = "hello") -> bytes:
    """Serialize a ChatMessage with string user id field to bytes."""
    chat = ChatMessage()
    chat.user.CopyFrom(_make_user_with_id_str(user_id, nickname))
    chat.content = content
    return chat.SerializeToString()


def _build_chat_payload_with_rtf(user_id: int = 123, nickname: str = "testuser",
                                 content: str = "hello") -> bytes:
    """Serialize a ChatMessage using rtfContent instead of content."""
    chat = ChatMessage()
    chat.user.CopyFrom(_make_user(user_id, nickname))
    chat.rtfContent.defaultPatter = content
    return chat.SerializeToString()


def _build_gift_payload(user_id: int = 456, nickname: str = "gifter",
                        repeat_count: int = 5, gift_name: str = "Rose",
                        diamond_count: int = 1,
                        gift_icon: str = "https://example.com/rose.png",
                        total_count: int = 5) -> bytes:
    """Serialize a GiftMessage to bytes."""
    gift_msg = GiftMessage()
    gift_msg.user.CopyFrom(_make_user(user_id, nickname))
    gift_msg.repeatCount = repeat_count

    gs = GiftStruct()
    gs.name = gift_name
    gs.diamondCount = diamond_count
    gs.icon = gift_icon
    gift_msg.gift.CopyFrom(gs)
    gift_msg.totalCount = total_count
    return gift_msg.SerializeToString()


def _build_like_payload(user_id: int = 789, nickname: str = "liker") -> bytes:
    """Serialize a LikeMessage to bytes."""
    like = LikeMessage()
    like.user.CopyFrom(_make_user(user_id, nickname))
    return like.SerializeToString()


def _build_emoji_chat_payload(user_id: int = 321, nickname: str = "emoji_user",
                              content: str = "[微笑]") -> bytes:
    """Serialize an EmojiChatMessage to bytes."""
    chat = EmojiChatMessage()
    chat.user.CopyFrom(_make_user(user_id, nickname))
    chat.defaultContent = content
    return chat.SerializeToString()


def _build_social_payload(user_id: int = 111, nickname: str = "follower",
                          action: int = 1) -> bytes:
    """Serialize a SocialMessage to bytes."""
    social = SocialMessage()
    social.user.CopyFrom(_make_user(user_id, nickname))
    social.action = action
    return social.SerializeToString()


def _build_member_payload(user_id: int = 222, nickname: str = "viewer") -> bytes:
    """Serialize a MemberMessage to bytes."""
    member = MemberMessage()
    member.user.CopyFrom(_make_user(user_id, nickname))
    member.memberCount = 100
    return member.SerializeToString()


def _build_room_user_seq_payload(current: int = 1234, total: str = "4.2万") -> bytes:
    """Serialize a RoomUserSeqMessage to bytes."""
    msg = RoomUserSeqMessage()
    msg.total = current
    msg.totalPvForAnchor = total
    return msg.SerializeToString()


def _build_response(messages: list[tuple[str, bytes]],
                    now_ms: int = 1713140234123,
                    internal_ext: str = "ext123",
                    need_ack: bool = False) -> bytes:
    """Build a serialized Response containing the given messages.

    messages: list of (method_name, serialized_inner_payload) tuples.
    Returns the raw Response bytes (NOT gzip'd, NOT wrapped in PushFrame).
    """
    resp = Response()
    resp.now = now_ms
    resp.internalExt = internal_ext
    resp.needAck = need_ack

    for method, payload_bytes in messages:
        msg = Message()
        msg.method = method
        msg.payload = payload_bytes
        resp.messagesList.append(msg)

    return resp.SerializeToString()


def _build_frame(response_bytes: bytes,
                 seq_id: int = 1, log_id: int = 100) -> bytes:
    """Wrap raw Response bytes in gzip, then in a PushFrame. Returns frame bytes."""
    compressed = gzip.compress(response_bytes)
    frame = PushFrame()
    frame.seqId = seq_id
    frame.logId = log_id
    frame.payload = compressed
    return frame.SerializeToString()


def _build_test_frame(messages: list[tuple[str, bytes]],
                      now_ms: int = 1713140234123,
                      internal_ext: str = "ext123",
                      need_ack: bool = False,
                      seq_id: int = 1,
                      log_id: int = 100) -> bytes:
    """Convenience: build full PushFrame from message list."""
    resp_bytes = _build_response(messages, now_ms, internal_ext, need_ack)
    return _build_frame(resp_bytes, seq_id, log_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecodeChat:
    """Test 1: decode() with ChatMessage returns correct flat dict."""

    def test_chat_basic(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastChatMessage", _build_chat_payload(123, "Alice", "hello world"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "chat"
        assert msg["user_id"] == "123"
        assert msg["nickname"] == "Alice"
        assert msg["content"] == "hello world"
        assert isinstance(msg["timestamp"], float)
        assert isinstance(msg["time"], str)
        # Verify ISO format
        assert "T" in msg["time"]

    def test_chat_multiple_messages(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastChatMessage", _build_chat_payload(1, "A", "msg1")),
            ("WebcastChatMessage", _build_chat_payload(2, "B", "msg2")),
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 2
        assert result.messages[0]["content"] == "msg1"
        assert result.messages[1]["content"] == "msg2"

    def test_chat_kept_when_user_fields_are_missing(self):
        from decoder import DanmakuDecoder

        chat = ChatMessage()
        chat.content = "only content"
        raw = _build_test_frame([
            ("WebcastChatMessage", chat.SerializeToString())
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "chat"
        assert msg["user_id"] == ""
        assert msg["nickname"] == ""
        assert msg["content"] == "only content"

    def test_chat_prefers_string_user_id_when_available(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastChatMessage", _build_chat_payload_with_id_str("998877", "Alice", "hello"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["user_id"] == "998877"
        assert msg["nickname"] == "Alice"
        assert msg["content"] == "hello"

    def test_emoji_chat_mapped_to_chat(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastEmojiChatMessage", _build_emoji_chat_payload(321, "Emoji", "[微笑]"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "chat"
        assert msg["user_id"] == "321"
        assert msg["nickname"] == "Emoji"
        assert msg["content"] == "[微笑]"


class TestDecodeGift:
    """Test 2: decode() with GiftMessage returns gift fields + gift_total."""

    def test_gift_basic(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastGiftMessage",
             _build_gift_payload(456, "Gifter", 5, "Rose", 1,
                                 "https://img.com/rose.png", 5))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "gift"
        assert msg["user_id"] == "456"
        assert msg["nickname"] == "Gifter"
        assert msg["gift_name"] == "Rose"
        assert msg["gift_count"] == 5
        assert msg["gift_value"] == 1
        assert msg["gift_icon"] == "https://img.com/rose.png"
        assert msg["gift_total"] == 5  # 5 * 1

    def test_gift_total_calculation(self):
        """gift_total should be gift_count * gift_value."""
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastGiftMessage",
             _build_gift_payload(100, "Big", 10, "Rocket", 50,
                                 "https://img.com/rocket.png", 500))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        msg = result.messages[0]
        assert msg["gift_count"] == 10
        assert msg["gift_value"] == 50
        assert msg["gift_total"] == 10 * 50  # 500


class TestDecodeLike:
    """Test 3: decode() with LikeMessage returns only common fields."""

    def test_like_basic(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastLikeMessage", _build_like_payload(789, "Liker"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "like"
        assert msg["user_id"] == "789"
        assert msg["nickname"] == "Liker"
        # Like has only common fields
        assert "content" not in msg
        assert "gift_name" not in msg


class TestDecodeFollow:
    """Test 4 & 5: decode() with SocialMessage, action==1 -> follow."""

    def test_follow_action_1(self):
        """action==1 returns follow type."""
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastSocialMessage", _build_social_payload(111, "Follower", 1))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "follow"
        assert msg["user_id"] == "111"
        assert msg["nickname"] == "Follower"


class TestDecodeEnterAndStats:
    def test_member_message_maps_to_enter(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastMemberMessage", _build_member_payload(222, "Viewer"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "enter"
        assert msg["user_id"] == "222"
        assert msg["nickname"] == "Viewer"

    def test_room_user_seq_maps_to_stats(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastRoomUserSeqMessage", _build_room_user_seq_payload(1234, "4.2万"))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "stats"
        assert msg["current_viewers"] == 1234
        assert msg["total_viewers"] == "4.2万"

    def test_follow_action_not_1_discarded(self):
        """action!=1 returns empty (discarded per D-09)."""
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastSocialMessage", _build_social_payload(111, "Fan", 2))
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 0


class TestDecodeUnknownMethod:
    """Test 6: unknown method names discarded silently."""

    def test_unknown_method_discarded(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastSomeFutureMessage", b"\x08\x01"),  # some unknown payload
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 0
        stats = decoder.method_stats_snapshot()
        assert stats["unknown_total"] == 1
        assert stats["unknown_method_counts"]["WebcastSomeFutureMessage"] == 1

    def test_mixed_known_and_unknown(self):
        """Unknown messages filtered, known messages kept."""
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastSomeFutureMessage", b"\x08\x01"),
            ("WebcastChatMessage", _build_chat_payload(1, "A", "hi")),
            ("WebcastAnotherUnknownMessage", b"\x08\x02"),
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 1
        assert result.messages[0]["type"] == "chat"
        assert result.messages[0]["content"] == "hi"
        stats = decoder.method_stats_snapshot()
        assert stats["method_counts"]["WebcastChatMessage"] == 1
        assert stats["method_counts"]["WebcastSomeFutureMessage"] == 1
        assert stats["method_counts"]["WebcastAnotherUnknownMessage"] == 1


class TestDecodeMalformed:
    """Test 7: malformed bytes return empty list, no crash."""

    def test_empty_bytes(self):
        from decoder import DanmakuDecoder

        decoder = DanmakuDecoder()
        result = decoder.decode(b"")
        assert result.messages == []

    def test_random_bytes(self):
        from decoder import DanmakuDecoder

        decoder = DanmakuDecoder()
        result = decoder.decode(b"\x00\x01\x02\x03\xff\xfe\xfd")
        assert result.messages == []

    def test_invalid_gzip_in_payload(self):
        """PushFrame parses but payload is not valid gzip."""
        from decoder import DanmakuDecoder

        frame = PushFrame()
        frame.payload = b"not_gzip_data"

        decoder = DanmakuDecoder()
        result = decoder.decode(frame.SerializeToString())
        assert result.messages == []


class TestBuildAck:
    """Test 8: build_ack() returns valid PushFrame bytes."""

    def test_ack_construction(self):
        from decoder import DanmakuDecoder

        # Build a frame + response to ack
        resp_bytes = _build_response(
            [("WebcastChatMessage", _build_chat_payload())],
            now_ms=1713140234123,
            internal_ext="ack_data_here",
            need_ack=True,
        )
        compressed = gzip.compress(resp_bytes)
        frame = PushFrame()
        frame.seqId = 42
        frame.logId = 999
        frame.payload = compressed
        raw = frame.SerializeToString()

        # Decode to get frame/response
        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert result.need_ack is True
        assert result.frame is not None
        assert result.response is not None

        # Build ACK
        ack_bytes = decoder.build_ack(result.frame, result.response)
        assert isinstance(ack_bytes, bytes)
        assert len(ack_bytes) > 0

        # Verify ACK content
        ack_frame = PushFrame()
        ack_frame.ParseFromString(ack_bytes)
        assert ack_frame.payloadType == "ack"
        assert ack_frame.payload == b"ack_data_here"
        assert ack_frame.logId == 999


class TestNeedAck:
    """Test 9: needAck flag accessible via DecodeResult."""

    def test_need_ack_true(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame(
            [("WebcastChatMessage", _build_chat_payload())],
            need_ack=True,
        )
        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert result.need_ack is True

    def test_need_ack_false(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame(
            [("WebcastChatMessage", _build_chat_payload())],
            need_ack=False,
        )
        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert result.need_ack is False


class TestFlatOutput:
    """Test 10: all output dicts are flat (no nested objects)."""

    def test_no_nested_objects(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastChatMessage", _build_chat_payload(1, "A", "hi")),
            ("WebcastGiftMessage",
             _build_gift_payload(2, "B", 3, "Rose", 1, "url", 3)),
            ("WebcastLikeMessage", _build_like_payload(3, "C")),
            ("WebcastSocialMessage", _build_social_payload(4, "D", 1)),
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 4

        for msg in result.messages:
            for key, value in msg.items():
                assert not isinstance(value, (dict, list)), \
                    f"Nested value at key '{key}': {value}"


class TestUserIdString:
    """Test 11: user_id is always a string (not int)."""

    def test_user_id_is_string(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame([
            ("WebcastChatMessage", _build_chat_payload(123456789, "User", "hi")),
        ])

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        assert len(result.messages) == 1
        assert isinstance(result.messages[0]["user_id"], str)
        assert result.messages[0]["user_id"] == "123456789"


class TestTimestamps:
    """Test 12: both timestamp (float) and time (ISO string) present."""

    def test_dual_timestamps(self):
        from decoder import DanmakuDecoder

        raw = _build_test_frame(
            [("WebcastChatMessage", _build_chat_payload(1, "A", "hi"))],
            now_ms=1713140234123,
        )

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        msg = result.messages[0]

        # timestamp should be float (seconds, from ms)
        assert isinstance(msg["timestamp"], float)
        assert msg["timestamp"] == 1713140234.123

        # time should be ISO 8601 string
        assert isinstance(msg["time"], str)
        assert "T" in msg["time"]

    def test_timestamp_fallback_when_now_zero(self):
        """When response.now is 0, timestamp uses time.time() fallback."""
        from decoder import DanmakuDecoder

        raw = _build_test_frame(
            [("WebcastChatMessage", _build_chat_payload())],
            now_ms=0,
        )

        decoder = DanmakuDecoder()
        result = decoder.decode(raw)
        msg = result.messages[0]

        # Should still have a valid timestamp (from time.time())
        assert isinstance(msg["timestamp"], float)
        assert msg["timestamp"] > 0
        assert isinstance(msg["time"], str)
