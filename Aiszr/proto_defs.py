"""Hand-written Protobuf message classes for Douyin live WebSocket danmaku protocol.

Defines only the fields needed for the four target message types:
chat, gift, follow, like. Field numbers cross-verified from three independent
sources (DySpider, zboyco/douyin-live-go, saermart/DouyinLiveWebFetcher).

Uses protobuf descriptor-based dynamic message definitions to get proper
ParseFromString() / SerializeToString() support without protoc-generated code.
"""
from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message_factory as _message_factory

# ---------------------------------------------------------------------------
# Build a single FileDescriptorProto containing all message types
# ---------------------------------------------------------------------------

_FILE = descriptor_pb2.FileDescriptorProto()
_FILE.name = "douyin_webcast.proto"
_FILE.package = "douyin"
_FILE.syntax = "proto3"

# -- Helper: add a message + its fields to the file descriptor proto -------


def _add_msg(name, fields):
    """Add a message descriptor to the file. fields = [(name, number, type)]."""
    msg = _FILE.message_type.add()
    msg.name = name
    for fname, fnum, ftype in fields:
        f = msg.field.add()
        f.name = fname
        f.number = fnum
        f.type = ftype
        f.label = 1  # LABEL_OPTIONAL
    return msg


# Proto field type constants
_UINT64 = 4    # TYPE_UINT64
_UINT32 = 13   # TYPE_UINT32
_STRING = 9    # TYPE_STRING
_BYTES = 12    # TYPE_BYTES
_BOOL = 8      # TYPE_BOOL
_MESSAGE = 11  # TYPE_MESSAGE


def _add_all_messages():
    """Build all message type descriptors in the file proto."""

    # Message names and their type_name references need to be set
    # We'll build them in dependency order.

    # 1. User
    _add_msg("User", [
        ("id", 1, _UINT64),
        ("shortId", 2, _UINT64),
        ("nickName", 3, _STRING),
        ("gender", 4, _UINT32),
        ("signature", 5, _STRING),
        ("level", 6, _UINT32),
        ("displayId", 38, _STRING),
        ("secUid", 46, _STRING),
        ("idStr", 1028, _STRING),
    ])

    # 2. TextPiece
    _add_msg("TextPiece", [
        ("stringValue", 3, _STRING),
    ])

    # 3. Text
    text_msg = _add_msg("Text", [
        ("defaultPatter", 2, _STRING),
    ])
    fp = text_msg.field.add()
    fp.name = "piecesList"
    fp.number = 4
    fp.type = _MESSAGE
    fp.label = 3  # LABEL_REPEATED
    fp.type_name = "douyin.TextPiece"

    # 4. GiftStruct
    _add_msg("GiftStruct", [
        ("image", 1, _STRING),
        ("describe", 2, _STRING),
        ("diamondCount", 12, _UINT64),
        ("name", 16, _STRING),
        ("icon", 21, _STRING),
    ])

    # 5. ChatMessage (user at field 2 is message type)
    chat_msg = _add_msg("ChatMessage", [
        ("content", 3, _STRING),
        ("visibleToSender", 4, _BOOL),
    ])
    # Insert user field at field 2 as message type
    f = chat_msg.field.add()
    f.name = "user"
    f.number = 2
    f.type = _MESSAGE
    f.label = 1
    f.type_name = "douyin.User"
    f22 = chat_msg.field.add()
    f22.name = "rtfContent"
    f22.number = 22
    f22.type = _MESSAGE
    f22.label = 1
    f22.type_name = "douyin.Text"

    # 6. GiftMessage (user at field 7, gift at field 15 are message types)
    gift_msg = _add_msg("GiftMessage", [
        ("giftId", 2, _UINT64),
        ("fanTicketCount", 3, _UINT64),
        ("groupCount", 4, _UINT64),
        ("repeatCount", 5, _UINT64),
        ("comboCount", 6, _UINT64),
        ("repeatEnd", 9, _UINT64),
        ("totalCount", 29, _UINT64),
    ])
    # user at field 7
    f7 = gift_msg.field.add()
    f7.name = "user"
    f7.number = 7
    f7.type = _MESSAGE
    f7.label = 1
    f7.type_name = "douyin.User"
    # gift at field 15
    f15 = gift_msg.field.add()
    f15.name = "gift"
    f15.number = 15
    f15.type = _MESSAGE
    f15.label = 1
    f15.type_name = "douyin.GiftStruct"

    # 7. LikeMessage (user at field 5 is message type)
    like_msg = _add_msg("LikeMessage", [
        ("count", 2, _UINT64),
        ("total", 3, _UINT64),
        ("color", 4, _UINT64),
    ])
    f5 = like_msg.field.add()
    f5.name = "user"
    f5.number = 5
    f5.type = _MESSAGE
    f5.label = 1
    f5.type_name = "douyin.User"

    # 8. SocialMessage (user at field 2 is message type)
    social_msg = _add_msg("SocialMessage", [
        ("action", 3, _UINT64),
    ])
    f2s = social_msg.field.add()
    f2s.name = "user"
    f2s.number = 2
    f2s.type = _MESSAGE
    f2s.label = 1
    f2s.type_name = "douyin.User"

    # 9. EmojiChatMessage (user at field 2 is message type)
    emoji_chat_msg = _add_msg("EmojiChatMessage", [
        ("emojiId", 3, _UINT64),
        ("defaultContent", 5, _STRING),
    ])
    f2e = emoji_chat_msg.field.add()
    f2e.name = "user"
    f2e.number = 2
    f2e.type = _MESSAGE
    f2e.label = 1
    f2e.type_name = "douyin.User"

    # 10. MemberMessage (user at field 2 is message type)
    member_msg = _add_msg("MemberMessage", [
        ("memberCount", 3, _UINT64),
        ("action", 10, _UINT64),
        ("actionDescription", 11, _STRING),
        ("userId", 12, _UINT64),
        ("popStr", 14, _STRING),
    ])
    f2m = member_msg.field.add()
    f2m.name = "user"
    f2m.number = 2
    f2m.type = _MESSAGE
    f2m.label = 1
    f2m.type_name = "douyin.User"

    # 11. RoomUserSeqMessage
    _add_msg("RoomUserSeqMessage", [
        ("total", 3, _UINT64),
        ("popStr", 4, _STRING),
        ("popularity", 6, _UINT64),
        ("totalUser", 7, _UINT64),
        ("totalUserStr", 8, _STRING),
        ("totalStr", 9, _STRING),
        ("onlineUserForAnchor", 10, _STRING),
        ("totalPvForAnchor", 11, _STRING),
        ("upRightStatsStr", 12, _STRING),
        ("upRightStatsStrComplete", 13, _STRING),
    ])

    # 12. Message (payload at field 2 -- CRITICAL: NOT field 3)
    _add_msg("Message", [
        ("method", 1, _STRING),
        ("payload", 2, _BYTES),  # field 2, NOT 3
        ("msgId", 3, _UINT64),
        ("msgType", 4, _UINT32),
    ])

    # 13. Response (messagesList at field 1 is repeated message type)
    resp_msg = _add_msg("Response", [
        ("cursor", 2, _STRING),
        ("fetchInterval", 3, _UINT64),
        ("now", 4, _UINT64),
        ("internalExt", 5, _STRING),
        ("fetchType", 6, _UINT32),
        ("heartbeatDuration", 8, _UINT64),
        ("needAck", 9, _BOOL),
        ("pushServer", 10, _STRING),
        ("liveCursor", 11, _STRING),
        ("historyNoMore", 12, _BOOL),
    ])
    # messagesList at field 1, repeated
    fm = resp_msg.field.add()
    fm.name = "messagesList"
    fm.number = 1
    fm.type = _MESSAGE
    fm.label = 3  # LABEL_REPEATED
    fm.type_name = "douyin.Message"

    # 14. PushFrame (outer envelope)
    _add_msg("PushFrame", [
        ("seqId", 1, _UINT64),
        ("logId", 2, _UINT64),
        ("service", 3, _UINT64),
        ("method", 4, _UINT64),
        ("headersList", 5, _STRING),  # simplified, not parsed
        ("payloadEncoding", 6, _STRING),
        ("payloadType", 7, _STRING),
        ("payload", 8, _BYTES),
    ])


_add_all_messages()

# ---------------------------------------------------------------------------
# Register the file descriptor and create message classes
# ---------------------------------------------------------------------------

_pool = _descriptor_pool.Default()
_file_desc = _pool.Add(_FILE)

# Create message classes via factory
PushFrame = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["PushFrame"]
)
Response = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["Response"]
)
Message = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["Message"]
)
User = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["User"]
)
TextPiece = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["TextPiece"]
)
Text = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["Text"]
)
ChatMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["ChatMessage"]
)
GiftMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["GiftMessage"]
)
GiftStruct = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["GiftStruct"]
)
LikeMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["LikeMessage"]
)
SocialMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["SocialMessage"]
)
EmojiChatMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["EmojiChatMessage"]
)
MemberMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["MemberMessage"]
)
RoomUserSeqMessage = _message_factory.GetMessageClass(
    _file_desc.message_types_by_name["RoomUserSeqMessage"]
)

__all__ = [
    "PushFrame",
    "Response",
    "Message",
    "User",
    "TextPiece",
    "Text",
    "ChatMessage",
    "GiftMessage",
    "GiftStruct",
    "LikeMessage",
    "SocialMessage",
    "EmojiChatMessage",
    "MemberMessage",
    "RoomUserSeqMessage",
]
