"""HeyGem 实时 SDK 接口、未部署 stub 以及对接本地 HeyGem 服务的 WS 客户端。

工厂 `build_default_client()` 是唯一切换点 —— 视环境变量 `AISZR_HEYGEM_URL` 决定
返回 stub 还是真实客户端。

Phase 2 改动（plan 2.4）：
- 引入 `LipFrame` NamedTuple（嘴部 crop + 坐标 + 单调 pts），替代原 (np.ndarray, int) 二元组
- Protocol `start` 增加 keyword-only 形参 `wav_duration_ms`
- 新增 `_RealHeyGemClient`：HTTP 控制平面探活/start/stop + WS 数据平面 send/recv
- 维护 push-side wrap、recv-side unwrap（未来钳制法），保证返还给 worker 的 pts 单调
- `_recv_loop` 走独立 reader thread（实现细节），只往 deque 写；Protocol 五方法仍单线程
- stop() 先 ws.close() 强制打断 recv，再 join — 毫秒级秒退；settimeout(0.5) 兜底
"""
from __future__ import annotations

import collections
import os
import struct
import threading
from typing import NamedTuple, Protocol, runtime_checkable

import numpy as np
from loguru import logger


DEFAULT_SAMPLE_RATE = 24000
DEFAULT_TARGET_FPS = 25
DEFAULT_CHUNK_MS = 240
DEFAULT_HEYGEM_URL = "http://localhost:8770"

# HeyGem 推理 + 网络往返的合理延迟上限。recv 回来的 pts 超过 push 高水位
# 加这个余量 → 必定属于上一周期（plan 2.3 R1 未来钳制法）。
PTS_FUTURE_CLAMP_MS = 100

# WS reader 端的环形缓冲容量；过满即 popleft 丢老帧，防 GC 内存暴涨。
FRAME_DEQUE_MAX = 60

# WS recv 兜底超时（plan 2.4 R3：close() 是主退出手段，settimeout 只为极端兜底）。
WS_RECV_TIMEOUT_SEC = 0.5


class HeyGemNotInstalledError(RuntimeError):
    """HeyGem 实时服务未就绪（未配 AISZR_HEYGEM_URL 或 /v1/health 探活失败）。"""


class LipFrame(NamedTuple):
    """服务端返回的一帧嘴部裁剪 + 在 mp4 原帧上的贴回坐标。

    `pts_ms` 已由 `_RealHeyGemClient._restore_monotonic_pts` 还原为
    与 audio_worker 同坐标系的单调 pts（跨 wav 循环不归零）。

    故意**不存** server 端 avatar_frame_idx：双端 mp4 解码器帧数可能不一致，
    客户端按 `wrapped_pts × target_fps` 本地算 idx（plan 2.4 R4）。
    """

    mouth_rgb: np.ndarray   # (crop_h, crop_w, 3) uint8 RGB
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    pts_ms: int


@runtime_checkable
class HeyGemRealtimeClient(Protocol):
    """Realtime lip-sync 客户端契约。所有方法在 HeyGemWorker 单线程上调用。

    实现约束：
    - 五个方法全部在 HeyGemWorker 线程被调用，实现内部**不要**自己起线程碰 CUDA。
      `_RealHeyGemClient._reader_thread` 是受 `_deque_lock` 保护的 deque writer，
      不调任何 Protocol 方法，因此不破坏单线程约束。
    - `push_audio_chunk` / `pull_video_frame` 必须**非阻塞**。
    - `pts_ms` 是透传身份标签，对应输出帧的 pts 必须单调跟随输入。
    - 帧格式：`LipFrame.mouth_rgb` shape `(H, W, 3)` uint8 RGB。
    - `close()` 必须在 `start()` 同一线程调用，且只能在 `stop()` 之后。
    """

    def start(
        self,
        avatar_video_path: str,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        target_fps: int = DEFAULT_TARGET_FPS,
        wav_duration_ms: int,
    ) -> None: ...

    def push_audio_chunk(self, pcm_s16le_mono: bytes, pts_ms: int) -> None: ...

    def pull_video_frame(self) -> "LipFrame | None": ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class _NotInstalledHeyGemClient:
    """部署前的占位实现：`start` 即抛 `HeyGemNotInstalledError`。"""

    def start(
        self,
        avatar_video_path: str,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        target_fps: int = DEFAULT_TARGET_FPS,
        wav_duration_ms: int = 0,
    ) -> None:
        raise HeyGemNotInstalledError(
            "尚未部署 HeyGem 实时 SDK，请先在 deploy/ 目录运行安装脚本"
            "（或设置 AISZR_HEYGEM_URL 指向本地 HeyGem 服务）"
        )

    def push_audio_chunk(self, pcm_s16le_mono: bytes, pts_ms: int) -> None:
        return None

    def pull_video_frame(self) -> "LipFrame | None":
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class _RealHeyGemClient:
    """对接本地 HeyGem 服务（HTTP 控制平面 + WS 数据平面）。

    协议见 plan 2.2 / 2.3。LipFrame 的 pts 在内部还原成单调 pts —— Dialog 端
    `_on_repaint_tick` 对 wrap 完全无感。
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or DEFAULT_HEYGEM_URL).rstrip("/")
        self._sid: str | None = None
        self._ws = None                     # websocket.WebSocket，lazy import
        self._reader_thread: threading.Thread | None = None
        self._closing = False
        self._frame_deque: collections.deque[LipFrame] = collections.deque()
        self._deque_lock = threading.Lock()

        # PTS unwrap 状态（plan 2.3）
        self._wav_duration_ms: int = 0
        self._latest_pushed_monotonic_pts: int = 0
        self._last_restored_pts: int = 0

        # 单线程契约守门
        self._owner_thread_id: int | None = None

    # ── 五方法 Protocol 实现 ─────────────────────────────────────────────

    def start(
        self,
        avatar_video_path: str,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        target_fps: int = DEFAULT_TARGET_FPS,
        wav_duration_ms: int,
    ) -> None:
        if wav_duration_ms <= 0:
            raise ValueError(
                f"wav_duration_ms 必须 > 0，实际 {wav_duration_ms}"
            )
        self._owner_thread_id = threading.get_ident()
        self._wav_duration_ms = wav_duration_ms
        self._latest_pushed_monotonic_pts = 0
        self._last_restored_pts = 0

        # lazy import — 让没装 websocket-client 的开发机仍能用 stub 路径
        try:
            import requests
        except ImportError as exc:
            raise HeyGemNotInstalledError(f"未安装 requests: {exc}") from exc
        try:
            import websocket  # noqa: F401  确保模块存在
        except ImportError as exc:
            raise HeyGemNotInstalledError(
                f"未安装 websocket-client (pip install websocket-client): {exc}"
            ) from exc

        # 1) 探活
        try:
            resp = requests.get(f"{self._base_url}/v1/health", timeout=2)
            resp.raise_for_status()
        except Exception as exc:
            raise HeyGemNotInstalledError(
                f"HeyGem 服务探活失败 {self._base_url}/v1/health: {exc}"
            ) from exc

        # 2) 开 session
        try:
            resp = requests.post(
                f"{self._base_url}/v1/sessions/start",
                json={
                    "avatar_video_path": avatar_video_path,
                    "sample_rate": sample_rate,
                    "target_fps": target_fps,
                    "wav_duration_ms": wav_duration_ms,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise HeyGemNotInstalledError(
                f"HeyGem /v1/sessions/start 失败: {exc}"
            ) from exc

        self._sid = data.get("session_id")
        if not self._sid:
            raise HeyGemNotInstalledError("HeyGem 服务返回空 session_id")

        # 3) 开 WS
        import websocket
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/v1/sessions/{self._sid}/stream"
        try:
            self._ws = websocket.create_connection(ws_url, timeout=5)
            self._ws.settimeout(WS_RECV_TIMEOUT_SEC)
        except Exception as exc:
            raise HeyGemNotInstalledError(f"HeyGem WS 连接失败 {ws_url}: {exc}") from exc

        # 4) 起 reader 线程
        self._closing = False
        self._reader_thread = threading.Thread(
            target=self._recv_loop,
            name="heygem-ws-reader",
            daemon=True,
        )
        self._reader_thread.start()
        logger.info(
            "_RealHeyGemClient started: url={} sid={} wav_duration_ms={}",
            self._base_url, self._sid, wav_duration_ms,
        )

    def push_audio_chunk(self, pcm_s16le_mono: bytes, pts_ms: int) -> None:
        if self._ws is None or self._closing:
            return
        wrapped = pts_ms % self._wav_duration_ms if self._wav_duration_ms > 0 else pts_ms
        self._latest_pushed_monotonic_pts = pts_ms
        try:
            from websocket import ABNF
            self._ws.send(
                struct.pack(">q", wrapped) + pcm_s16le_mono,
                opcode=ABNF.OPCODE_BINARY,
            )
        except Exception as exc:
            logger.warning("ws send dropped chunk pts={}: {}", pts_ms, exc)
            # 非阻塞契约：丢就丢，不抛

    def pull_video_frame(self) -> "LipFrame | None":
        with self._deque_lock:
            if not self._frame_deque:
                return None
            return self._frame_deque.popleft()

    def stop(self) -> None:
        """优化 R3：先 ws.close() 强制打断 recv，再 join → 毫秒级秒退。"""
        self._closing = True
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        # best-effort 关 session
        if self._sid is not None:
            try:
                import requests
                requests.post(
                    f"{self._base_url}/v1/sessions/{self._sid}/stop",
                    timeout=5,
                )
            except Exception:
                pass
        logger.info("_RealHeyGemClient stopped sid={}", self._sid)

    def close(self) -> None:
        with self._deque_lock:
            self._frame_deque.clear()
        self._sid = None
        self._ws = None
        self._reader_thread = None
        self._owner_thread_id = None

    # ── 内部：reader thread & 解码 ───────────────────────────────────────

    def _recv_loop(self) -> None:
        """reader thread —— 只往 deque 写，不调 Protocol 方法。"""
        from websocket import (
            WebSocketException,
            WebSocketTimeoutException,
        )

        while not self._closing:
            ws = self._ws
            if ws is None:
                break
            try:
                payload = ws.recv()
            except WebSocketTimeoutException:
                continue           # 超时唤醒：检查 _closing 后回环
            except WebSocketException as exc:
                logger.debug("ws recv aborted: {}", exc)
                break
            except Exception as exc:
                logger.warning("ws recv unexpected error: {}", exc)
                break
            if not payload:
                break
            if isinstance(payload, str):
                # 控制帧或服务端 status 推送 —— 跳过
                continue
            try:
                lip = self._decode_frame(payload)
            except Exception as exc:
                logger.warning("decode frame failed: {}", exc)
                continue
            with self._deque_lock:
                self._frame_deque.append(lip)
                while len(self._frame_deque) > FRAME_DEQUE_MAX:
                    self._frame_deque.popleft()

    def _decode_frame(self, payload: bytes) -> LipFrame:
        """协议：[i64 BE wrapped_pts][i32 BE server_idx][i32 BE crop_x/y/w/h][crop RGB888]。

        server_idx 解出来后**故意丢弃**（plan 2.4 R4：双端解码器帧数可能不一致，
        客户端按本地 wrapped_pts × target_fps 算 idx）。
        """
        HEAD_FMT = ">qiiiii"
        HEAD = struct.calcsize(HEAD_FMT)
        if len(payload) < HEAD:
            raise ValueError(f"frame payload too short: {len(payload)} < {HEAD}")
        wrapped_pts, _server_idx, cx, cy, cw, ch = struct.unpack(
            HEAD_FMT, payload[:HEAD]
        )
        expected = cw * ch * 3
        body = payload[HEAD:HEAD + expected]
        if len(body) != expected:
            raise ValueError(
                f"frame body size mismatch: got {len(body)}, expected "
                f"{cw}*{ch}*3={expected}"
            )
        mouth = np.frombuffer(body, dtype=np.uint8).reshape(ch, cw, 3).copy()
        return LipFrame(
            mouth_rgb=mouth,
            crop_x=int(cx),
            crop_y=int(cy),
            crop_w=int(cw),
            crop_h=int(ch),
            pts_ms=self._restore_monotonic_pts(int(wrapped_pts)),
        )

    def _restore_monotonic_pts(self, wrapped_pts: int) -> int:
        """未来钳制法 unwrap（plan 2.3 R1）。

        服务端回的 wrapped_pts 落在 [0, wav_duration_ms)。给它加上"当前推进的循环
        编号 × wav_duration_ms"。若 candidate 超出"最近推送的 monotonic_pts +
        PTS_FUTURE_CLAMP_MS"（即超出合理推理 + 网络延迟上限），必定属于上一周期，
        减去一个 wav_duration_ms。

        最后再 max 一次，防服务端抖动导致 pts 倒退污染 ring 排序。
        """
        if self._wav_duration_ms <= 0:
            return wrapped_pts
        base_cycle = self._latest_pushed_monotonic_pts // self._wav_duration_ms
        candidate = base_cycle * self._wav_duration_ms + wrapped_pts
        if candidate > self._latest_pushed_monotonic_pts + PTS_FUTURE_CLAMP_MS:
            candidate -= self._wav_duration_ms
        self._last_restored_pts = max(self._last_restored_pts, candidate)
        return self._last_restored_pts


def build_default_client() -> HeyGemRealtimeClient:
    """工厂函数。`AISZR_HEYGEM_URL` 缺省 → stub；设置 → 真实 WS 客户端。"""
    base_url = os.environ.get("AISZR_HEYGEM_URL")
    if base_url:
        return _RealHeyGemClient(base_url=base_url)
    return _NotInstalledHeyGemClient()
