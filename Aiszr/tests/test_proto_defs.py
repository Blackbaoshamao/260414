"""Tests for proto_defs.py - Protobuf message class round-trip serialization."""
import pytest


def test_pushframe_roundtrip():
    """PushFrame can serialize/deserialize with seqId, logId, payloadType, payload."""
    from proto_defs import PushFrame

    frame = PushFrame()
    frame.seqId = 100
    frame.logId = 200
    frame.payloadType = "msg"
    frame.payload = b"\x1f\x8b\x00\x00test_data"

    data = frame.SerializeToString()
    assert len(data) > 0

    frame2 = PushFrame()
    frame2.ParseFromString(data)
    assert frame2.seqId == 100
    assert frame2.logId == 200
    assert frame2.payloadType == "msg"
    assert frame2.payload == b"\x1f\x8b\x00\x00test_data"


def test_response_roundtrip():
    """Response can serialize/deserialize with messagesList, internalExt, needAck."""
    from proto_defs import Response, Message

    # Build a Response with one message
    inner_msg = Message()
    inner_msg.method = "WebcastChatMessage"
    inner_msg.payload = b"chat_payload"

    resp = Response()
    resp.messagesList.append(inner_msg)
    resp.internalExt = "ext_data_123"
    resp.needAck = True
    resp.now = 1713140234123  # milliseconds

    data = resp.SerializeToString()
    assert len(data) > 0

    resp2 = Response()
    resp2.ParseFromString(data)
    assert len(resp2.messagesList) == 1
    assert resp2.messagesList[0].method == "WebcastChatMessage"
    assert resp2.messagesList[0].payload == b"chat_payload"
    assert resp2.internalExt == "ext_data_123"
    assert resp2.needAck is True
    assert resp2.now == 1713140234123


def test_message_payload_field_2():
    """Message.payload is at field number 2 (NOT 3). Critical field mapping."""
    from proto_defs import Message

    msg = Message()
    msg.method = "WebcastChatMessage"
    msg.payload = b"\x0a\x06\x08\x01\x1a\x04test"
    msg.msgId = 999

    data = msg.SerializeToString()
    assert len(data) > 0

    msg2 = Message()
    msg2.ParseFromString(data)
    assert msg2.method == "WebcastChatMessage"
    assert msg2.payload == b"\x0a\x06\x08\x01\x1a\x04test"
    assert msg2.msgId == 999


def test_user_roundtrip():
    """User can serialize/deserialize with id, nickName, idStr."""
    from proto_defs import User

    user = User()
    user.id = 12345678901234
    user.nickName = "testuser"
    user.idStr = "12345678901234"

    data = user.SerializeToString()
    assert len(data) > 0

    user2 = User()
    user2.ParseFromString(data)
    assert user2.id == 12345678901234
    assert user2.nickName == "testuser"
    assert user2.idStr == "12345678901234"


def test_chatmessage_roundtrip():
    """ChatMessage can serialize/deserialize with user and content."""
    from proto_defs import ChatMessage, User

    user = User()
    user.id = 111
    user.nickName = "chatter"

    chat = ChatMessage()
    chat.user.CopyFrom(user)
    chat.content = "hello world"

    data = chat.SerializeToString()
    assert len(data) > 0

    chat2 = ChatMessage()
    chat2.ParseFromString(data)
    assert chat2.user.id == 111
    assert chat2.user.nickName == "chatter"
    assert chat2.content == "hello world"


def test_giftmessage_roundtrip():
    """GiftMessage can serialize/deserialize with user, repeatCount, gift."""
    from proto_defs import GiftMessage, User, GiftStruct

    user = User()
    user.id = 222
    user.nickName = "gifter"

    gift_struct = GiftStruct()
    gift_struct.name = "Rose"
    gift_struct.diamondCount = 1
    gift_struct.icon = "https://example.com/rose.png"

    gift = GiftMessage()
    gift.user.CopyFrom(user)
    gift.repeatCount = 5
    gift.gift.CopyFrom(gift_struct)
    gift.totalCount = 5

    data = gift.SerializeToString()
    assert len(data) > 0

    gift2 = GiftMessage()
    gift2.ParseFromString(data)
    assert gift2.user.id == 222
    assert gift2.repeatCount == 5
    assert gift2.gift.name == "Rose"
    assert gift2.gift.diamondCount == 1
    assert gift2.gift.icon == "https://example.com/rose.png"
    assert gift2.totalCount == 5


def test_giftstruct_roundtrip():
    """GiftStruct can serialize with diamondCount, name, icon."""
    from proto_defs import GiftStruct

    gs = GiftStruct()
    gs.diamondCount = 100
    gs.name = "Rocket"
    gs.icon = "https://example.com/rocket.png"

    data = gs.SerializeToString()
    gs2 = GiftStruct()
    gs2.ParseFromString(data)
    assert gs2.diamondCount == 100
    assert gs2.name == "Rocket"
    assert gs2.icon == "https://example.com/rocket.png"


def test_likemessage_roundtrip():
    """LikeMessage can serialize with user."""
    from proto_defs import LikeMessage, User

    user = User()
    user.id = 333
    user.nickName = "liker"

    like = LikeMessage()
    like.user.CopyFrom(user)
    like.count = 10
    like.total = 100

    data = like.SerializeToString()
    like2 = LikeMessage()
    like2.ParseFromString(data)
    assert like2.user.id == 333
    assert like2.count == 10
    assert like2.total == 100


def test_socialmessage_roundtrip():
    """SocialMessage can serialize with user and action."""
    from proto_defs import SocialMessage, User

    user = User()
    user.id = 444
    user.nickName = "follower"

    social = SocialMessage()
    social.user.CopyFrom(user)
    social.action = 1  # 1 = follow

    data = social.SerializeToString()
    social2 = SocialMessage()
    social2.ParseFromString(data)
    assert social2.user.id == 444
    assert social2.action == 1
