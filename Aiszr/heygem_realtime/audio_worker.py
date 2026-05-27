"""PyAudio 回调模式的本地 WAV 循环播放 worker。

设计要点（对应 plan.R1 / R9 / R8）：
- PA 回调跑在 PA 自己的线程上，**绝不**直接 emit pyqtSignal
- 回调里只把 PCM 字节扔进 `collections.deque`（append 是原子的），并在 `_pts_lock`
  下推进 `_played_samples`（主时钟）
- 一个跑在 worker 线程的 QTimer(5ms) 把 deque 排空，重新切分为固定 `BYTES_PER_CHUNK`
  并 emit `chunk_ready(pcm, pts_ms)`
- EOF 时当前回调用 0 填补剩余字节，下一次回调从头读，避免 click
- PTS 跨循环边界单调递增（绝不归零）
"""
from __future__ import annotations

import threading
import wave
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from loguru import logger


SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le
CHUNK_MS = 240
SAMPLES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS // 1000   # 5760
BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * SAMPLE_WIDTH   # 11520
DRAIN_INTERVAL_MS = 5


class AudioPlaybackWorker(QObject):
    """循环播放本地 WAV、按固定块向外 emit 音频供 HeyGem 消费。"""

    chunk_ready = pyqtSignal(bytes, int)   # (pcm_s16le_mono, chunk_pts_ms)
    started = pyqtSignal()
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._wave: wave.Wave_read | None = None
        self._pa = None             # type: ignore[assignment]   pyaudiowpatch.PyAudio
        self._stream = None         # type: ignore[assignment]   pyaudio Stream

        self._raw_chunks: deque[bytes] = deque()
        self._rechunk_buffer = bytearray()

        self._pts_lock = threading.Lock()
        self._played_samples: int = 0       # 主时钟（喂给 PA 的累计样本，跨循环单调）
        self._emit_pts_samples: int = 0     # 已 emit 给 HeyGem 的累计样本（跨循环单调）

        self._drain_timer: QTimer | None = None
        self._closing = False

    @pyqtSlot(str, int)
    def start(self, wav_path: str, out_device_index: int) -> None:
        """打开 WAV、开 PA 输出流。在 worker 线程上调用（thread.started 触发）。"""
        try:
            self._wave = wave.open(wav_path, "rb")
        except Exception as exc:
            self.error.emit(f"无法打开 WAV 文件: {exc}")
            return

        rate = self._wave.getframerate()
        ch = self._wave.getnchannels()
        sw = self._wave.getsampwidth()
        if rate != SAMPLE_RATE or ch != CHANNELS or sw != SAMPLE_WIDTH:
            self._wave.close()
            self._wave = None
            self.error.emit(
                f"WAV 格式不匹配：期望 {SAMPLE_RATE}Hz mono s16le，"
                f"实际 {rate}Hz {ch}ch sw={sw}"
            )
            return

        try:
            import pyaudiowpatch as paw
        except ImportError as exc:
            self.error.emit(f"未安装 pyaudiowpatch: {exc}")
            self._wave.close()
            self._wave = None
            return

        try:
            self._pa = paw.PyAudio()
            kwargs = dict(
                format=paw.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=1024,
                stream_callback=self._pa_callback,
            )
            if out_device_index is not None and out_device_index >= 0:
                kwargs["output_device_index"] = out_device_index
            self._stream = self._pa.open(**kwargs)
        except Exception as exc:
            self.error.emit(f"无法打开音频输出流: {exc}")
            self._cleanup_pa()
            return

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._drain_chunk_queue)
        self._drain_timer.start()

        try:
            self._stream.start_stream()
        except Exception as exc:
            self.error.emit(f"无法启动音频流: {exc}")
            self._cleanup_pa()
            return

        logger.info(
            "AudioPlaybackWorker started: wav={} device={}",
            Path(wav_path).name,
            out_device_index,
        )
        self.started.emit()

    @pyqtSlot()
    def stop(self) -> None:
        """同线程调用：关流、关 PA、清状态。"""
        self._closing = True
        if self._drain_timer is not None:
            self._drain_timer.stop()
            self._drain_timer = None
        self._cleanup_pa()
        if self._wave is not None:
            try:
                self._wave.close()
            except Exception:
                pass
            self._wave = None
        self._raw_chunks.clear()
        self._rechunk_buffer.clear()
        logger.info("AudioPlaybackWorker stopped")
        self.stopped.emit()

    def current_play_pts_ms(self) -> int:
        """主时钟（线程安全）。Dialog 16ms tick 读这个。"""
        with self._pts_lock:
            return int(self._played_samples * 1000 / SAMPLE_RATE)

    # ── PA callback (runs on PA's own thread) ──────────────────────────────

    def _pa_callback(self, in_data, frame_count, time_info, status):  # noqa: D401
        import pyaudiowpatch as paw

        if self._closing or self._wave is None:
            return (b"\x00" * (frame_count * SAMPLE_WIDTH), paw.paComplete)

        needed_bytes = frame_count * SAMPLE_WIDTH
        out = bytearray()
        while len(out) < needed_bytes:
            remaining = needed_bytes - len(out)
            frames_to_read = remaining // SAMPLE_WIDTH
            try:
                chunk = self._wave.readframes(frames_to_read)
            except Exception:
                chunk = b""
            if not chunk:
                # EOF — 用 0 填补本次回调，下次从头读，避免 click
                out.extend(b"\x00" * remaining)
                try:
                    self._wave.rewind()
                except Exception:
                    pass
                break
            out.extend(chunk)

        # deque.append 是原子的 (CPython)，无需锁
        self._raw_chunks.append(bytes(out))
        with self._pts_lock:
            self._played_samples += frame_count

        return (bytes(out), paw.paContinue)

    # ── Drain (runs on worker thread via QTimer) ───────────────────────────

    def _drain_chunk_queue(self) -> None:
        # 把 PA 回调线程攒下的字节并到 rechunk 缓冲
        while True:
            try:
                self._rechunk_buffer.extend(self._raw_chunks.popleft())
            except IndexError:
                break

        # 切固定 BYTES_PER_CHUNK 大小往外 emit
        while len(self._rechunk_buffer) >= BYTES_PER_CHUNK:
            pcm = bytes(self._rechunk_buffer[:BYTES_PER_CHUNK])
            del self._rechunk_buffer[:BYTES_PER_CHUNK]
            chunk_pts_ms = int(self._emit_pts_samples * 1000 / SAMPLE_RATE)
            self._emit_pts_samples += SAMPLES_PER_CHUNK
            self.chunk_ready.emit(pcm, chunk_pts_ms)

    def _cleanup_pa(self) -> None:
        if self._stream is not None:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
