"""HeyGem 实时口型预览弹窗。

UX 约束：
- 标准 Windows 标题栏（最小化/最大化/关闭三按钮可见可用，feedback memory 锁定）
- 视频区域（_FramePainter）hover-inert（WA_TransparentForMouseEvents）
- Esc 关 / 主窗口级联关 / 标题栏 X 关

Phase 2 改动（plan 2.6）：
- A/V sync Bug 1 修复：每 tick 至多消费一帧；超前帧留在 ring head
- avatar mp4 预读到 RAM、预转 RGB 一次（优化 R2）
- 嘴部合成 `_compose_frame`：本地按 wrapped_pts × target_fps 算 idx（优化 R4）
- `_on_frame_ready` payload 改 LipFrame；`_frame_ring: deque[LipFrame]`
- wav_duration_ms 在 __init__ 计算，透传给 video_worker.start
"""
from __future__ import annotations

import wave
from collections import deque
from pathlib import Path

import numpy as np
from PyQt5.QtCore import (
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget
from loguru import logger

from heygem_realtime.audio_worker import AudioPlaybackWorker
from heygem_realtime.client import LipFrame
from heygem_realtime.video_worker import HeyGemWorker


# A/V sync 窗口（plan）：超 -250ms 老帧丢，+80ms 超前留下一 tick
SYNC_WINDOW_LOWER_MS = -250
SYNC_WINDOW_UPPER_MS = 80

REPAINT_INTERVAL_MS = 16          # ~60Hz
FRAME_RING_MAX = 30
SHUTDOWN_WAIT_MS = 2000
AVATAR_TARGET_FPS = 25            # 与 client.start 的 target_fps 保持一致

DEFAULT_DIALOG_SIZE = (540, 960)  # 大致 9:16，避免在 SDK 没接前就预设错的尺寸


def _numpy_rgb_to_qimage(arr: np.ndarray) -> QImage:
    """numpy (H, W, 3) uint8 RGB → QImage Format_RGB888，含一次 copy 防止内存被回收。"""
    if arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
        raise ValueError(f"unexpected frame shape/dtype: {arr.shape} {arr.dtype}")
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    h, w, _ = arr.shape
    image = QImage(arr.data, w, h, w * 3, QImage.Format_RGB888)
    return image.copy()


def _read_wav_duration_ms(wav_path: str) -> int:
    with wave.open(wav_path, "rb") as wf:
        return int(wf.getnframes() * 1000 / wf.getframerate())


def _preload_avatar_frames_rgb(avatar_path: str) -> list[np.ndarray]:
    """预读 avatar mp4 到 RAM 并预转 RGB（plan 2.6 优化 R2）。

    cv2 是 lazy import —— 没装 OpenCV 的开发机仍能跑 stub 路径（错误覆盖
    模式下不会触发 _compose_frame）。装了 cv2 但视频本身有问题，记 warning
    并返回空 list；后续 _compose_frame 会优雅退化。
    """
    try:
        import cv2
    except ImportError:
        logger.warning(
            "未安装 opencv-python，无法预读 avatar 帧；real-HeyGem 路径需要 cv2"
        )
        return []

    cap = cv2.VideoCapture(avatar_path)
    if not cap.isOpened():
        logger.warning("avatar 视频打开失败: {}", avatar_path)
        try:
            cap.release()
        except Exception:
            pass
        return []

    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    return frames


class _FramePainter(QWidget):
    """黑底 + 当前帧的 QWidget。hover 完全无反应。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(True)
        self._image: QImage | None = None

    def set_frame(self, image: QImage) -> None:
        self._image = image

    def paintEvent(self, event) -> None:  # noqa: D401
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._image is None or self._image.isNull():
            return
        # 等比缩放居中
        target = self.rect()
        scaled = self._image.scaled(
            target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        x = (target.width() - scaled.width()) // 2
        y = (target.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)


class HeyGemPreviewDialog(QDialog):
    """实时口型驱动预览弹窗。"""

    closed = pyqtSignal()

    def __init__(
        self,
        wav_path: str,
        avatar_video_path: str,
        out_device_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._wav_path = wav_path
        self._avatar_path = avatar_video_path
        self._out_device_index = out_device_index

        self.setWindowTitle("数字人实时预览")
        # 标准 Windows 标题栏（feedback memory 锁定），视频区域仍 hover-inert
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(*DEFAULT_DIALOG_SIZE)

        self._frame_ring: deque[LipFrame] = deque(maxlen=FRAME_RING_MAX)
        self._first_frame_received = False
        self._closing = False
        self._target_fps = AVATAR_TARGET_FPS
        self._wav_duration_ms = 0
        self._avatar_frames: list[np.ndarray] = []

        # 预读 wav 时长 + avatar 帧（在起 worker 之前）
        init_error: str | None = None
        try:
            self._wav_duration_ms = _read_wav_duration_ms(wav_path)
        except Exception as exc:
            init_error = f"无法读取 WAV 时长: {exc}"
            logger.warning("{} path={}", init_error, wav_path)
        if self._wav_duration_ms <= 0 and init_error is None:
            init_error = f"WAV 时长为 0: {wav_path}"

        if init_error is None:
            self._avatar_frames = _preload_avatar_frames_rgb(avatar_video_path)
            logger.info(
                "avatar preloaded: path={} frames={}",
                Path(avatar_video_path).name,
                len(self._avatar_frames),
            )

        # ── Layout ─────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._painter = _FramePainter(self)
        root.addWidget(self._painter)

        # 覆盖层：冷启动文字 + 错误文字
        self._overlay_label = QLabel("数字人启动中…", self._painter)
        self._overlay_label.setAlignment(Qt.AlignCenter)
        self._overlay_label.setStyleSheet(
            "color: white; font-size: 16px; "
            "background-color: rgba(0, 0, 0, 180);"
        )

        self._error_label = QLabel("", self._painter)
        self._error_label.setAlignment(Qt.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            "color: #ffb3b3; font-size: 15px; padding: 24px; "
            "background-color: rgba(0, 0, 0, 200);"
        )
        self._error_label.hide()

        # ── Workers + Threads ──────────────────────────────────────────────
        self._audio_thread = QThread(self)
        self._audio_worker = AudioPlaybackWorker()
        self._audio_worker.moveToThread(self._audio_thread)

        self._video_thread = QThread(self)
        self._video_worker = HeyGemWorker()
        self._video_worker.moveToThread(self._video_thread)

        # audio chunk → heygem on_audio_chunk（保留连接句柄供 close 时断开）
        self._chunk_conn = self._audio_worker.chunk_ready.connect(
            self._video_worker.on_audio_chunk, Qt.QueuedConnection
        )
        self._video_worker.frame_ready.connect(
            self._on_frame_ready, Qt.QueuedConnection
        )
        self._audio_worker.error.connect(self._on_worker_error, Qt.QueuedConnection)
        self._video_worker.error.connect(self._on_worker_error, Qt.QueuedConnection)

        # thread.started → worker.start(...)
        self._audio_thread.started.connect(
            lambda: self._audio_worker.start(self._wav_path, self._out_device_index)
        )
        self._video_thread.started.connect(
            lambda: self._video_worker.start(self._avatar_path, self._wav_duration_ms)
        )

        # ── Repaint timer ──────────────────────────────────────────────────
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._on_repaint_tick)

        if init_error is not None:
            # 初始化阶段就拿到致命错 —— 不启 worker，直接显示错误覆盖
            QTimer.singleShot(0, lambda msg=init_error: self._on_worker_error(msg))
            return

        # 启动顺序：video 先 start 让 SDK warmup，audio 紧接其后；任一失败由 error 信号驱动 UI
        self._video_thread.start()
        self._audio_thread.start()
        self._repaint_timer.start()

    # ── Geometry: 让覆盖层跟随大小 ────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: D401
        super().resizeEvent(event)
        rect = self._painter.rect()
        self._overlay_label.setGeometry(rect)
        self._error_label.setGeometry(rect)

    # ── Key handling: Esc 关 ──────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: D401
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # ── Worker signal handlers ────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_frame_ready(self, lip_frame: object) -> None:
        if not isinstance(lip_frame, LipFrame):
            return
        self._frame_ring.append(lip_frame)
        if not self._first_frame_received:
            self._first_frame_received = True
            self._overlay_label.hide()

    @pyqtSlot(str)
    def _on_worker_error(self, message: str) -> None:
        if self._closing:
            return
        logger.warning("HeyGemPreviewDialog worker error: {}", message)
        self._overlay_label.hide()
        self._error_label.setText(message)
        self._error_label.show()

    @pyqtSlot()
    def _on_repaint_tick(self) -> None:
        """Bug 1 修复（plan 2.6）：每 tick 至多消费一帧；超前帧原样留在 ring head。"""
        if self._closing:
            return
        audio_pts = self._audio_worker.current_play_pts_ms()
        # Step 1: 丢老帧（连续 popleft 直到 head 不再老）
        while self._frame_ring and self._frame_ring[0].pts_ms < audio_pts + SYNC_WINDOW_LOWER_MS:
            self._frame_ring.popleft()
        # Step 2: 至多消费一帧；超前的 head 原样保留
        chosen: LipFrame | None = None
        if self._frame_ring:
            head = self._frame_ring[0]
            if head.pts_ms <= audio_pts + SYNC_WINDOW_UPPER_MS:
                chosen = self._frame_ring.popleft()
        if chosen is None:
            return
        try:
            composed_rgb = self._compose_frame(chosen)
            if composed_rgb is None:
                return
            image = _numpy_rgb_to_qimage(composed_rgb)
        except Exception as exc:
            logger.warning("frame compose failed: {}", exc)
            return
        self._painter.set_frame(image)
        self._painter.update()

    # ── 嘴部合成（plan 2.6 优化 R2 + R4） ──────────────────────────────────

    def _compose_frame(self, lip: LipFrame) -> np.ndarray | None:
        """把 lip.mouth_rgb 贴到本地按 pts 算出的 avatar 帧上，返回 RGB。

        优化 R2: _avatar_frames 已是 RGB，无需 cvtColor。
        优化 R4: idx 用本地 wrapped_pts × target_fps 算，不信服务端 avatar_frame_idx。
        plan P2 守门: 越界 crop 直接返回 base 不贴嘴，不崩。

        avatar_frames 为空时（cv2 没装 / 视频解码失败）返回 None — 调用方跳过该 tick。
        """
        if not self._avatar_frames:
            return None
        if self._wav_duration_ms <= 0:
            return None
        wrapped_pts = lip.pts_ms % self._wav_duration_ms
        idx = int(wrapped_pts * self._target_fps / 1000) % len(self._avatar_frames)
        base_rgb = self._avatar_frames[idx]
        composed = base_rgb.copy()
        h, w = composed.shape[:2]
        # 边界 clip
        x1, y1 = max(0, lip.crop_x), max(0, lip.crop_y)
        x2 = min(w, lip.crop_x + lip.crop_w)
        y2 = min(h, lip.crop_y + lip.crop_h)
        if x1 >= x2 or y1 >= y2:
            return composed
        mx1, my1 = x1 - lip.crop_x, y1 - lip.crop_y
        mx2 = mx1 + (x2 - x1)
        my2 = my1 + (y2 - y1)
        composed[y1:y2, x1:x2] = lip.mouth_rgb[my1:my2, mx1:mx2]
        return composed

    # ── Shutdown ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: D401
        if self._closing:
            event.accept()
            return
        self._closing = True
        self._repaint_timer.stop()

        # 断开 audio → video 链路，避免往 stopping 的 client 推块
        try:
            self._audio_worker.chunk_ready.disconnect(self._video_worker.on_audio_chunk)
        except Exception:
            pass

        # HeyGem 先停：CUDA cleanup 同线程跑完再 quit
        if self._video_thread.isRunning():
            QMetaObject.invokeMethod(
                self._video_worker, "stop", Qt.BlockingQueuedConnection
            )
            self._video_thread.quit()
            self._video_thread.wait(SHUTDOWN_WAIT_MS)

        # Audio 后停
        if self._audio_thread.isRunning():
            QMetaObject.invokeMethod(
                self._audio_worker, "stop", Qt.BlockingQueuedConnection
            )
            self._audio_thread.quit()
            self._audio_thread.wait(SHUTDOWN_WAIT_MS)

        self.closed.emit()
        super().closeEvent(event)
