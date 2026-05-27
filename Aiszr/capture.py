"""WebSocket interception layer for Douyin live room danmaku capture.

Uses Playwright's passive WebSocket monitoring to intercept binary frames
from Douyin's webcast WebSocket, decode them through DanmakuDecoder,
and emit flat JSON dicts via async callback.

Features:
- Passive WS monitoring via page.on("websocket") + ws.on("framereceived")
- ACK heartbeat via injected JS (page.evaluate) to bypass passive WS limitation
- Auto-reload for stable WebSocket connection
- Exponential backoff auto-reconnection on WS close
- Only monitors WebSocket URLs containing "webcast"
"""
import asyncio
import base64
import inspect
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable, Coroutine, Optional

from loguru import logger

from decoder import DanmakuDecoder, DecodeResult


# Type alias for the async message callback
MessageCallback = Callable[[dict], Coroutine]


class RoomCapture:
    """Captures danmaku from a Douyin live room via Playwright WebSocket interception.

    Usage::

        capture = RoomCapture(context, room_url, decoder)
        await capture.start(on_message)
        # ... runs until stop() is called or Ctrl+C
        await capture.stop()
    """

    def __init__(self, context, room_url: str, decoder: DanmakuDecoder):
        """Initialize RoomCapture.

        Args:
            context: Playwright BrowserContext (authenticated).
            room_url: Full Douyin live room URL (e.g. https://live.douyin.com/814261282984).
            decoder: DanmakuDecoder instance for protobuf decode + ACK construction.
        """
        self._context = context
        self._room_url = room_url
        self._decoder = decoder
        self._page = None
        self._ws = None
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_in_progress = False
        self._message_callback: Optional[MessageCallback] = None
        self._ws_connected = asyncio.Event()
        self._recent_chat_signatures: dict[tuple[str, str], float] = {}
        self._pending_ws_chat: list[dict] = []
        self._chat_metrics = {
            "ws_chat_total": 0,
            "ws_chat_complete": 0,
            "ws_chat_missing_nickname": 0,
            "dom_chat_complete": 0,
            "dom_chat_promoted": 0,
            "chat_forwarded_total": 0,
            "chat_dropped_incomplete": 0,
        }
        self._chat_metrics_last_log = time.monotonic()
        self._chat_metrics_log_interval_sec = 60.0

    @property
    def running(self) -> bool:
        """Whether the capture is currently active."""
        return self._running

    def _is_page_available(self) -> bool:
        if self._page is None:
            return False
        try:
            checker = getattr(self._page, "is_closed", None)
            if checker is None:
                return True
            if callable(checker) and inspect.iscoroutinefunction(checker):
                return True
            value = checker() if callable(checker) else checker
            # In tests this may be an AsyncMock coroutine; treat as available.
            if asyncio.iscoroutine(value):
                try:
                    value.close()
                except Exception:
                    pass
                return True
            return not bool(value)
        except Exception:
            return True

    _PREFIXED_TEXT_RE = re.compile(r"^\s*(?P<nick>[^:：]{1,30})[:：]\s*(?P<body>.+?)\s*$")
    _TIME_PREFIX_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?\s")
    _CHAT_SIGNATURE_WINDOW_SEC = 2.0
    _CHAT_FUSION_WINDOW_SEC = 3.0
    _EVENT_SUFFIXES = (
        (
            "enter",
            (
                "来了",
                "来啦",
                "进入了直播间",
                "进入直播间",
                "进入了房间",
                "进入了直播",
            ),
        ),
        (
            "share",
            (
                "分享了直播间",
                "分享了直播",
                "分享直播间",
            ),
        ),
    )
    _UI_NOISE_KEYWORDS = (
        "小时榜",
        "人气榜",
        "精选",
        "推荐",
        "搜索",
        "朋友",
        "我的",
        "放映厅",
        "短剧",
        "小游戏",
        "充钻石",
        "客户端",
        "壁纸",
        "通知投稿",
        "最多显示",
        "切换失败",
    )

    @staticmethod
    def _normalize_dom_text(text: str) -> str:
        return " ".join((text or "").strip().split())

    @classmethod
    def _looks_like_ui_noise(cls, nickname: str, content: str) -> bool:
        combined = f"{nickname} {content}".strip()
        if not combined:
            return True
        if nickname.isdigit():
            return True
        if cls._TIME_PREFIX_RE.match(content):
            return True
        if any(keyword in combined for keyword in cls._UI_NOISE_KEYWORDS):
            return True
        tokens = combined.split()
        if len(tokens) >= 10 and not any(
            any("\u4e00" <= ch <= "\u9fff" or ch.isalnum() for ch in token)
            for token in tokens
        ):
            return True
        return False

    @classmethod
    def _extract_prefixed_dom_message(cls, nickname: str, content: str) -> tuple[str, str]:
        if nickname:
            return nickname, content
        match = cls._PREFIXED_TEXT_RE.match(content)
        if not match:
            return nickname, content
        return cls._normalize_dom_text(match.group("nick")), cls._normalize_dom_text(match.group("body"))

    @classmethod
    def _extract_event_from_content(cls, nickname: str, content: str) -> tuple[str, str] | None:
        for event_type, suffixes in cls._EVENT_SUFFIXES:
            for suffix in suffixes:
                if content == suffix:
                    return event_type, nickname
                if nickname and content.endswith(suffix):
                    return event_type, nickname
        return None

    @classmethod
    def _classify_dom_message(cls, nickname: str, content: str, ts_float: float, ts_iso: str) -> dict | None:
        nickname = cls._normalize_dom_text(nickname)
        content = cls._normalize_dom_text(content)
        nickname, content = cls._extract_prefixed_dom_message(nickname, content)

        if not content or "\n" in content:
            return None
        if cls._looks_like_ui_noise(nickname, content):
            return None

        raw_nickname = nickname
        display_nickname = "" if raw_nickname in {"神秘观众", "绁炵瑙備紬"} else raw_nickname

        event = cls._extract_event_from_content(raw_nickname, content)
        if event is not None:
            event_type, _ = event
            return {
                "type": event_type,
                "user_id": "",
                "nickname": display_nickname,
                "timestamp": ts_float,
                "time": ts_iso,
                "content": "",
            }

        if display_nickname and content:
            return {
                "type": "chat",
                "user_id": "",
                "nickname": display_nickname,
                "timestamp": ts_float,
                "time": ts_iso,
                "content": content,
            }

        return None

    @staticmethod
    def _normalize_chat_value(value: object) -> str:
        return " ".join(str(value or "").strip().split())

    @classmethod
    def _chat_signature(cls, msg: dict) -> tuple[str, str] | None:
        if msg.get("type") != "chat":
            return None
        nickname = cls._normalize_chat_value(msg.get("nickname", ""))
        content = cls._normalize_chat_value(msg.get("content", ""))
        if not nickname or not content:
            return None
        return (nickname.casefold(), content.casefold())

    def _prune_chat_buffers(self, now: float) -> None:
        expired = [
            item
            for item in self._pending_ws_chat
            if now - item["created_at"] >= self._CHAT_FUSION_WINDOW_SEC
        ]
        if expired:
            self._chat_metrics["chat_dropped_incomplete"] += len(expired)
        self._recent_chat_signatures = {
            signature: created_at
            for signature, created_at in self._recent_chat_signatures.items()
            if now - created_at < self._CHAT_SIGNATURE_WINDOW_SEC
        }
        self._pending_ws_chat = [
            item
            for item in self._pending_ws_chat
            if now - item["created_at"] < self._CHAT_FUSION_WINDOW_SEC
        ]

    def _drop_pending_ws_match(self, dom_msg: dict, now: float) -> None:
        content = self._normalize_chat_value(dom_msg.get("content", "")).casefold()
        if not content:
            return

        for index in range(len(self._pending_ws_chat) - 1, -1, -1):
            item = self._pending_ws_chat[index]
            if item["content"] != content:
                continue
            if now - item["created_at"] > self._CHAT_FUSION_WINDOW_SEC:
                continue
            self._pending_ws_chat.pop(index)
            return

    async def _emit_message(self, msg: dict) -> None:
        if msg.get("type") == "chat":
            self._chat_metrics["chat_forwarded_total"] += 1
            self._log_chat_metrics_if_due()
        if self._message_callback:
            await self._message_callback(msg)

    async def _handle_ws_chat_message(self, msg: dict) -> None:
        now = time.monotonic()
        self._prune_chat_buffers(now)
        self._chat_metrics["ws_chat_total"] += 1

        signature = self._chat_signature(msg)
        if signature is not None:
            self._chat_metrics["ws_chat_complete"] += 1
            if signature in self._recent_chat_signatures:
                return
            self._recent_chat_signatures[signature] = now
            await self._emit_message(msg)
            return

        content = self._normalize_chat_value(msg.get("content", ""))
        if not content:
            return
        self._chat_metrics["ws_chat_missing_nickname"] += 1
        self._pending_ws_chat.append(
            {
                "created_at": now,
                "content": content.casefold(),
            }
        )
        self._log_chat_metrics_if_due()

    async def _handle_dom_chat_message(self, msg: dict) -> None:
        now = time.monotonic()
        self._prune_chat_buffers(now)

        signature = self._chat_signature(msg)
        if signature is None:
            return
        self._chat_metrics["dom_chat_complete"] += 1
        if signature in self._recent_chat_signatures:
            return

        self._drop_pending_ws_match(msg, now)
        self._recent_chat_signatures[signature] = now
        self._chat_metrics["dom_chat_promoted"] += 1
        await self._emit_message(msg)

    def _log_chat_metrics_if_due(self, force: bool = False, reason: str = "periodic") -> None:
        now = time.monotonic()
        total_observed = (
            self._chat_metrics["ws_chat_total"]
            + self._chat_metrics["dom_chat_complete"]
        )
        if not force and now - self._chat_metrics_last_log < self._chat_metrics_log_interval_sec:
            return
        if total_observed == 0:
            return

        self._chat_metrics_last_log = now
        logger.info(
            "Chat metrics [{}]: ws_total={} ws_complete={} ws_missing_nick={} dom_complete={} dom_promoted={} forwarded={} dropped_incomplete={}",
            reason,
            self._chat_metrics["ws_chat_total"],
            self._chat_metrics["ws_chat_complete"],
            self._chat_metrics["ws_chat_missing_nickname"],
            self._chat_metrics["dom_chat_complete"],
            self._chat_metrics["dom_chat_promoted"],
            self._chat_metrics["chat_forwarded_total"],
            self._chat_metrics["chat_dropped_incomplete"],
        )

    async def start(self, on_message: MessageCallback) -> None:
        """Start capturing danmaku from the live room.

        Creates a new page, injects WebSocket reference capture script,
        sets up passive WS listener, navigates to room URL, then
        auto-reloads for stable WebSocket connection.

        Args:
            on_message: Async callable receiving a dict per decoded danmaku message.
        """
        self._message_callback = on_message

        # Create a new page in the authenticated context
        self._page = await self._context.new_page()

        # Block video/media streams to save bandwidth.
        # We only need the WebSocket connection for danmaku data, not the live video.
        # Do NOT block images/stylesheets/fonts — they're needed for page JS to init properly.
        async def _block_resources(route):
            if route.request.resource_type == "media":
                await route.abort()
            else:
                await route.continue_()

        await self._page.route("**/*", _block_resources)

        # Inject JS to capture WebSocket references for ACK sending.
        # Patch WebSocket constructor before any page JS runs.
        await self._page.add_init_script("""
            (() => {
                const OrigWS = window.WebSocket;
                window.__dydm_ws_list = [];
                window.WebSocket = function(url, protocols) {
                    const ws = new OrigWS(url, protocols);
                    if (url.includes('webcast')) {
                        window.__dydm_ws_list.push(ws);
                        ws.addEventListener('close', () => {
                            window.__dydm_ws_list = window.__dydm_ws_list.filter(w => w !== ws);
                        });
                    }
                    return ws;
                };
                window.WebSocket.prototype = OrigWS.prototype;
                window.WebSocket.CONNECTING = OrigWS.CONNECTING;
                window.WebSocket.OPEN = OrigWS.OPEN;
                window.WebSocket.CLOSING = OrigWS.CLOSING;
                window.WebSocket.CLOSED = OrigWS.CLOSED;
            })();
        """)

        # Expose Python callback for DOM-captured chat messages
        await self._page.expose_binding("on_dom_message", self._on_dom_message)

        # Inject DOM observer: multi-strategy chat container detection.
        # Falls back to body observation if no container found via selectors.
        await self._page.add_init_script("""
            (() => {
                const seen = new Set();
                const pending = new WeakMap();

                function send(nick, content) {
                    if (!window.on_dom_message) return;
                    const key = nick + '\\x01' + content;
                    if (seen.has(key)) return;
                    seen.add(key);
                    if (seen.size > 500) {
                        const arr = [...seen];
                        seen.clear();
                        arr.slice(-400).forEach(k => seen.add(k));
                    }
                    console.log('[Dydm] DOM chat:', nick || '(no-nick)', content);
                    window.on_dom_message(nick || '', content);
                }

                function queueExtract(node) {
                    if (!node) return;
                    let target = node.nodeType === 1 ? node : node.parentElement;
                    if (!target) return;

                    for (let i = 0; i < 4 && target.parentElement; i++) {
                        const text = (target.innerText || '').trim();
                        if (text && text.length <= 300) break;
                        target = target.parentElement;
                    }

                    const prev = pending.get(target);
                    if (prev) clearTimeout(prev);

                    const timer = setTimeout(() => {
                        pending.delete(target);
                        extractChat(target);
                    }, 180);
                    pending.set(target, timer);
                }

                function extractChat(node) {
                    if (node.nodeType !== 1) return;
                    const tag = node.tagName;
                    if (tag === 'SCRIPT' || tag === 'STYLE'
                        || tag === 'LINK' || tag === 'META') return;
                    const text = (node.innerText || '').trim();
                    if (!text || text.length > 300) return;

                    // Pattern 1: span children (nickname span + content spans)
                    const spans = node.querySelectorAll('span');
                    if (spans.length >= 2) {
                        const nick = spans[0].innerText.trim();
                        const rest = Array.from(spans).slice(1)
                            .map(s => s.innerText.trim()).filter(Boolean);
                        if (nick && rest.length > 0 && nick.length < 30) {
                            send(nick, rest.join(' '));
                            return;
                        }
                    }

                    // Pattern 2: direct children with short text
                    const kids = Array.from(node.children);
                    if (kids.length >= 2) {
                        const texts = kids
                            .map(c => (c.innerText || '').trim()).filter(Boolean);
                        if (texts.length >= 2 && texts[0].length < 30) {
                            send(texts[0], texts.slice(1).join(' '));
                            return;
                        }
                    }

                    // Pattern 3: single short text content
                    if (text.length > 0 && text.length < 200
                            && node.children.length <= 2) {
                        send('', text);
                    }
                }

                function startObserver(target, withCharData) {
                    console.log('[Dydm] Observing:', target.tagName,
                        (target.className || '').substring(0, 80));
                    const opts = { childList: true, subtree: true };
                    if (withCharData) opts.characterData = true;
                    new MutationObserver((mutations) => {
                        for (const m of mutations) {
                            if (m.type === 'childList') {
                                for (const node of m.addedNodes) {
                                    if (node.nodeType !== 1) continue;
                                    if ((node.innerText || '').length > 1000)
                                        continue;
                                    queueExtract(node);
                                }
                            } else if (m.type === 'characterData') {
                                const p = m.target.parentElement;
                                if (p) queueExtract(p);
                            }
                        }
                    }).observe(target, opts);
                }

                function findChatContainer() {
                    // Strategy 1: CSS selector match
                    const sels = [
                        '[class*="webcast-chatroom"] [class*="list"]',
                        '[class*="chatroom"] [class*="list"]',
                        '[class*="chat-room"] [class*="list"]',
                        '[class*="webcast-chatroom"]',
                        '[class*="chatroom"]',
                        '[class*="chat-room"]',
                        '[data-e2e="chat-list"]',
                    ];
                    for (const sel of sels) {
                        try {
                            const el = document.querySelector(sel);
                            if (el) {
                                console.log('[Dydm] Found via selector:', sel);
                                return el;
                            }
                        } catch(e) {}
                    }

                    // Strategy 2: scrollable div with many short-text children
                    let best = null, bestScore = 0;
                    document.querySelectorAll('div').forEach(div => {
                        try {
                            const style = getComputedStyle(div);
                            const ov = style.overflowY || style.overflow;
                            if (ov !== 'auto' && ov !== 'scroll') return;
                            const ch = div.children;
                            if (ch.length < 3) return;
                            let score = 0;
                            for (let i = 0; i < Math.min(ch.length, 20); i++) {
                                const t = (ch[i].innerText || '').trim();
                                if (t.length > 0 && t.length < 200) score++;
                            }
                            if (score > bestScore) {
                                bestScore = score;
                                best = div;
                            }
                        } catch(e) {}
                    });
                    if (best && bestScore >= 3) {
                        console.log('[Dydm] Found scrollable container, score:',
                            bestScore);
                        return best;
                    }
                    return null;
                }

                let done = false;
                function trySetup() {
                    if (done) return;
                    const c = findChatContainer();
                    if (c) { done = true; startObserver(c, true); }
                }

                [1000, 2000, 3000, 5000, 8000, 12000].forEach(
                    d => setTimeout(trySetup, d)
                );

                // Fallback: observe body after 15s if no container found
                setTimeout(() => {
                    if (!done) {
                        done = true;
                        console.log('[Dydm] No chat container found, observing body');
                        startObserver(document.body, false);
                    }
                }, 15000);
            })();
        """)

        # Set up passive WebSocket listener BEFORE navigation
        self._page.on("websocket", self._on_ws_open)

        # Navigate to the live room
        logger.info("Navigating to room: {}", self._room_url)
        try:
            await self._page.goto(
                self._room_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as e:
            logger.warning("Navigation warning (continuing): {}", e)

        # Auto-reload to ensure stable WebSocket connection
        logger.info("Auto-reloading for stable connection...")
        try:
            await self._page.reload(
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as e:
            logger.warning("Reload warning (continuing): {}", e)

        # Wait for the webcast WebSocket to be established by the page's JS
        self._running = True
        logger.info("Page loaded, waiting for WebSocket connection...")
        try:
            await asyncio.wait_for(self._ws_connected.wait(), timeout=30)
            logger.info("Room capture started for: {}", self._room_url)
        except asyncio.TimeoutError:
            logger.warning("No WebSocket detected within 30s, capture running anyway")

    async def _on_ws_open(self, ws) -> None:
        """Handle a new WebSocket connection opened by the page.

        Only attaches listeners to WebSocket URLs containing "douyin.com",
        which identifies Douyin's danmaku/push WebSocket. Other WebSocket
        connections (analytics, etc.) are ignored.
        """
        logger.info("WebSocket opened: {}", ws.url)
        if "douyin.com" not in ws.url:
            return

        logger.info("Danmaku WebSocket connected: {}", ws.url)
        self._ws = ws
        self._ws_connected.set()
        self._reconnect_attempts = 0  # reset on new connection

        ws.on("framereceived", self._on_frame_received)
        ws.on("close", self._on_ws_close)

    async def _on_frame_received(self, payload) -> None:
        """Handle a received WebSocket frame.

        Only processes binary frames (bytes). Chat messages are captured
        by the DOM observer — WS only emits like/gift/follow here.
        ACK responses are sent back when the decoder signals need_ack.
        """
        # Pitfall 4: Only binary frames are protobuf data
        if not isinstance(payload, bytes):
            return

        # Decode through the protobuf pipeline
        result: DecodeResult = self._decoder.decode(payload)

        # Use WebSocket as the primary danmaku/event channel.
        for msg in result.messages:
            msg = dict(msg)
            logger.debug("Danmaku: {}", json.dumps(msg, ensure_ascii=False))
            if msg.get("type") == "chat":
                await self._handle_ws_chat_message(msg)
            else:
                await self._emit_message(msg)

        # Send ACK if requested to keep connection alive (Pitfall 2)
        if result.need_ack and result.frame is not None and result.response is not None:
            await self._send_ack(result)

    async def _on_dom_message(self, source, nickname: str, content: str):
        """Handle DOM-captured chat/system message from injected MutationObserver."""
        if not self._message_callback:
            return

        ts_float = time.time()
        dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
        ts_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        msg = self._classify_dom_message(nickname, content, ts_float, ts_iso)
        if msg is None:
            return

        logger.debug("DOM message: {}", json.dumps(msg, ensure_ascii=False))
        if msg.get("type") == "chat":
            await self._handle_dom_chat_message(msg)
        else:
            await self._emit_message(msg)

    async def _send_ack(self, result: DecodeResult) -> None:
        """Build and send ACK response through the page's WebSocket.

        Uses page.evaluate() to send ACK bytes through the WebSocket
        reference captured by the injected init script. This bypasses
        Playwright's passive monitoring limitation (ws.send() not available).
        """
        try:
            ack_bytes = self._decoder.build_ack(result.frame, result.response)
            ack_b64 = base64.b64encode(ack_bytes).decode("ascii")

            sent = await self._page.evaluate("""(ack_b64) => {
                const bytes = Uint8Array.from(atob(ack_b64), c => c.charCodeAt(0));
                for (const ws of (window.__dydm_ws_list || [])) {
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(bytes.buffer);
                        return true;
                    }
                }
                return false;
            }""", ack_b64)

            if sent:
                logger.debug("ACK sent ({} bytes)", len(ack_bytes))
            else:
                logger.warning("No open WebSocket found for ACK")
        except Exception as e:
            logger.warning("Failed to send ACK: {}", e)

    async def _on_ws_close(self) -> None:
        """Handle WebSocket close event.

        If capture is still running, triggers reconnection with
        exponential backoff. If capture was stopped intentionally,
        does nothing.
        """
        logger.warning("WebSocket connection closed")
        if not self._running:
            return
        if self._reconnect_in_progress:
            logger.debug("Reconnect already in progress; skip duplicate close event")
            return
        await self._reconnect()

    async def _reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff.

        Uses page.reload() to trigger the page's own JavaScript to
        re-establish the WebSocket connection. The page.on("websocket")
        listener will automatically re-attach when the page reconnects.

        Backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s, 60s, ... (max 60s)

        Returns:
            True if reconnection succeeded, False if all attempts exhausted.
        """
        if not self._running:
            return False

        async with self._reconnect_lock:
            if self._reconnect_in_progress:
                logger.debug("Reconnect already in progress")
                return False
            self._reconnect_in_progress = True
            try:
                for attempt in range(self._max_reconnect_attempts):
                    if not self._running:
                        return False

                    delay = min(2 ** self._reconnect_attempts, 60)
                    self._reconnect_attempts += 1

                    logger.warning(
                        "Reconnecting in {}s (attempt {}/{})",
                        delay,
                        attempt + 1,
                        self._max_reconnect_attempts,
                    )
                    await asyncio.sleep(delay)

                    try:
                        self._ws = None
                        self._ws_connected.clear()

                        if not self._is_page_available():
                            logger.warning("Playwright page is closed, recreating room page before reconnect")
                            if self._message_callback is None:
                                logger.error("Reconnect aborted: message callback is missing")
                                self._running = False
                                return False
                            await self.start(self._message_callback)
                            if self._ws_connected.is_set():
                                logger.info("Reconnect recovered via full page recreate")
                                return True
                            logger.warning("Page recreated but WS not connected yet, retrying...")
                            continue

                        await self._page.reload(
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )

                        # Check if redirected to login page (session expired)
                        current_url = self._page.url
                        if "passport.douyin.com" in current_url or "sso.douyin.com" in current_url:
                            logger.error("Session expired - redirected to login page")
                            self._running = False
                            return False

                        logger.info("Page reloaded, waiting for WebSocket reconnection")
                        try:
                            await asyncio.wait_for(
                                self._ws_connected.wait(), timeout=15
                            )
                            return True
                        except asyncio.TimeoutError:
                            logger.warning("No WS after reload, retrying...")

                    except Exception as e:
                        logger.error("Reconnect attempt {} failed: {}", attempt + 1, e)

                # All attempts exhausted
                logger.error(
                    "All {} reconnection attempts exhausted. Stopping capture.",
                    self._max_reconnect_attempts,
                )
                self._running = False
                return False
            finally:
                self._reconnect_in_progress = False


    async def stop(self) -> None:
        """Stop capturing and clean up resources.

        Sets running to False and closes the page.
        """
        self._running = False
        self._log_chat_metrics_if_due(force=True, reason="stop")
        if self._page:
            try:
                await self._page.close()
            except Exception as e:
                logger.debug("Error closing page: {}", e)
            self._page = None
        self._ws = None
        logger.info("Capture stopped")

    async def get_room_id(self) -> str:
        """Extract the real room_id from the page's RENDER_DATA.

        The URL-visible room ID (short_id/web_rid) differs from the
        internal room_id. This method extracts the real room_id from
        the page's embedded JSON data.

        Returns:
            The real room_id as a string.

        Raises:
            ValueError: If room_id cannot be extracted from the page.
        """
        room_id = await self._page.evaluate("""
            () => {
                try {
                    const el = document.querySelector('#RENDER_DATA');
                    if (el) {
                        const data = JSON.parse(decodeURIComponent(el.textContent));
                        for (const key of Object.keys(data)) {
                            if (data[key] && data[key].room) {
                                return data[key].room.room_id ||
                                       data[key].room.id ||
                                       data[key].roomId;
                            }
                        }
                    }
                } catch(e) {}
                return null;
            }
        """)

        if not room_id:
            raise ValueError("Could not extract room_id from page RENDER_DATA")

        return str(room_id)
