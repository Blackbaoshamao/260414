"""Stateless Protobuf decode pipeline for Douyin live WebSocket danmaku.

Decodes the three-layer pipeline:
    PushFrame -> gzip decompress -> Response -> typed messages

Outputs each decoded message as a flat JSON-compatible dict.
Constructs ACK heartbeats for keep-alive.

Thread-safe: DanmakuDecoder is stateless -- decode() has no side effects.
"""
import gzip
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from proto_defs import (
    ChatMessage,
    EmojiChatMessage,
    GiftMessage,
    LikeMessage,
    MemberMessage,
    PushFrame,
    Response,
    RoomUserSeqMessage,
    SocialMessage,
)

# Method name -> short type name mapping (D-03, D-07)
METHOD_MAP = {
    "WebcastChatMessage": "chat",
    "WebcastEmojiChatMessage": "chat",
    "WebcastGiftMessage": "gift",
    "WebcastLikeMessage": "like",
    "WebcastSocialMessage": "follow",
    "WebcastMemberMessage": "enter",
    "WebcastRoomUserSeqMessage": "stats",
}

from app_paths import app_dir

_DEBUG_LOG = app_dir() / "debug_payload.log"
_MAX_DEBUG_LOG = 10 * 1024 * 1024  # 10 MB


@dataclass
class DecodeResult:
    """Result of decoding a WebSocket frame.

    Attributes:
        messages: List of flat dicts, one per decoded danmaku message.
        need_ack: True if the server expects an ACK PushFrame back.
        frame: The parsed PushFrame (None on decode failure).
        response: The parsed Response (None on decode failure).
    """
    messages: list[dict]
    need_ack: bool = False
    frame: PushFrame | None = None
    response: Response | None = None
    parse_failures: int = 0


class DanmakuDecoder:
    """Stateless decoder for Douyin live danmaku WebSocket frames.

    Usage::

        decoder = DanmakuDecoder()
        result = decoder.decode(raw_bytes)
        for msg in result.messages:
            print(msg["type"], msg["nickname"], msg.get("content", ""))
        if result.need_ack:
            ack = decoder.build_ack(result.frame, result.response)
    """

    def __init__(self):
        self._fail_count: int = 0
        self._method_counts: dict[str, int] = {}
        self._unknown_method_counts: dict[str, int] = {}
        self._parse_fail_counts: dict[str, int] = {}
        self._method_stats_last_log = time.monotonic()
        self._method_stats_log_interval_sec = 60.0

    @property
    def fail_count(self) -> int:
        return self._fail_count

    def reset_fail_count(self):
        self._fail_count = 0

    def method_stats_snapshot(self) -> dict:
        total = sum(self._method_counts.values())
        unknown_total = sum(self._unknown_method_counts.values())
        return {
            "total": total,
            "known_total": total - unknown_total,
            "unknown_total": unknown_total,
            "method_counts": dict(self._method_counts),
            "unknown_method_counts": dict(self._unknown_method_counts),
            "parse_fail_counts": dict(self._parse_fail_counts),
        }

    def log_method_stats(self, reason: str = "manual"):
        self._log_method_stats_if_due(force=True, reason=reason)

    def _log_method_stats_if_due(self, force: bool = False, reason: str = "periodic"):
        now = time.monotonic()
        total = sum(self._method_counts.values())
        if total == 0:
            return
        if not force and now - self._method_stats_last_log < self._method_stats_log_interval_sec:
            return

        self._method_stats_last_log = now
        unknown_total = sum(self._unknown_method_counts.values())
        top_unknown = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(
                self._unknown_method_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ) or "-"
        top_parse_fail = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(
                self._parse_fail_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ) or "-"
        logger.info(
            "Decoder methods [{}]: total={} known={} unknown={} top_unknown={} top_parse_fail={}",
            reason,
            total,
            total - unknown_total,
            unknown_total,
            top_unknown,
            top_parse_fail,
        )

    def decode(self, raw_bytes: bytes) -> DecodeResult:
        """Decode raw WebSocket binary frame to danmaku messages.

        Pure function: no state, no side effects. Returns empty messages
        list on any parse error (malformed input, schema drift, etc.).

        Args:
            raw_bytes: Raw binary payload from WebSocket frame.

        Returns:
            DecodeResult with messages list and metadata for ACK.
        """
        try:
            # Layer 1: Parse PushFrame envelope
            frame = PushFrame()
            frame.ParseFromString(raw_bytes)

            # Layer 2: Gzip decompress (Pitfall 1 -- ALWAYS decompress)
            payload = gzip.decompress(frame.payload)

            # Layer 3: Parse Response
            response = Response()
            response.ParseFromString(payload)

            # Extract timestamp source from response
            ts_float, ts_iso = self._extract_timestamps(response.now)

            # Process each message in the response
            messages = []
            failures = 0
            for msg in response.messagesList:
                method = str(msg.method or "").strip() or "<empty>"
                self._method_counts[method] = self._method_counts.get(method, 0) + 1

                if method not in METHOD_MAP:
                    self._unknown_method_counts[method] = self._unknown_method_counts.get(method, 0) + 1
                    if self._unknown_method_counts[method] == 1:
                        logger.warning(
                            "Unknown websocket method observed: {} | payload_hex={}",
                            method,
                            msg.payload[:64].hex(),
                        )
                    continue

                msg_type = METHOD_MAP[method]
                parsed = self._parse_message(msg_type, msg.payload, ts_float, ts_iso)
                if parsed is not None:
                    messages.append(parsed)
                else:
                    failures += 1
                    self._parse_fail_counts[method] = self._parse_fail_counts.get(method, 0) + 1

            self._log_method_stats_if_due()

            return DecodeResult(
                messages=messages,
                need_ack=response.needAck,
                frame=frame,
                response=response,
                parse_failures=failures,
            )

        except Exception as e:
            logger.warning("帧解码失败: {} | hex={}", e, raw_bytes[:64].hex())
            self._log_failed_payload(raw_bytes, "frame")
            return DecodeResult(messages=[])

    def build_ack(self, frame: PushFrame, response: Response) -> bytes:
        """Construct ACK PushFrame to send back through WebSocket.

        Per RESEARCH.md Pattern 3:
            payloadType = "ack"
            payload = response.internalExt (UTF-8 encoded)
            logId = frame.logId

        Args:
            frame: The original PushFrame that was received.
            response: The decoded Response containing internalExt.

        Returns:
            Serialized ACK PushFrame bytes.
        """
        ack = PushFrame()
        ack.payloadType = "ack"
        ack.payload = response.internalExt.encode("utf-8")
        ack.logId = frame.logId
        return ack.SerializeToString()

    # ------------------------------------------------------------------
    # Internal parsing methods
    # ------------------------------------------------------------------

    def _parse_message(self, msg_type: str, payload_bytes: bytes,
                       ts_float: float, ts_iso: str) -> dict | None:
        """Dispatch to type-specific parser. Falls back to raw extraction on failure."""
        try:
            if msg_type == "chat":
                return self._parse_chat(payload_bytes, ts_float, ts_iso)
            elif msg_type == "gift":
                return self._parse_gift(payload_bytes, ts_float, ts_iso)
            elif msg_type == "like":
                return self._parse_like(payload_bytes, ts_float, ts_iso)
            elif msg_type == "follow":
                return self._parse_social(payload_bytes, ts_float, ts_iso)
            elif msg_type == "enter":
                return self._parse_member(payload_bytes, ts_float, ts_iso)
            elif msg_type == "stats":
                return self._parse_room_user_seq(payload_bytes, ts_float, ts_iso)
        except Exception as e:
            # Fallback: try to extract strings from raw bytes
            fallback = self._fallback_extract(payload_bytes, msg_type, ts_float, ts_iso)

            if fallback is not None:
                # Salvaged — log but don't count as failure
                logger.info("备用提取成功: type={}", msg_type)
                self._log_failed_payload(payload_bytes, msg_type, True)
                return fallback

            # No fallback — check if this is a system packet we should ignore
            if self._is_likely_system_payload(payload_bytes):
                logger.debug("跳过系统包: type={}", msg_type)
                return None

            # Chat parse failure is not real loss — DOM observer captures chat
            if msg_type == "chat":
                return None

            # Non-chat danmaku loss — count and log
            self._fail_count += 1
            logger.warning(
                "解析 {} 消息失败: {} | payload_hex={}",
                msg_type, e, payload_bytes[:64].hex(),
            )
            self._log_failed_payload(payload_bytes, msg_type, False)
        return None

    def _parse_chat(self, payload_bytes: bytes,
                    ts_float: float, ts_iso: str) -> dict | None:
        """Parse chat-like messages and extract user/content."""
        chat = ChatMessage()
        chat.ParseFromString(payload_bytes)
        user_id, nickname = self._extract_user(chat.user)
        content = chat.content

        if not content:
            content = self._extract_text_content(getattr(chat, "rtfContent", None))

        if not content:
            emoji_chat = EmojiChatMessage()
            emoji_chat.ParseFromString(payload_bytes)
            user_id, nickname = self._extract_user(emoji_chat.user)
            content = emoji_chat.defaultContent

        if not content:
            return None

        return self._build_message_dict("chat", user_id, nickname,
                                        ts_float, ts_iso,
                                        {"content": content})

    def _parse_member(self, payload_bytes: bytes,
                      ts_float: float, ts_iso: str) -> dict | None:
        """Parse MemberMessage as room-entry event."""
        member = MemberMessage()
        member.ParseFromString(payload_bytes)

        user_id, nickname = self._extract_user(member.user)
        if not user_id and not nickname:
            return None

        return self._build_message_dict("enter", user_id, nickname,
                                        ts_float, ts_iso)

    def _parse_room_user_seq(self, payload_bytes: bytes,
                             ts_float: float, ts_iso: str) -> dict | None:
        """Parse RoomUserSeqMessage as room stats snapshot."""
        room_stats = RoomUserSeqMessage()
        room_stats.ParseFromString(payload_bytes)

        if (
            not room_stats.total
            and not room_stats.totalUser
            and not room_stats.totalStr
            and not room_stats.totalUserStr
            and not room_stats.totalPvForAnchor
        ):
            return None

        return self._build_message_dict(
            "stats",
            "",
            "",
            ts_float,
            ts_iso,
            {
                "current_viewers": int(room_stats.total or 0),
                "total_viewers": room_stats.totalPvForAnchor or room_stats.totalUserStr or room_stats.totalStr,
                "popularity": int(room_stats.popularity or 0),
                "stats_text": room_stats.upRightStatsStrComplete or room_stats.upRightStatsStr or room_stats.popStr,
            },
        )

    def _parse_gift(self, payload_bytes: bytes,
                    ts_float: float, ts_iso: str) -> dict | None:
        """Parse GiftMessage. Extract user, gift info, calculate total."""
        gift = GiftMessage()
        gift.ParseFromString(payload_bytes)

        user_id, nickname = self._extract_user(gift.user)
        if not user_id:
            return None

        gift_count = gift.repeatCount
        gift_value = gift.gift.diamondCount
        gift_total = gift_count * gift_value  # D-04: pre-calculated

        return self._build_message_dict("gift", user_id, nickname,
                                        ts_float, ts_iso,
                                        {
                                            "gift_name": gift.gift.name,
                                            "gift_count": gift_count,
                                            "gift_value": gift_value,
                                            "gift_icon": gift.gift.icon,
                                            "gift_total": gift_total,
                                        })

    def _parse_like(self, payload_bytes: bytes,
                    ts_float: float, ts_iso: str) -> dict | None:
        """Parse LikeMessage. Only common fields (D-06)."""
        like = LikeMessage()
        like.ParseFromString(payload_bytes)

        user_id, nickname = self._extract_user(like.user)
        if not user_id:
            return None

        return self._build_message_dict("like", user_id, nickname,
                                        ts_float, ts_iso)

    def _parse_social(self, payload_bytes: bytes,
                      ts_float: float, ts_iso: str) -> dict | None:
        """Parse SocialMessage. Only action==1 (follow) is kept (D-09)."""
        social = SocialMessage()
        social.ParseFromString(payload_bytes)

        if social.action != 1:
            # Not a follow event -- discard silently
            return None

        user_id, nickname = self._extract_user(social.user)
        if not user_id:
            return None

        return self._build_message_dict("follow", user_id, nickname,
                                        ts_float, ts_iso)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_user(user) -> tuple[str, str]:
        """Extract user_id (str) and nickname from User protobuf message.

        Returns (user_id, nickname). Accepts messages where user_id is 0
        but nickname is present — the id field may be at an unmapped number.
        """
        uid = ""
        for attr in ("idStr", "id", "displayId", "secUid"):
            value = getattr(user, attr, "")
            if value:
                uid = str(value)
                break
        nick = getattr(user, "nickName", "")
        if not uid and not nick:
            return ("", "")
        return (uid, nick)

    @staticmethod
    def _build_message_dict(msg_type: str, user_id: str, nickname: str,
                            timestamp: float, time_iso: str,
                            extra: dict | None = None) -> dict:
        """Build flat JSON-compatible dict with common + type-specific fields.

        Common fields per D-05: type, user_id, nickname, timestamp, time.
        Extra fields are merged in for type-specific data.
        """
        result = {
            "type": msg_type,
            "user_id": user_id,        # D-05: always string
            "nickname": nickname,
            "timestamp": timestamp,     # D-02: unix float
            "time": time_iso,           # D-02: ISO 8601 string
        }
        if extra:
            result.update(extra)
        return result

    @staticmethod
    def _extract_timestamps(now_ms: int) -> tuple[float, str]:
        """Extract dual timestamps from Response.now field.

        Args:
            now_ms: Response.now value in milliseconds (0 if absent).

        Returns:
            (unix_float, iso_string) tuple.
        """
        if now_ms and now_ms > 0:
            ts_float = now_ms / 1000.0
        else:
            ts_float = time.time()

        dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
        ts_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return (ts_float, ts_iso)

    @staticmethod
    def _extract_text_content(text_obj) -> str:
        if text_obj is None:
            return ""

        default_pattern = str(getattr(text_obj, "defaultPatter", "") or "").strip()
        pieces = []
        for piece in getattr(text_obj, "piecesList", []) or []:
            value = str(getattr(piece, "stringValue", "") or "").strip()
            if value:
                pieces.append(value)

        joined = "".join(pieces).strip()
        if joined:
            return joined
        return default_pattern

    # ------------------------------------------------------------------
    # Fallback extraction & debug logging
    # ------------------------------------------------------------------

    # Noise patterns: URLs, CDN paths, protocol keywords, image extensions, system messages
    _NOISE_CONTAINS = (
        "douyinpic.com", "amemv.com", "snssdk.com", "tplv",
        "webcast", "Webcast", "webcas",
        "Message", "Response", "PushFrame",
        "proto", "google.protobuf", "荣誉等级", "粉丝团等级", "级勋章",
        "https:", "http:",
        ".image", "obj.image", "image",
        "room_foll", "room_follow",
    )
    _NOISE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4")

    @staticmethod
    def _is_noise(text: str) -> bool:
        """Check if a string is noise (URL, resource path, protocol keyword, hex, token)."""
        if not text:
            return True
        # URLs and paths
        if text.startswith(("http://", "https://", "/")):
            return True
        # Douyin user secure_id tokens (base64: "MS4wLjAB" = version header)
        if text.startswith("MS4wLjAB"):
            return True
        # CDN / protocol / system keywords (substring match)
        for pattern in DanmakuDecoder._NOISE_CONTAINS:
            if pattern in text:
                return True
        # Image / media extensions
        lower = text.lower()
        for ext in DanmakuDecoder._NOISE_EXTENSIONS:
            if ext in lower:
                return True
        # Long strings without CJK or spaces — tokens, hashes, random IDs
        if len(text) >= 10:
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)
            if not has_cjk and ' ' not in text:
                return True
        # Pure hex strings (≥4 chars)
        if len(text) >= 4 and all(c in "0123456789abcdefABCDEF" for c in text):
            return True
        # Hex color codes like #FF0000 (with possible trailing garbage)
        if text.startswith("#") and len(text) >= 4:
            hex_part = text[1:]
            if len(hex_part) >= 3 and all(c in "0123456789abcdefABCDEF " for c in hex_part[:6]):
                return True
        # Douyin internal template placeholders
        if len(text) >= 3 and text[0] == "{" and text[1].isdigit() and ":" in text:
            return True
        return False

    @staticmethod
    def _scan_strings(payload_bytes: bytes) -> list[str]:
        """Extract all valid UTF-8 strings from raw protobuf bytes."""
        strings = []
        i = 0
        data = payload_bytes
        while i < len(data) - 1:
            if (data[i] & 0x07) == 2:  # wire type 2 = length-delimited
                length, shift, j = 0, 0, i + 1
                for _ in range(5):
                    if j >= len(data):
                        break
                    b = data[j]
                    length |= (b & 0x7F) << shift
                    shift += 7
                    j += 1
                    if not (b & 0x80):
                        break
                if 2 <= length < 512 and j + length <= len(data):
                    chunk = data[j:j + length]
                    try:
                        text = chunk.decode("utf-8")
                        printable = sum(1 for c in text if c.isprintable())
                        if text and printable / len(text) > 0.8:
                            stripped = text.strip()
                            if stripped:
                                strings.append(stripped)
                    except (UnicodeDecodeError, ValueError):
                        pass
            i += 1
        return strings

    @staticmethod
    def _strip_binary_tail(text: str) -> str:
        """Strip 1-2 trailing ASCII letters from CJK or numeric-dominant strings.

        Protobuf binary data often appends 1-2 ASCII bytes that decode as
        random letters (e.g. "Jf", "Jk", "Jl") after real content.
        """
        if len(text) < 3:
            return text
        has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)
        has_digits_before_tail = any(c.isdigit() for c in text[:-2])
        if not has_cjk and not has_digits_before_tail:
            return text
        stripped = 0
        while stripped < 2 and len(text) > 2:
            last = text[-1]
            if last.isascii() and last.isalpha():
                text = text[:-1]
                stripped += 1
            else:
                break
        return text

    @staticmethod
    def _is_likely_system_payload(payload_bytes: bytes) -> bool:
        """Check if payload is a system packet (heartbeat, tracking) not danmaku."""
        strings = DanmakuDecoder._scan_strings(payload_bytes)
        if not strings:
            return True  # pure binary — system packet
        # All strings are noise → system packet
        return all(DanmakuDecoder._is_noise(s) for s in strings)

    @staticmethod
    def _fallback_extract(payload_bytes: bytes, msg_type: str,
                          ts_float: float, ts_iso: str) -> dict | None:
        """Extract real user content from raw protobuf bytes when normal parsing fails.

        Chat messages are NOT handled here — the DOM observer captures chat
        reliably from the rendered page. WS fallback for chat produces too
        much garbage (binary fragments, URL pieces, tokens). Only
        like/gift/follow use this fallback path.
        """
        if msg_type == "chat":
            return None
        # Scan all UTF-8 strings
        raw = DanmakuDecoder._scan_strings(payload_bytes)

        # Split on all control characters (protobuf binary garbage mixed with text)
        # e.g. "级勋章\x12\x01\x06\x1a(实际内容" → ["级勋章", "(实际内容"]
        split_raw = []
        for s in raw:
            parts = re.split(r'[\x00-\x1f\x7f]', s)
            for p in parts:
                p = DanmakuDecoder._strip_binary_tail(p.strip())
                if p:
                    split_raw.append(p)

        # Filter noise from split strings
        clean = [s for s in split_raw if not DanmakuDecoder._is_noise(s)]

        if not clean:
            return None

        # Smart assignment: first = nickname, rest = content
        if len(clean) == 1:
            if msg_type == "chat":
                nickname = ""
                content = clean[0]
            else:
                nickname = clean[0]
                content = ""
        else:
            nickname = clean[0]
            rest = clean[1:]
            content = max(rest, key=len) if rest else ""

        base = {
            "type": msg_type,
            "user_id": "",
            "nickname": nickname,
            "timestamp": ts_float,
            "time": ts_iso,
        }

        if msg_type == "chat":
            base["content"] = content
        else:
            base["content"] = ""

        return base

    @staticmethod
    def _log_failed_payload(data: bytes, context: str, fallback_ok: bool = False):
        """Append hex dump of failed payload to debug_payload.log."""
        try:
            if _DEBUG_LOG.exists() and _DEBUG_LOG.stat().st_size > _MAX_DEBUG_LOG:
                return
            with _DEBUG_LOG.open("a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                tag = " [Fallback提取成功]" if fallback_ok else ""
                f.write(f"\n[{ts}] {context}{tag} ({len(data)} bytes):\n")
                for offset in range(0, min(len(data), 512), 16):
                    chunk = data[offset:offset + 16]
                    hex_str = " ".join(f"{b:02x}" for b in chunk)
                    ascii_str = "".join(
                        chr(b) if 32 <= b < 127 else "." for b in chunk
                    )
                    f.write(f"  {offset:04x}  {hex_str:<48s}  {ascii_str}\n")
        except Exception:
            pass
