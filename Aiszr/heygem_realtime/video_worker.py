"""HeyGem 实时 SDK 客户端的 worker 包装。

线程亲和性约束（plan.R2）：
- CUDA / TRT context 必须在 `start` slot 里创建（thread.started → start 这条回调）
- 所有 `client.*` 调用必须在同一线程上
- `stop` 在同线程里依次 `client.stop()` → `client.close()`
- `_thread_id` 守门：start 记下，stop/close 时断言一致

Phase 2 改动（plan 2.5）：
- `start` 签名扩展 `wav_duration_ms`，透传给 `client.start(..., wav_duration_ms=...)`
- `frame_ready` payload 从 `(np.ndarray, int)` 改为单个 `LipFrame`
"""
from __future__ import annotations

import threading

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from loguru import logger

from heygem_realtime.client import (
    HeyGemNotInstalledError,
    HeyGemRealtimeClient,
    LipFrame,
    build_default_client,
)


DRAIN_INTERVAL_MS = 5
MAX_DRAIN_PER_TICK = 4


class HeyGemWorker(QObject):
    """把 audio chunk 喂给 HeyGem SDK，把 SDK 吐回的 LipFrame emit 出去。"""

    frame_ready = pyqtSignal(object)        # payload: LipFrame
    started = pyqtSignal()
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(
        self,
        client: HeyGemRealtimeClient | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # client 在 start slot 里 build —— 保证真实 SDK 的 CUDA context 落在 worker 线程
        self._injected_client: HeyGemRealtimeClient | None = client
        self._client: HeyGemRealtimeClient | None = None
        self._drain_timer: QTimer | None = None
        self._thread_id: int | None = None
        self._running = False

    @pyqtSlot(str, int, str)
    def start(self, avatar_video_path: str, wav_duration_ms: int, wav_path: str = "") -> None:
        """在 worker 线程上启动 SDK。失败时 emit error 并不再继续。"""
        self._thread_id = threading.get_ident()
        self._client = self._injected_client or build_default_client()
        try:
            self._client.start(
                avatar_video_path, wav_duration_ms=wav_duration_ms,
                wav_path=wav_path,
            )
        except HeyGemNotInstalledError as exc:
            logger.warning("HeyGem 未部署：{}", exc)
            self.error.emit(str(exc))
            return
        except Exception as exc:
            logger.exception("HeyGem client.start 失败")
            self.error.emit(f"HeyGem 启动失败: {exc}")
            return

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._drain_frames)
        self._drain_timer.start()
        self._running = True
        logger.info("HeyGemWorker started")
        self.started.emit()

    @pyqtSlot(bytes, int)
    def on_audio_chunk(self, pcm_bytes: bytes, pts_ms: int) -> None:
        if not self._running or self._client is None:
            return
        try:
            self._client.push_audio_chunk(pcm_bytes, pts_ms)
        except HeyGemNotInstalledError as exc:
            self._running = False
            self.error.emit(str(exc))
        except Exception as exc:
            logger.exception("push_audio_chunk 失败")
            self._running = False
            self.error.emit(f"HeyGem 推送音频失败: {exc}")

    @pyqtSlot()
    def stop(self) -> None:
        """同线程：停 drain timer → client.stop → client.close → emit stopped。"""
        if self._thread_id is not None and self._thread_id != threading.get_ident():
            logger.error(
                "HeyGemWorker.stop 在错线程被调用 expect={} got={}",
                self._thread_id,
                threading.get_ident(),
            )
            # 仍然尝试停 timer，避免泄漏
        self._running = False
        if self._drain_timer is not None:
            self._drain_timer.stop()
            self._drain_timer = None
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                logger.exception("client.stop 异常")
            try:
                self._client.close()
            except Exception:
                logger.exception("client.close 异常")
        logger.info("HeyGemWorker stopped")
        self.stopped.emit()

    def _drain_frames(self) -> None:
        if not self._running or self._client is None:
            return
        for _ in range(MAX_DRAIN_PER_TICK):
            try:
                result = self._client.pull_video_frame()
            except Exception as exc:
                logger.exception("pull_video_frame 异常")
                self._running = False
                self.error.emit(f"HeyGem 取帧失败: {exc}")
                return
            if result is None:
                break
            if not isinstance(result, LipFrame):
                logger.warning(
                    "client.pull_video_frame 返回非 LipFrame：{}",
                    type(result).__name__,
                )
                continue
            self.frame_ready.emit(result)
