"""WeChat Channels live danmaku capture via HTTP polling.

Polls /micro/live/cgi-bin/mmfinderassistant-bin/live/msg every ~5s,
parses msg_list items into the common danmaku dict format used by
DanmakuDisplay and AIReplyEngine.

Usage:
    wc = WeChatCapture(on_message=handler)
    await wc.start()
    ...
    await wc.stop()
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from playwright.async_api import async_playwright

DATA_DIR = Path(__file__).resolve().parent / "wechat_browser_data"
PAGE_URL = "https://channels.weixin.qq.com/platform/live/liveBuild"
MSG_ENDPOINT = "/mmfinderassistant-bin/live/msg"
POLL_SEC = 5.0


def _make_message(msg_item: dict) -> dict:
    nickname = msg_item.get("nickname", "")
    content = msg_item.get("content", "")
    username = msg_item.get("username", "")
    seq = msg_item.get("seq", "0")

    ts = time.time()
    ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    return {
        "type": "chat",
        "user_id": username or f"wx_{seq}",
        "nickname": nickname,
        "timestamp": ts,
        "time": ts_iso,
        "content": content,
        "seq": seq,
    }


class WeChatCapture:
    def __init__(self, on_message):
        self._on_message = on_message
        self._browser = None
        self._context = None
        self._page = None
        self._running = False
        self._seen_seqs: set[str] = set()

    async def start(self) -> str:
        self._running = True
        pw = await async_playwright().start()
        self._context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(DATA_DIR.resolve()),
            headless=False,
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        self._page = await self._context.new_page()

        self._page.on("response", self._on_response)
        await self._page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=30000)

        url = self._page.url
        if "login" in url.lower():
            logger.info("WeChat: 需要扫码登录")
            while self._running:
                await asyncio.sleep(2)
                try:
                    url = self._page.url
                except Exception:
                    break
                if "platform" in url and "login" not in url.lower():
                    logger.info(f"WeChat: 已登录 {url}")
                    break

        self._running = True
        return self._page.url

    async def _on_response(self, resp):
        if MSG_ENDPOINT not in resp.url or resp.status != 200:
            return
        try:
            body = await resp.body()
            data = json.loads(body.decode("utf-8"))
            inner = json.loads(data.get("data", {}).get("respJsonStr", "{}"))
            msgs = inner.get("msg_list", [])
            for item in msgs:
                seq = item.get("seq", "")
                if seq and seq in self._seen_seqs:
                    continue
                if seq:
                    self._seen_seqs.add(seq)
                msg = _make_message(item)
                await self._on_message(msg)
        except Exception:
            pass

    async def send_comment(self, text: str) -> bool:
        """注入文本到视频号助手「助手发言」框并点击发送。

        Selectors 由 .planning/phases/10-keyword-reply-ui-redesign/PROBE-RESULT.md 探针确认：
        textarea[placeholder*='发言'] (className message-input) + 第一个 visible+enabled
        的 button:has-text('发送') (className weui-desktop-btn_primary)。
        空框时按钮会带 weui-desktop-btn_disabled，fill 后会自动 enabled。
        """
        if not self._page or not self._running or not text:
            return False
        try:
            textarea = self._page.locator("textarea[placeholder*='发言']")
            await textarea.wait_for(state="visible", timeout=3000)
            await textarea.fill(text)
            buttons = await self._page.locator("button:has-text('发送')").all()
            for btn in buttons:
                try:
                    if await btn.is_visible() and await btn.is_enabled():
                        await btn.click()
                        logger.info(f"WeChat: 已注入评论 {text[:30]!r}")
                        return True
                except Exception:
                    continue
            logger.warning("WeChat: 找不到可点击的『发送』按钮")
            return False
        except Exception as e:
            logger.warning(f"WeChat: 注入评论失败 {e}")
            return False

    async def stop(self):
        self._running = False
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        self._page = None
        self._seen_seqs.clear()
