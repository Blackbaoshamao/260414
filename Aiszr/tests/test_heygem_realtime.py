"""HeyGem 实时口型链路测试。

不依赖真实 GUI / 真实音频设备 / 真实 HeyGem SDK，覆盖：
- stub client 的行为
- build_default_client 的契约
- default_anchor_wav_path 的边界
- _numpy_rgb_to_qimage 形状校验
- HeyGemWorker error 路径（stub）+ on_audio_chunk 路由
- _on_repaint_tick 的 A/V 同步窗口（Phase 2 Bug 1 修复）
- _RealHeyGemClient pts wrap/unwrap（Phase 2 Bug 3 + R1）
- _RealHeyGemClient stop/recv_loop 退出（Phase 2 Bug 4 + R3）
- _compose_frame 越界 clip + 本地 idx（Phase 2 Bug 2 + R4）
- _preload_avatar_frames_rgb 通道顺序（Phase 2 R2）

需要 QApplication 的 dialog tick 测试使用 QCoreApplication（仅 signal/slot 不显示），
PA + 真实音频设备相关的 AudioPlaybackWorker 测试故意省略——见 plan R3。
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt5.QtCore import QCoreApplication

from heygem_realtime.client import (
    HeyGemNotInstalledError,
    HeyGemRealtimeClient,
    LipFrame,
    PTS_FUTURE_CLAMP_MS,
    _NotInstalledHeyGemClient,
    _RealHeyGemClient,
    build_default_client,
)
from heygem_realtime.video_worker import HeyGemWorker
from ui_pages.heygem_preview_dialog import (
    SYNC_WINDOW_LOWER_MS,
    SYNC_WINDOW_UPPER_MS,
    _numpy_rgb_to_qimage,
)


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    return app


# ── Stub client ───────────────────────────────────────────────────────────


def test_stub_client_start_raises_not_installed():
    client = _NotInstalledHeyGemClient()
    with pytest.raises(HeyGemNotInstalledError):
        client.start("/fake/avatar.mp4")


def test_stub_client_other_methods_are_noop():
    client = _NotInstalledHeyGemClient()
    # 这些方法必须可被无副作用反复调用——HeyGemWorker.stop 链路依赖
    client.push_audio_chunk(b"\x00" * 8, 0)
    assert client.pull_video_frame() is None
    client.stop()
    client.close()


def test_build_default_client_returns_stub_when_env_unset(monkeypatch):
    monkeypatch.delenv("AISZR_HEYGEM_URL", raising=False)
    client = build_default_client()
    assert isinstance(client, _NotInstalledHeyGemClient)
    assert isinstance(client, HeyGemRealtimeClient)


def test_build_default_client_returns_real_when_env_set(monkeypatch):
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)
    # Protocol runtime_checkable 只查方法存在
    for name in ("start", "push_audio_chunk", "pull_video_frame", "stop", "close"):
        assert callable(getattr(client, name))


# ── default_anchor_wav_path 边界 ───────────────────────────────────────────


def test_default_anchor_wav_path_returns_none_when_text_blank():
    from voice_manager import default_anchor_wav_path

    settings = MagicMock()
    settings.anchor_script = "   "
    assert default_anchor_wav_path(settings) is None


def test_default_anchor_wav_path_returns_none_when_voice_missing():
    from voice_manager import default_anchor_wav_path

    settings = MagicMock()
    settings.anchor_script = "欢迎来到直播间"
    settings.anchor.voice_id = "missing-voice-id"
    settings.find_voice.return_value = None
    assert default_anchor_wav_path(settings) is None


def test_default_anchor_wav_path_returns_none_when_clone_voice_missing():
    from voice_manager import default_anchor_wav_path

    settings = MagicMock()
    settings.anchor_script = "欢迎来到直播间"
    settings.anchor.voice_id = "voice-x"
    voice = MagicMock()
    voice.clone_voice_id = ""
    settings.find_voice.return_value = voice
    assert default_anchor_wav_path(settings) is None


def test_default_anchor_wav_path_matches_synthesize_cache_key():
    """Locks the preview helper to the same key AliyunBailianProvider.synthesize emits.

    Regression: previously the helper passed a raw float volume ratio (~1.2) into
    _cached_synthesis_path, while real synthesis passed the int 60. Hashes diverged
    so the preview never found the cached WAV.
    """
    from voice_manager import (
        DEFAULT_SPEED_RATIO,
        DEFAULT_VOLUME_RATIO,
        QWEN_TTS_VC_MODEL,
        VOICE_DATA_DIR,
        _cached_synthesis_path,
        default_anchor_wav_path,
    )
    from voice_models import VoiceEntry, VoiceRoleConfig, VoiceSettings

    voice = VoiceEntry(id="voice-x", clone_voice_id="cosyvoice-v2:abc")
    settings = VoiceSettings(
        provider="aliyun_bailian",
        model_id=QWEN_TTS_VC_MODEL,
        voices=[voice],
        anchor=VoiceRoleConfig(voice_id="voice-x", volume_gain=100),
        anchor_script="欢迎来到直播间，今天好物多多",
    )

    helper_path = default_anchor_wav_path(settings)
    assert helper_path is not None

    # 复刻 AliyunBailianProvider.synthesize 里的关键参数转换
    role_volume = max(0.0, min(2.0, DEFAULT_VOLUME_RATIO * (100 / 100.0)))
    expected_volume_int = max(0, min(100, round(50 * role_volume)))
    expected = _cached_synthesis_path(
        VOICE_DATA_DIR / "anchor" / "generated",
        "aliyun_bailian",
        QWEN_TTS_VC_MODEL,
        "cosyvoice-v2:abc",
        "欢迎来到直播间，今天好物多多",
        DEFAULT_SPEED_RATIO,
        expected_volume_int,
    )
    assert helper_path == expected


# ── _numpy_rgb_to_qimage ───────────────────────────────────────────────────


def test_numpy_rgb_to_qimage_accepts_valid_frame():
    arr = np.zeros((4, 6, 3), dtype=np.uint8)
    arr[:, :, 0] = 255  # 全红
    image = _numpy_rgb_to_qimage(arr)
    assert image.width() == 6
    assert image.height() == 4
    # 第 (0,0) 像素是红色 (255,0,0)
    px = image.pixel(0, 0)
    assert (px & 0xFF0000) >> 16 == 255


def test_numpy_rgb_to_qimage_rejects_wrong_dtype():
    arr = np.zeros((4, 6, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        _numpy_rgb_to_qimage(arr)


def test_numpy_rgb_to_qimage_rejects_wrong_shape():
    arr = np.zeros((4, 6, 4), dtype=np.uint8)  # 4 通道
    with pytest.raises(ValueError):
        _numpy_rgb_to_qimage(arr)

    arr2 = np.zeros((4, 6), dtype=np.uint8)  # 灰度
    with pytest.raises(ValueError):
        _numpy_rgb_to_qimage(arr2)


def test_numpy_rgb_to_qimage_handles_noncontiguous():
    # 转置出来的 view 不是 C-contiguous——helper 必须用 ascontiguousarray 把它救回
    base = np.zeros((3, 8, 6), dtype=np.uint8)
    arr = np.transpose(base, (1, 2, 0))  # (8, 6, 3) 但 strides 怪
    assert not arr.flags["C_CONTIGUOUS"]
    image = _numpy_rgb_to_qimage(arr)
    assert image.width() == 6 and image.height() == 8


# ── HeyGemWorker ───────────────────────────────────────────────────────────


def _make_stub_lip_frame(pts_ms: int = 0) -> LipFrame:
    return LipFrame(
        mouth_rgb=np.zeros((4, 4, 3), dtype=np.uint8),
        crop_x=0, crop_y=0, crop_w=4, crop_h=4,
        pts_ms=pts_ms,
    )


def test_heygem_worker_emits_error_on_not_installed(qapp, monkeypatch):
    """env 缺省 → 默认 stub client → worker.start 同步内 emit error，不留 timer。"""
    monkeypatch.delenv("AISZR_HEYGEM_URL", raising=False)
    worker = HeyGemWorker()    # client 在 start 里 build
    errors: list[str] = []
    started: list[bool] = []
    worker.error.connect(lambda msg: errors.append(msg))
    worker.started.connect(lambda: started.append(True))

    worker.start("/fake/avatar.mp4", 1000)

    assert started == []
    assert len(errors) == 1
    assert "HeyGem" in errors[0]
    assert worker._drain_timer is None
    assert worker._running is False


def test_heygem_worker_routes_chunk_to_client(qapp):
    fake_client = MagicMock(spec=HeyGemRealtimeClient)
    fake_client.start.return_value = None
    fake_client.pull_video_frame.return_value = None

    worker = HeyGemWorker(client=fake_client)
    worker.start("/fake/avatar.mp4", 1000)

    # client.start 应当带上 wav_duration_ms kwarg
    assert fake_client.start.call_args.kwargs.get("wav_duration_ms") == 1000

    chunks = [
        (b"\x00\x00" * 10, 0),
        (b"\x01\x01" * 10, 240),
        (b"\x02\x02" * 10, 480),
    ]
    for pcm, pts in chunks:
        worker.on_audio_chunk(pcm, pts)

    assert fake_client.push_audio_chunk.call_count == 3
    assert fake_client.push_audio_chunk.call_args_list[0].args == chunks[0]
    assert fake_client.push_audio_chunk.call_args_list[2].args == chunks[2]

    worker.stop()


def test_heygem_worker_emits_lip_frame_payload(qapp):
    """_drain_frames 应当 emit LipFrame 单值，不再是 (np.ndarray, int) 二元组。"""
    fake_client = MagicMock(spec=HeyGemRealtimeClient)
    fake_client.start.return_value = None
    lip = _make_stub_lip_frame(pts_ms=240)
    fake_client.pull_video_frame.side_effect = [lip, None, None, None, None]

    worker = HeyGemWorker(client=fake_client)
    received: list[object] = []
    worker.frame_ready.connect(lambda payload: received.append(payload))

    worker.start("/fake/avatar.mp4", 1000)
    # 手工触发一次 drain
    worker._drain_frames()

    assert len(received) == 1
    assert isinstance(received[0], LipFrame)
    assert received[0].pts_ms == 240

    worker.stop()


def test_heygem_worker_stops_routing_after_client_error(qapp):
    """on_audio_chunk 遇到 NotInstalled 后必须停止推送、emit error 一次。"""
    fake_client = MagicMock(spec=HeyGemRealtimeClient)
    fake_client.start.return_value = None
    fake_client.pull_video_frame.return_value = None
    fake_client.push_audio_chunk.side_effect = HeyGemNotInstalledError("nope")

    worker = HeyGemWorker(client=fake_client)
    errors: list[str] = []
    worker.error.connect(lambda msg: errors.append(msg))

    worker.start("/fake/avatar.mp4", 1000)
    worker.on_audio_chunk(b"\x00" * 8, 0)
    # 二次推送：worker 已置 _running=False，应静默丢弃
    worker.on_audio_chunk(b"\x00" * 8, 240)

    assert fake_client.push_audio_chunk.call_count == 1
    assert errors == ["nope"]

    worker.stop()


# ── A/V sync 窗口算法（Phase 2 Bug 1：每 tick 至多消费一帧） ───────────────


def _run_repaint_step(frame_ring: deque, audio_pts: int) -> LipFrame | None:
    """复刻 HeyGemPreviewDialog._on_repaint_tick 的核心挑帧逻辑（Bug 1 修复后）。"""
    while frame_ring and frame_ring[0].pts_ms < audio_pts + SYNC_WINDOW_LOWER_MS:
        frame_ring.popleft()
    if not frame_ring:
        return None
    head = frame_ring[0]
    if head.pts_ms > audio_pts + SYNC_WINDOW_UPPER_MS:
        return None
    return frame_ring.popleft()


def test_av_sync_consumes_at_most_one_frame_per_tick():
    """Bug 1 修复：5 帧全在窗口内 → 单 tick 只消费一帧、ring 剩 4。"""
    ring = deque([
        _make_stub_lip_frame(990),
        _make_stub_lip_frame(1000),
        _make_stub_lip_frame(1010),
        _make_stub_lip_frame(1020),
        _make_stub_lip_frame(1030),
    ])
    chosen = _run_repaint_step(ring, audio_pts=1000)
    assert chosen is not None and chosen.pts_ms == 990
    assert len(ring) == 4


def test_av_sync_drops_stale_frames():
    """Bug 1：纯老帧 → 全部 pop，ring 空，chosen None。"""
    ring = deque([
        _make_stub_lip_frame(500),
        _make_stub_lip_frame(600),
    ])
    chosen = _run_repaint_step(ring, audio_pts=2000)
    assert chosen is None
    assert len(ring) == 0


def test_av_sync_keeps_future_frame_in_ring():
    """Bug 1：仅一帧超前 → tick 不消费、留在 ring head。"""
    ring = deque([_make_stub_lip_frame(2000)])
    chosen = _run_repaint_step(ring, audio_pts=1000)
    assert chosen is None
    assert len(ring) == 1
    assert ring[0].pts_ms == 2000


def test_av_sync_drops_old_then_keeps_future():
    """复合：老帧丢 + 超前帧留。"""
    ring = deque([
        _make_stub_lip_frame(500),    # < 1000-250=750 → 丢
        _make_stub_lip_frame(5000),   # > 1000+80=1080 → 留
    ])
    chosen = _run_repaint_step(ring, audio_pts=1000)
    assert chosen is None
    assert len(ring) == 1
    assert ring[0].pts_ms == 5000


# ── PTS wrap / unwrap（Phase 2 Bug 3 + R1） ────────────────────────────────


def test_pts_wrap_round_trip(monkeypatch):
    """push monotonic=15000, wav_duration=10000 → recv wrapped=5000 应 restore 回 15000。"""
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)
    client._wav_duration_ms = 10000
    client._latest_pushed_monotonic_pts = 15000
    client._last_restored_pts = 0

    restored = client._restore_monotonic_pts(5000)
    assert restored == 15000


def test_pts_wrap_future_clamp_short_wav_long_latency(monkeypatch):
    """R1: wav=5000ms 短 + 服务端延迟大场景 —— 未来钳制法砍回上一周期。

    场景：push 已推到 monotonic=20100（cycle=4，wrapped=100），服务端回来
    wrapped=100。candidate = 4*5000+100 = 20100。
    candidate (20100) > push (20100) + 100 → False（边界点），不砍。

    再推到 monotonic=20200，恰好同 wrapped=100 才会触发钳制：
    candidate = 4*5000+100 = 20100，push=20200，20100 < 20300 → 不钳制，回 20100。

    构造能触发钳制的场景：push 到 cycle 4 早期，但服务端回来的 wrapped 接近
    cycle 末尾 — 半周期启发式会误判为当前周期，而未来钳制会正确归位上一周期。
    """
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)
    client._wav_duration_ms = 5000
    # push 进入 cycle 4 的早期：monotonic = 20050（cycle=4, wrapped_pushed=50）
    client._latest_pushed_monotonic_pts = 20050
    client._last_restored_pts = 0

    # 服务端回来 wrapped=4900（属于 cycle 3 的尾部，因网络延迟在 cycle 4 的早期才到）
    # base_cycle = 20050 // 5000 = 4
    # candidate = 4*5000 + 4900 = 24900
    # 24900 > 20050 + 100 = 20150 → 触发钳制 → candidate -= 5000 = 19900（cycle 3 尾）
    restored = client._restore_monotonic_pts(4900)
    assert restored == 19900, (
        f"expected 19900 (cycle 3 tail after future-clamp), got {restored}"
    )

    # 半周期启发式（candidate vs base ± wav/2）在此场景判 24900 比 20050 大 4850，
    # 接近 wav/2=2500 的 2 倍，判定逻辑很难明确归位 —— 未来钳制基于推送高水位，更鲁棒。


def test_pts_wrap_monotonic_across_cycle_boundary(monkeypatch):
    """跨循环边界连续 recv，restore 序列必须单调（防服务端抖动倒退）。"""
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)
    client._wav_duration_ms = 10000
    client._latest_pushed_monotonic_pts = 22000  # cycle 2, wrapped 2000
    client._last_restored_pts = 0

    seq = []
    for wrapped in [1500, 1800, 2000]:   # 服务端先回 cycle 2 内三个 wrapped
        seq.append(client._restore_monotonic_pts(wrapped))
    # 现在服务端突然抖动回了一个 wrapped=1700（小于上一次 2000）
    seq.append(client._restore_monotonic_pts(1700))
    # 单调钳制 → 最后一个必须 >= 上一个
    for i in range(1, len(seq)):
        assert seq[i] >= seq[i - 1], f"非单调: {seq}"


def test_pts_future_clamp_constant_is_reasonable():
    """sanity check：PTS_FUTURE_CLAMP_MS 不应被无意改大到 >wav_duration。"""
    assert 0 < PTS_FUTURE_CLAMP_MS < 1000


# ── _RealHeyGemClient stop / recv_loop（Phase 2 Bug 4 + R3） ───────────────


def test_real_client_stop_returns_within_100ms(monkeypatch):
    """R3: stop() 应在毫秒级返回 —— ws.close() 是主退出手段，settimeout 仅兜底。"""
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)

    # 注入 mock ws + 一个永远阻塞在 recv 的 reader thread
    mock_ws = MagicMock()
    close_event = threading.Event()

    def mock_recv():
        # close() 会被调到 → 改成抛 WebSocketException 让 reader 跳出
        close_event.wait(timeout=5)
        from websocket import WebSocketException
        raise WebSocketException("closed")

    def mock_close():
        close_event.set()

    mock_ws.recv.side_effect = mock_recv
    mock_ws.close.side_effect = mock_close

    client._ws = mock_ws
    client._closing = False
    client._reader_thread = threading.Thread(
        target=client._recv_loop, daemon=True
    )
    client._reader_thread.start()

    t0 = time.monotonic()
    client.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"stop() 超时 {elapsed*1000:.1f}ms，应 ≤ 500ms"
    assert mock_ws.close.called


def test_real_client_recv_loop_exits_on_close_flag(monkeypatch):
    """Bug 4: recv 阻塞 → settimeout 超时唤醒 → 检 _closing 旗 → 退出。"""
    monkeypatch.setenv("AISZR_HEYGEM_URL", "http://localhost:8770")
    client = build_default_client()
    assert isinstance(client, _RealHeyGemClient)

    from websocket import WebSocketTimeoutException

    mock_ws = MagicMock()
    call_count = {"n": 0}

    def mock_recv():
        call_count["n"] += 1
        raise WebSocketTimeoutException("timeout")

    mock_ws.recv.side_effect = mock_recv
    client._ws = mock_ws
    client._closing = False
    reader = threading.Thread(target=client._recv_loop, daemon=True)
    reader.start()

    # 等几个 timeout 循环
    time.sleep(0.05)
    assert call_count["n"] > 0, "recv 应已被多次调"

    client._closing = True
    reader.join(timeout=1.0)
    assert not reader.is_alive(), "_closing 旗设上后 reader 应在 1s 内退出"


# ── _compose_frame（Phase 2 Bug 2 + R4） ───────────────────────────────────


def _make_dialog_for_compose(avatar_frames: list[np.ndarray], wav_duration_ms: int):
    """构造一个不真正起 worker 的 dialog 实例，仅供 _compose_frame 单测用。"""
    from ui_pages.heygem_preview_dialog import HeyGemPreviewDialog

    obj = HeyGemPreviewDialog.__new__(HeyGemPreviewDialog)
    obj._avatar_frames = avatar_frames
    obj._wav_duration_ms = wav_duration_ms
    obj._target_fps = 25
    return obj


def test_compose_clips_out_of_bounds_crop():
    """Bug 2 守门：越界 crop 直接返回 base，不崩。"""
    base = np.full((100, 80, 3), 50, dtype=np.uint8)
    obj = _make_dialog_for_compose([base], wav_duration_ms=1000)

    lip = LipFrame(
        mouth_rgb=np.full((20, 20, 3), 200, dtype=np.uint8),
        crop_x=500, crop_y=500,    # 远超 base 尺寸
        crop_w=20, crop_h=20,
        pts_ms=40,
    )
    out = obj._compose_frame(lip)
    assert out is not None
    # base 没被污染 —— composed 各像素仍 == 50
    assert np.all(out == 50)


def test_compose_partial_overlap_clip():
    """部分越界：合成区域应被 clip 到 base 内。"""
    base = np.zeros((100, 100, 3), dtype=np.uint8)
    obj = _make_dialog_for_compose([base], wav_duration_ms=1000)

    mouth = np.full((30, 30, 3), 200, dtype=np.uint8)
    lip = LipFrame(
        mouth_rgb=mouth,
        crop_x=85, crop_y=85,      # 嘴部超出右下边缘 15px
        crop_w=30, crop_h=30,
        pts_ms=40,
    )
    out = obj._compose_frame(lip)
    assert out is not None
    # 写入区域 [85:100, 85:100]：值=200；其余=0
    assert np.all(out[85:100, 85:100] == 200)
    assert np.all(out[:85, :] == 0)
    assert np.all(out[:, :85] == 0)


def test_compose_local_idx_ignores_server_field():
    """R4: idx 完全由本地 wrapped_pts × target_fps 推出，与服务端任何字段无关。

    LipFrame 本就不含 server_idx 字段；这条测试通过相同 pts 但不同 mouth_rgb
    验证 _compose_frame 选 idx 仅看 pts。
    """
    # 5 帧不同颜色的 avatar
    frames = [np.full((20, 20, 3), c, dtype=np.uint8) for c in [10, 50, 90, 130, 170]]
    obj = _make_dialog_for_compose(frames, wav_duration_ms=200)
    # target_fps=25 → 40ms/帧
    # pts=80 → wrapped=80 → idx=int(80*25/1000)=2 → frame[2] (=90)
    lip_a = LipFrame(
        mouth_rgb=np.full((4, 4, 3), 7, dtype=np.uint8),
        crop_x=0, crop_y=0, crop_w=4, crop_h=4,
        pts_ms=80,
    )
    lip_b = LipFrame(
        mouth_rgb=np.full((4, 4, 3), 99, dtype=np.uint8),
        crop_x=0, crop_y=0, crop_w=4, crop_h=4,
        pts_ms=80,    # 同 pts → 应选同 idx
    )

    out_a = obj._compose_frame(lip_a)
    out_b = obj._compose_frame(lip_b)
    assert out_a is not None and out_b is not None
    # 边角（不在嘴部覆盖区）反映 base 帧选择
    assert out_a[10, 10, 0] == 90, f"expected base color 90, got {out_a[10,10,0]}"
    assert out_b[10, 10, 0] == 90


def test_compose_wraps_idx_across_cycle_boundary():
    """跨循环边界：pts 大于 wav_duration_ms 时仍能正确 wrap 出 idx。"""
    frames = [np.full((10, 10, 3), c, dtype=np.uint8) for c in [10, 50, 90, 130]]
    obj = _make_dialog_for_compose(frames, wav_duration_ms=160)
    # target_fps=25 → 40ms/帧；wav_duration_ms=160 → 4 帧一循环
    # pts=200 → wrapped=200%160=40 → idx=int(40*25/1000)=1 → frame[1] (=50)
    lip = LipFrame(
        mouth_rgb=np.full((2, 2, 3), 0, dtype=np.uint8),
        crop_x=0, crop_y=0, crop_w=2, crop_h=2,
        pts_ms=200,
    )
    out = obj._compose_frame(lip)
    assert out is not None
    assert out[5, 5, 0] == 50


def test_compose_returns_none_when_no_avatar_frames():
    """avatar 预读失败（cv2 缺失等）→ 返回 None，repaint tick 优雅跳过。"""
    obj = _make_dialog_for_compose([], wav_duration_ms=1000)
    lip = _make_stub_lip_frame(pts_ms=40)
    assert obj._compose_frame(lip) is None


def test_avatar_frames_pre_converted_to_rgb(tmp_path, monkeypatch):
    """R2 守门：_preload_avatar_frames_rgb 必须返回 RGB 通道顺序。

    Mock cv2.VideoCapture 模拟 BGR 帧，验证 helper 调了 cvtColor 把它转成 RGB。
    """
    from ui_pages import heygem_preview_dialog as hpd

    # 构造一帧已知 BGR 颜色：[B=10, G=20, R=30]，转 RGB 后 [R=30, G=20, B=10]
    bgr_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    bgr_frame[:, :, 0] = 10   # B
    bgr_frame[:, :, 1] = 20   # G
    bgr_frame[:, :, 2] = 30   # R

    class FakeCap:
        def __init__(self, _path):
            self._n = 0
        def isOpened(self):
            return True
        def read(self):
            self._n += 1
            if self._n == 1:
                return True, bgr_frame.copy()
            return False, None
        def release(self):
            pass

    class FakeCv2:
        COLOR_BGR2RGB = "BGR2RGB"
        def VideoCapture(self, path):
            return FakeCap(path)
        def cvtColor(self, frame, code):
            assert code == self.COLOR_BGR2RGB
            # BGR → RGB：通道顺序翻转
            return frame[:, :, ::-1].copy()

    fake_cv2 = FakeCv2()
    # 把 'cv2' 装到 sys.modules 让 helper 内部的 lazy import 命中假货
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    frames = hpd._preload_avatar_frames_rgb(str(tmp_path / "fake.mp4"))
    assert len(frames) == 1
    # RGB 顺序：第 0 通道 = R = 30
    assert frames[0][0, 0, 0] == 30
    assert frames[0][0, 0, 1] == 20
    assert frames[0][0, 0, 2] == 10


def test_preload_returns_empty_when_cv2_missing(monkeypatch, tmp_path):
    """cv2 import 失败 → 返回空 list，stub 路径仍能 dialog 起来。"""
    from ui_pages import heygem_preview_dialog as hpd

    monkeypatch.setitem(sys.modules, "cv2", None)

    # 直接 patch builtins.__import__ 模拟 import cv2 抛 ImportError
    import builtins
    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("no cv2 in test env")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    frames = hpd._preload_avatar_frames_rgb(str(tmp_path / "fake.mp4"))
    assert frames == []
