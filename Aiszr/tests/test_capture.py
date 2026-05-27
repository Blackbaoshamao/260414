"""Tests for capture.py RoomCapture class.

Unit tests using mocked Playwright objects to verify:
- WS interception setup and navigation
- Binary/text frame discrimination
- Decoder integration and message callback
- ACK sending
- Auto-reconnection with exponential backoff
- Stop/cleanup behavior
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decoder import DanmakuDecoder, DecodeResult
from proto_defs import PushFrame, Response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_decoder():
    """DanmakuDecoder mock that returns configurable DecodeResult."""
    decoder = MagicMock(spec=DanmakuDecoder)
    decoder.decode = MagicMock(
        return_value=DecodeResult(messages=[], need_ack=False)
    )
    decoder.build_ack = MagicMock(return_value=b"\x08\x01")  # minimal protobuf
    return decoder


@pytest.fixture
def mock_ws():
    """AsyncMock for a Playwright WebSocket object."""
    ws = AsyncMock()
    ws.url = "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/"
    ws.on = MagicMock()
    ws.send = MagicMock()
    return ws


@pytest.fixture
def mock_page_for_capture():
    """AsyncMock page configured for capture tests."""
    page = AsyncMock()
    page.url = "https://live.douyin.com/814261282984"
    page.goto = AsyncMock()
    page.reload = AsyncMock()
    page.close = AsyncMock()
    page.on = MagicMock()  # non-async event registration
    page.evaluate = AsyncMock(return_value="7123456789012345678")
    return page


@pytest.fixture
def mock_context_for_capture(mock_page_for_capture):
    """AsyncMock browser context that returns the capture page."""
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=mock_page_for_capture)
    return context


@pytest.fixture(autouse=True)
def _skip_ws_wait():
    """Skip the 30s WebSocket wait in start() for unit tests."""
    async def _instant_wait(coro, timeout=None):
        coro.close()
        return None
    with patch("capture.asyncio.wait_for", side_effect=_instant_wait):
        yield


# ---------------------------------------------------------------------------
# Test: start() navigates to room URL and sets up WS listener
# ---------------------------------------------------------------------------


class TestStart:
    """Verify start() sets up WebSocket listener and navigates correctly."""

    async def test_start_creates_page_and_navigates(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """start() creates a new page and navigates to the room URL."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        # Page was created from context
        mock_context_for_capture.new_page.assert_called_once()

        # WS listener was set up BEFORE navigation
        mock_page_for_capture.on.assert_called_once()
        args = mock_page_for_capture.on.call_args
        assert args[0][0] == "websocket"

        # Navigation happened
        mock_page_for_capture.goto.assert_called_once_with(
            "https://live.douyin.com/814261282984",
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Running flag is set
        assert capture.running is True

        await capture.stop()

    async def test_start_stores_callback(
        self, mock_context_for_capture, mock_decoder
    ):
        """start() stores the on_message callback."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        assert capture._message_callback is on_msg
        await capture.stop()


# ---------------------------------------------------------------------------
# Test: _on_ws_open() only attaches to webcast WebSocket URLs
# ---------------------------------------------------------------------------


class TestOnWsOpen:
    """Verify _on_ws_open filters by URL and attaches frame listener."""

    async def test_webcast_ws_is_monitored(
        self, mock_context_for_capture, mock_decoder, mock_ws
    ):
        """WebSocket URL containing 'webcast' is monitored."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        # Simulate WS open event
        handler = capture._on_ws_open
        await handler(mock_ws)

        assert capture._ws is mock_ws
        # Should have registered framereceived and close listeners
        assert mock_ws.on.call_count == 2
        call_args = [c[0][0] for c in mock_ws.on.call_args_list]
        assert "framereceived" in call_args
        assert "close" in call_args

        await capture.stop()

    async def test_non_webcast_ws_is_ignored(
        self, mock_context_for_capture, mock_decoder
    ):
        """WebSocket URL without 'webcast' is ignored."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        other_ws = AsyncMock()
        other_ws.url = "wss://some-analytics.example.com/track"

        await capture._on_ws_open(other_ws)

        assert capture._ws is None  # not stored
        other_ws.on.assert_not_called()  # no listeners attached

        await capture.stop()


# ---------------------------------------------------------------------------
# Test: _on_frame_received() passes binary frames to decoder
# ---------------------------------------------------------------------------


class TestOnFrameReceived:
    """Verify binary frame handling, decoder integration, and callback."""

    async def test_binary_frame_decoded_and_callback_called(
        self, mock_context_for_capture, mock_decoder
    ):
        """Binary frame is passed to decoder; non-chat messages trigger callback."""
        from capture import RoomCapture

        follow_msg = {
            "type": "follow",
            "user_id": "123",
            "nickname": "test_user",
            "timestamp": 1713140234.0,
            "time": "2024-04-15T02:30:34",
        }
        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(messages=[follow_msg], need_ack=False)
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        # Simulate receiving a binary frame
        await capture._on_frame_received(b"\x08\x01\x12\x02test")

        # Decoder was called with the raw bytes
        mock_decoder.decode.assert_called_once_with(b"\x08\x01\x12\x02test")

        # Callback was invoked with the decoded follow message
        on_msg.assert_called_once_with(follow_msg)

    async def test_chat_forwarded_from_ws(
        self, mock_context_for_capture, mock_decoder
    ):
        """Chat messages from WS protobuf are forwarded to callback."""
        from capture import RoomCapture

        chat_msg = {
            "type": "chat",
            "user_id": "123",
            "nickname": "test_user",
            "content": "hello",
            "timestamp": 1713140234.0,
            "time": "2024-04-15T02:30:34",
        }
        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(messages=[chat_msg], need_ack=False)
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_frame_received(b"\x08\x01\x12\x02test")

        # Chat message IS forwarded (WS protobuf path)
        on_msg.assert_called_once_with(chat_msg)

        await capture.stop()

    async def test_text_frame_ignored(
        self, mock_context_for_capture, mock_decoder
    ):
        """Text frames (str payloads) are silently ignored."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        # Simulate receiving a text frame
        await capture._on_frame_received("some text data")

        # Decoder should NOT be called
        mock_decoder.decode.assert_not_called()
        on_msg.assert_not_called()

        await capture.stop()

    async def test_ack_sent_when_needed(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """ACK is sent via page.evaluate when decoder signals need_ack=True."""
        from capture import RoomCapture

        # Create real PushFrame and Response for ACK construction
        frame = PushFrame()
        frame.logId = 42
        response = Response()
        response.internalExt = "test_ext"

        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(
                messages=[],
                need_ack=True,
                frame=frame,
                response=response,
            )
        )
        mock_decoder.build_ack = MagicMock(return_value=b"\x08*\x12\x08test_ext")
        # page.evaluate returns True (ACK sent successfully)
        mock_page_for_capture.evaluate = AsyncMock(return_value=True)

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_frame_received(b"\x08\x01")

        # build_ack was called with the frame and response
        mock_decoder.build_ack.assert_called_once_with(frame, response)

        # ACK was sent via page.evaluate (not ws.send)
        mock_page_for_capture.evaluate.assert_called_once()
        call_args = mock_page_for_capture.evaluate.call_args
        assert "ack_b64" in call_args[0][1] or "WebSocket.OPEN" in call_args[0][0]

        await capture.stop()

    async def test_ack_failure_does_not_crash(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """ACK send failure logs warning but does not crash the capture."""
        from capture import RoomCapture

        frame = PushFrame()
        frame.logId = 42
        response = Response()
        response.internalExt = "test_ext"

        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(
                messages=[],
                need_ack=True,
                frame=frame,
                response=response,
            )
        )
        mock_decoder.build_ack = MagicMock(return_value=b"\x08*\x12\x08test_ext")
        # page.evaluate raises an error
        mock_page_for_capture.evaluate = AsyncMock(
            side_effect=RuntimeError("page closed")
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        # Should not raise
        await capture._on_frame_received(b"\x08\x01")

        # Capture should still be running
        assert capture.running is True

        await capture.stop()

    async def test_multiple_messages_in_one_frame(
        self, mock_context_for_capture, mock_decoder
    ):
        """Multiple messages from a single frame are all forwarded."""
        from capture import RoomCapture

        msgs = [
            {"type": "chat", "user_id": "1", "nickname": "a", "content": "hi"},
            {"type": "like", "user_id": "2", "nickname": "b"},
            {"type": "gift", "user_id": "3", "nickname": "c", "gift_name": "rose"},
        ]
        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(messages=msgs, need_ack=False)
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_frame_received(b"\x08\x01")

        # Chat + like + gift all forwarded
        assert on_msg.call_count == 3
        assert on_msg.call_args_list[0][0][0] == msgs[0]  # chat
        assert on_msg.call_args_list[1][0][0] == msgs[1]  # like
        assert on_msg.call_args_list[2][0][0] == msgs[2]  # gift

        await capture.stop()


class TestDomMessage:
    """Verify DOM fallback only forwards allowed live-room events."""

    async def test_dom_noise_message_is_dropped(
        self, mock_context_for_capture, mock_decoder
    ):
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_dom_message(None, "", "小时榜")
        on_msg.assert_not_called()

        await capture.stop()

    async def test_dom_enter_message_is_classified(
        self, mock_context_for_capture, mock_decoder
    ):
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_dom_message(None, "欣欣向荣hg", "来了")

        on_msg.assert_called_once()
        msg = on_msg.call_args[0][0]
        assert msg["type"] == "enter"
        assert msg["nickname"] == "欣欣向荣hg"
        assert msg["content"] == ""

        await capture.stop()

    async def test_dom_complete_chat_is_forwarded(
        self, mock_context_for_capture, mock_decoder
    ):
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_dom_message(None, "测试用户", "售后怎么处理")

        on_msg.assert_called_once()
        msg = on_msg.call_args[0][0]
        assert msg["type"] == "chat"
        assert msg["nickname"] == "测试用户"
        assert msg["content"] == "售后怎么处理"

        await capture.stop()

    async def test_dom_chat_is_suppressed_after_ws_complete_match(
        self, mock_context_for_capture, mock_decoder
    ):
        from capture import RoomCapture

        chat_msg = {
            "type": "chat",
            "user_id": "123",
            "nickname": "测试用户",
            "content": "售后怎么处理",
            "timestamp": 1713140234.0,
            "time": "2024-04-15T02:30:34",
        }
        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(messages=[chat_msg], need_ack=False)
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_frame_received(b"\x08\x01")
        await capture._on_dom_message(None, "测试用户", "售后怎么处理")

        on_msg.assert_called_once_with(chat_msg)

        await capture.stop()

    async def test_dom_complete_chat_complements_incomplete_ws_chat(
        self, mock_context_for_capture, mock_decoder
    ):
        from capture import RoomCapture

        ws_incomplete = {
            "type": "chat",
            "user_id": "",
            "nickname": "",
            "content": "售后怎么处理",
            "timestamp": 1713140234.0,
            "time": "2024-04-15T02:30:34",
        }
        mock_decoder.decode = MagicMock(
            return_value=DecodeResult(messages=[ws_incomplete], need_ack=False)
        )

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        on_msg = AsyncMock()
        await capture.start(on_msg)

        await capture._on_frame_received(b"\x08\x01")
        on_msg.assert_not_called()

        await capture._on_dom_message(None, "测试用户", "售后怎么处理")

        on_msg.assert_called_once()
        msg = on_msg.call_args[0][0]
        assert msg["type"] == "chat"
        assert msg["nickname"] == "测试用户"
        assert msg["content"] == "售后怎么处理"

        await capture.stop()


# ---------------------------------------------------------------------------
# Test: _on_ws_close() triggers reconnection when running
# ---------------------------------------------------------------------------


class TestOnWsClose:
    """Verify WS close triggers reconnection or stops capture."""

    async def test_close_triggers_reconnect_when_running(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """When capture is running, WS close triggers reconnection."""
        from capture import RoomCapture

        # Make reload succeed immediately
        mock_page_for_capture.reload = AsyncMock()

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())
        assert capture.running is True

        # Patch _reconnect to verify it's called
        reconnect_called = False
        original_reconnect = capture._reconnect

        async def mock_reconnect():
            nonlocal reconnect_called
            reconnect_called = True
            return True

        capture._reconnect = mock_reconnect

        await capture._on_ws_close()

        assert reconnect_called is True
        await capture.stop()

    async def test_close_does_nothing_when_not_running(
        self, mock_context_for_capture, mock_decoder
    ):
        """When capture is stopped, WS close does not trigger reconnection."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        # Don't start -- running stays False
        assert capture.running is False

        # _on_ws_close should do nothing
        await capture._on_ws_close()

        # No page reload should happen
        assert capture._reconnect_attempts == 0


# ---------------------------------------------------------------------------
# Test: stop() sets running to False and closes the page
# ---------------------------------------------------------------------------


class TestStop:
    """Verify stop() cleans up resources."""

    async def test_stop_cleans_up(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """stop() sets running=False, closes page, clears WS reference."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        # Set a WS reference
        capture._ws = AsyncMock()

        await capture.stop()

        assert capture.running is False
        assert capture._ws is None
        assert capture._page is None
        mock_page_for_capture.close.assert_called_once()

    async def test_stop_without_start(
        self, mock_context_for_capture, mock_decoder
    ):
        """stop() on a capture that was never started does not crash."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )

        # Should not raise
        await capture.stop()
        assert capture.running is False


# ---------------------------------------------------------------------------
# Test: reconnection uses exponential backoff
# ---------------------------------------------------------------------------


class TestReconnect:
    """Verify exponential backoff reconnection behavior."""

    async def test_reconnect_uses_exponential_backoff(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """Reconnect delays follow exponential pattern: 1, 2, 4, 8..."""
        from capture import RoomCapture

        # Make reload fail so all attempts are made
        mock_page_for_capture.reload = AsyncMock(side_effect=Exception("fail"))

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        capture._running = True
        capture._page = mock_page_for_capture  # page must exist for reload

        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        with patch("capture.asyncio.sleep", side_effect=mock_sleep):
            result = await capture._reconnect()

        # All 10 attempts exhausted
        assert result is False
        assert len(sleep_delays) == 10

        # Delays should be: 1, 2, 4, 8, 16, 32, 60, 60, 60, 60
        # (2^0=1, 2^1=2, 2^2=4, 2^3=8, 2^4=16, 2^5=32, min(2^6,60)=60, ...)
        expected = [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
        assert sleep_delays == expected

        # Running should be set to False after exhaustion
        assert capture.running is False

    async def test_reconnect_succeeds_on_retry(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder, mock_ws
    ):
        """Reconnect succeeds after page reload and WS reconnection."""
        from capture import RoomCapture

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        capture._running = True
        capture._page = mock_page_for_capture  # page must exist for reload

        # Track sleep calls
        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        with patch("capture.asyncio.sleep", side_effect=mock_sleep):
            # Set WS after reload to simulate reconnection
            async def simulate_reconnect(*args, **kwargs):
                capture._ws = mock_ws

            mock_page_for_capture.reload = AsyncMock(
                side_effect=simulate_reconnect
            )
            result = await capture._reconnect()

        assert result is True
        # One sleep: backoff delay (1s); WS wait uses asyncio.wait_for
        assert len(sleep_delays) == 1
        assert sleep_delays[0] == 1  # 2^0 = 1 (backoff)


# ---------------------------------------------------------------------------
# Test: get_room_id() extracts room ID from page
# ---------------------------------------------------------------------------


class TestGetRoomId:
    """Verify room ID extraction from page RENDER_DATA."""

    async def test_get_room_id_success(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """get_room_id returns the room ID from page.evaluate."""
        from capture import RoomCapture

        mock_page_for_capture.evaluate = AsyncMock(return_value="7123456789012345678")

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        room_id = await capture.get_room_id()
        assert room_id == "7123456789012345678"

        await capture.stop()

    async def test_get_room_id_failure_raises(
        self, mock_context_for_capture, mock_page_for_capture, mock_decoder
    ):
        """get_room_id raises ValueError when room_id is None."""
        from capture import RoomCapture

        mock_page_for_capture.evaluate = AsyncMock(return_value=None)

        capture = RoomCapture(
            mock_context_for_capture,
            "https://live.douyin.com/814261282984",
            mock_decoder,
        )
        await capture.start(AsyncMock())

        with pytest.raises(ValueError, match="Could not extract room_id"):
            await capture.get_room_id()

        await capture.stop()
