"""Probe V6: Capture request + response bodies for /live/msg endpoint.

Usage:
    python wechat_probe.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

TARGET_URL = "https://channels.weixin.qq.com/platform/live/liveBuild"
USER_DATA_DIR = Path(__file__).resolve().parent / "wechat_browser_data"

_TS = lambda: datetime.now().strftime("%H:%M:%S")


def _log(tag: str, msg: str) -> None:
    print(f"[{_TS()}] [{tag}] {msg}")


async def main():
    _log("INIT", f"Browser data dir: {USER_DATA_DIR}")

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR.resolve()),
        headless=False,
        channel="msedge",
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1920, "height": 1080},
    )

    page = await context.new_page()

    # ── Intercept /live/msg request + response ──────────────────
    async def handle_route(route):
        req = route.request
        url = req.url

        if "/live/msg" in url:
            # Log request
            _log("MSG-REQ", f"URL: {url}")
            _log("MSG-REQ", f"Headers: {json.dumps(dict(req.headers), ensure_ascii=False)[:500]}")
            post_data = req.post_data
            if post_data:
                _log("MSG-REQ-BODY", post_data[:2000])
                try:
                    obj = json.loads(post_data)
                    _log("MSG-REQ-JSON", json.dumps(obj, ensure_ascii=False, indent=2)[:2000])
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                _log("MSG-REQ-BODY", "(empty / form-data)")

        # Forward the request
        resp = await route.fetch()
        body = await resp.body()

        if "/live/msg" in url:
            text = body.decode("utf-8", errors="replace")
            _log("MSG-RESP", f"Status: {resp.status}")
            _log("MSG-RESP-BODY", text[:3000])
            try:
                obj = json.loads(text)
                inner = obj.get("data", {}).get("respJsonStr", "")
                if inner:
                    inner_obj = json.loads(inner)
                    msgs = inner_obj.get("msg_list", [])
                    _log("DANMAKU", f"=== {len(msgs)} messages ===")
                    for m in msgs:
                        _log("DANMAKU",
                             f"  [{m.get('nickname','?')}] {m.get('content','?')}  "
                             f"(seq={m.get('seq','?')}, type={m.get('type','?')})")
            except Exception:
                pass

        await route.fulfill(response=resp)

    await page.route("**/mmfinderassistant-bin/live/msg**", handle_route)

    # ── WS monitor ───────────────────────────────────────────────
    def _on_ws(ws):
        _log("WS-OPEN", ws.url)

        def _on_frame(payload):
            if isinstance(payload, bytes):
                try:
                    text = payload.decode("utf-8")
                    _log("WS-BIN", text[:500])
                except UnicodeDecodeError:
                    _log("WS-BIN", payload[:120].hex(" "))
            else:
                _log("WS-TXT", str(payload)[:500])

        def _on_close():
            _log("WS-CLOSE", ws.url)

        ws.on("framereceived", _on_frame)
        ws.on("close", _on_close)

    page.on("websocket", _on_ws)

    # ── Navigate ─────────────────────────────────────────────────
    _log("PAGE", f"→ {TARGET_URL}")
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
    _log("PAGE", f"Current: {page.url}")

    if "login" in page.url.lower():
        _log("LOGIN", "需要扫码登录，请在浏览器中完成...")
        while True:
            await asyncio.sleep(2)
            cur = page.url
            if "platform" in cur and "login" not in cur.lower():
                _log("LOGIN", f"已登录: {cur}")
                break

    _log("READY", "══════════════════════════════════════════════")
    _log("READY", "等待 /live/msg 轮询响应...")
    _log("READY", "让观众发弹幕，观察 [MSG] / [MSG-JSON] 输出")
    _log("READY", "按 Ctrl+C 退出")
    _log("READY", "══════════════════════════════════════════════")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        _log("STOP", "用户中断")

    await context.close()
    await pw.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
