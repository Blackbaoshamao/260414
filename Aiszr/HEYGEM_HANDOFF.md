# HeyGem 实时口型 — Handoff (2026-05-22)

> 这是一份给"下一轮对话"的交接文档。Stub 阶段已经全部落地并通过真实 GUI 验证；
> 剩下的就是把 stub 换成真实 HeyGem SDK，UI / worker / dialog 不需要动。

原始 plan：`C:/Users/STG-DEVPC004/.claude/plans/recursive-meandering-snowflake.md`

---

## ⚠️ 架构转向（2026-05-25）：batch 才是真路径

走完一轮真实部署调研后，确认 **HeyGem 官方没有流式接口**：

- 官方 `docker-compose-5090.yml` 暴露的 `:8383` 是 `POST /easy/submit` + `GET /easy/query`，
  submit-and-wait（参考 [deploy/heygem/HeyGem/src/main/api/f2f.js](deploy/heygem/HeyGem/src/main/api/f2f.js)、
  [deploy/heygem/HeyGem/src/main/service/video.js](deploy/heygem/HeyGem/src/main/service/video.js)）。
- 下面 B/C/D 节描述的"WS push_audio_chunk → pull_video_frame"是 plan
  `recursive-meandering-snowflake.md` 设计阶段的 Protocol stub，不是真实 SDK。
- 直播助手延迟预算 2-5 秒，batch 完全够用 — 全 WAV → 全 mp4 → ffmpeg HLS → OBS。

**新的真实接入路径**：

```
TTS 全段 WAV  →  HeyGemBatchClient.synthesize()  →  mp4 绝对路径
              [Aiszr/heygem_realtime/batch_client.py]
                  ↓
              已有 digital_human_pipeline 的 start_hls_push
              （把原本的"绿幕循环 + WAV"换成 HeyGem 的 mp4）
```

| 文件 | 角色 |
|------|------|
| [heygem_realtime/batch_client.py](Aiszr/heygem_realtime/batch_client.py) | 同步阻塞 submit+poll 客户端，**真正接入用的就是它** |
| [heygem_realtime/client.py](Aiszr/heygem_realtime/client.py) | 原 WS Protocol stub，**保留**（21 个单元测试仍依赖） |
| [deploy/heygem/benchmark.py](Aiszr/deploy/heygem/benchmark.py) | docker 起来后跑这个量真实 fps，决定段长调参 |
| [deploy/heygem/server.py](Aiszr/deploy/heygem/server.py) | 早期 WS mock，给 smoke_test.py 当对端，**部署完可删** |
| [deploy/heygem/smoke_test.py](Aiszr/deploy/heygem/smoke_test.py) | 同上，给 server.py 测的 |

**Aiszr 现状**（`digital_human_pipeline.py`）：TTS → ffmpeg HLS（绿幕循环 + WAV 合成）→ HTTP :8780 → OBS。
**接入点**：在 `_synthesize_audio` 与 `start_hls_push` 之间塞一步 `HeyGemBatchClient.synthesize()`，
把 `start_hls_push` 的输入从"绿幕 mp4 + WAV"换成"HeyGem 出来的口型 mp4"。

**部署 checklist**（按顺序）：

1. **[用户做]** 跑 `D:\tmp\docker-dl\Docker Desktop_4.73.0_Machine_X64_exe_en-US.exe`（需要管理员），
   走完 WSL2 安装 + 重启 + 接受许可。完成标准：任意终端里 `docker --version` 有输出。
2. **[自主]** `docker pull guiji2025/duix.avatar-5090 guiji2025/fish-speech-5090`（约 70GB）。
3. **[自主]** 从 `deploy/heygem/HeyGem/deploy/` 跑 `docker compose -f docker-compose-5090.yml up -d`，
   验证 `:8383` 和 `:18180` 起来。
4. **[自主]** 把一个 anchor mp4 放到 `D:/duix_avatar_data/face2face/temp/anchor.mp4`，
   跑 `python deploy/heygem/benchmark.py --gen-wav --avatar anchor.mp4` 量真实 fps。
5. **[自主]** 把 `HeyGemBatchClient` 接进 `digital_human_pipeline.py` 的状态机。

---

## 当前位置（旧 plan 的 WS 流式路径，已偏离真实接入路径）

```
[已完成]   Step 1  暴露 cached_synthesis_path + default_anchor_wav_path
[已完成]   Step 2  SDK 抽象 + _NotInstalledHeyGemClient stub
[已完成]   Step 3  AudioPlaybackWorker（PA 回调 → deque → 5ms Qt drain → chunk_ready）
[已完成]   Step 4  HeyGemWorker（thread-local CUDA、5ms pull、frame_ready）
[已完成]   Step 5  HeyGemPreviewDialog（标准 Windows chrome、_FramePainter hover-inert）
[已完成]   Step 6  VoiceConfigPage 按钮 + ui.py 路由 + homepage selected_out_index 访问器
[已完成]   单元测试 tests/test_heygem_realtime.py（21 passed）
[已完成]   真实 GUI 走通 stub 路径：错误覆盖正常显示

[待用户验]  A. 音频链路独立播出（错误覆盖期间扬声器是否在播 WAV）
[阻塞中]    B. 本地部署 HeyGem 实时 SDK（环境工程，不在代码仓里）
[阻塞中]    C. 实现 _RealHeyGemClient（只改 build_default_client，UI/worker 0 改动）
[阻塞中]    D. A/V 同步调参（拿到真实帧后量滞后，必要时改 SYNC_WINDOW_*）
```

---

## 接下来怎么做（按顺序）

### A. 音频链路独立验证（30 秒，先做这一步）

在 AI 语音页点"启动口型预览"，弹窗出来后**仔细听**主页选的那个扬声器：

- **能听见** WAV 在播 → 音频路径独立工作正常，可以放心走 B
- **听不见** → 先排查：
  - 看 log 里 `AudioPlaybackWorker started: wav=... device=-1`（-1 表示走默认设备）
  - 看是否抛 "WAV 格式不匹配：期望 24000Hz mono s16le" 错误覆盖
  - 看是否抛 "无法打开音频输出流" 错误覆盖
  - 如果错误覆盖只有 HeyGem 的、没看到音频错误，但又听不见声 → 可能是 device_index=-1 在你的环境里 PA 不接受默认值，要把 selected_out_index 的 fallback 逻辑改成显式查 `PyAudio().get_default_output_device_info()["index"]`

### B. 本地部署 HeyGem 实时 SDK

这步在代码之外。需要：

1. 拿到 HeyGem 实时口型 SDK（官方 release / 源码 / Docker，由用户决定）
2. 装好 CUDA / TensorRT 等依赖
3. 跑通他们的 demo 确认环境 OK
4. **告诉下一轮对话 SDK 的接入方式**：
   - Python 包名？还是动态库（.dll / .so）？
   - 接口风格：函数调用？socket？HTTP？gRPC？
   - 模型权重路径
   - avatar 视频要什么格式（分辨率、编码、长度）
   - 实测延迟：从 push_audio_chunk 到对应 pull_video_frame 大概多少 ms

这些信息直接决定 `_RealHeyGemClient` 怎么写。

### C. 实现 _RealHeyGemClient

**只改一个文件**：`Aiszr/heygem_realtime/client.py`

新增一个类满足 `HeyGemRealtimeClient` Protocol，5 个方法的契约见下面。然后改 `build_default_client()`：

```python
def build_default_client() -> HeyGemRealtimeClient:
    # 旧：return _NotInstalledHeyGemClient()
    return _RealHeyGemClient()
```

**禁止改动**：`audio_worker.py`、`video_worker.py`、`heygem_preview_dialog.py`、`ui.py` 里的 HeyGem 路由。这些都是经过真实 GUI 验证过的，改了就要重新走一遍 smoke。

### D. A/V 同步调参（可能不需要）

真实帧流跑起来后，看口型是否对得上音频。如果明显错位：

- 在 `_drain_frames`（[video_worker.py](Aiszr/heygem_realtime/video_worker.py#L108)）和 `_on_repaint_tick`（[heygem_preview_dialog.py](Aiszr/ui_pages/heygem_preview_dialog.py#L218)）里加 `time.monotonic()` 打点测从 push 到 paint 的实际延迟
- 改 [heygem_preview_dialog.py:31-32](Aiszr/ui_pages/heygem_preview_dialog.py#L31-L32) 的 `SYNC_WINDOW_LOWER_MS / UPPER_MS`
- 默认窗口 `[-250ms, +80ms]` 是基于 25fps 的理论值，真实 SDK 可能需要扩大或偏移

---

## 核心文件 & 契约

### Protocol contract（实现 _RealHeyGemClient 必读）

`Aiszr/heygem_realtime/client.py` `HeyGemRealtimeClient`：

```python
def start(self, avatar_video_path: str, sample_rate: int = 24000, target_fps: int = 25) -> None: ...
def push_audio_chunk(self, pcm_s16le_mono: bytes, pts_ms: int) -> None: ...
def pull_video_frame(self) -> tuple[np.ndarray, int] | None: ...
def stop(self) -> None: ...
def close(self) -> None: ...
```

约束（必须遵守）：

- **五个方法全部在同一线程被调**（HeyGemWorker 自己的 QThread）。实现内部**不要再开线程碰 CUDA**，否则 TRT context 会绑错。
- `push_audio_chunk` / `pull_video_frame` 必须**非阻塞**。`pull_video_frame` 没帧就 return None，外面 5ms 后再来。
- `pts_ms` 是**透传身份标签**。push 进去 pts=X 的 chunk，对应吐出来的帧 pts 必须 ≈ X（或 X + 内部固定延迟），保持单调递增。**跨循环边界绝不能归零**，因为 audio_worker 自己也维护单调 PTS。
- 帧格式严格：`np.ndarray` shape `(H, W, 3)`、`dtype=uint8`、**RGB 顺序**（不是 BGR）。错了直接 paintEvent 颜色翻车。
- `close()` 必须在 `start()` 同一线程调用，且只能在 `stop()` 之后。`HeyGemWorker.stop` 已经保证这点。

### 文件地图

```
Aiszr/
  heygem_realtime/
    __init__.py            导出
    client.py              ★ 唯一要改的文件（加 _RealHeyGemClient + 改 build_default_client）
    audio_worker.py        PA 回调 + Qt timer drain，不要改
    video_worker.py        SDK pull + frame_ready，不要改
  ui_pages/
    heygem_preview_dialog.py   弹窗，不要改
    voiceconfigpage.py         line 62 信号 + line 351 按钮 + line 845 emit slot
    homepage.py                line 330 selected_out_index() — 直接读 combo.currentData()
  voice_manager.py         line 117 cached_synthesis_path 别名 + line 120 default_anchor_wav_path
  ui.py                    line 1838 路由 + line 2355 _on_open_heygem_preview + line 2406 级联关闭
  tests/
    test_heygem_realtime.py    18 个测试
```

---

## 已经踩过的坑（别再踩回去）

> 这些都是真实 GUI 测试中发现并修过的，写在这里防止下一轮"清理"时改回去。

### 坑 1：volume cache key 不一致

`default_anchor_wav_path` 必须把 volume float 比例转成 `max(0, min(100, round(50 * volume)))` **整数 0-100**，跟真实合成时 `AliyunBailianProvider.synthesize` 用的算法保持一致。否则 hash 不一样 → 缓存 WAV 在硬盘但路径找不到。

位置：[voice_manager.py:138-148](Aiszr/voice_manager.py#L138-L148)
锁定测试：`test_default_anchor_wav_path_matches_synthesize_cache_key`

### 坑 2：selected_out_index 跟 combo 脱钩

不要再用影子状态 `self._selected_out_index`。`selected_out_index()` 必须**直接读 `self._spk_combo.currentData()`**，combo 显示什么就用什么。

位置：[homepage.py:330-344](Aiszr/ui_pages/homepage.py#L330-L344)

### 坑 3：device_index=-1 不是"没选"

`_enum_via_qt_multimedia` 这条 fallback 路径把所有设备的 index 写成 -1（Qt 拿不到 pyaudio index）。-1 在我们的契约里表示"用系统默认输出"。`AudioPlaybackWorker.start` 已经 `if out_device_index is not None and out_device_index >= 0` 才把 index 传给 PA。

`ui.py` 的 Toast 校验**只看 combo.count() == 0**，不看 -1。

位置：[ui.py:2380-2389](Aiszr/ui.py#L2380-L2389)、[audio_worker.py:95-97](Aiszr/heygem_realtime/audio_worker.py#L95-L97)

### 坑 4：弹窗 chrome — 标准 Windows 标题栏

用户中途改主意，弹窗**必须有**最小化/最大化/关闭三按钮。不要再加 `Qt.FramelessWindowHint` 或 `Qt.WindowStaysOnTopHint`。

视频区域（`_FramePainter`）的 `WA_TransparentForMouseEvents=True` **不要动** — hover-inert 约束仍然有效，只是约束的是视频区域内的鼠标，不是窗口 chrome。

位置：[heygem_preview_dialog.py:97-105](Aiszr/ui_pages/heygem_preview_dialog.py#L97-L105)

### 坑 5：painter 子控件收不到鼠标点击

任何 painter 的子控件（按钮、可点击 label）都会因为 `WA_TransparentForMouseEvents=True` 收不到鼠标事件 — Qt 的子控件不能"豁免"父级的 mouse-transparent。

如果要在视频区上叠交互元素，**必须**把它设为 dialog 自己的子控件、悬浮在 painter 之上（用 raise_() 或更高 z-order）。

历史教训：原来 plan 设计的右下角"关闭"按钮就是这样失效的，已删除。

---

## 验证

```bash
# 单元测试
cd Aiszr
.venv/Scripts/python.exe -m pytest tests/test_heygem_realtime.py tests/test_voice_manager_cache.py -q
# 期望：21 passed
```

GUI smoke：

1. 启动 app → AI 语音页 → 看见"启动口型预览"按钮
2. 不满足前置（缺 avatar / 缺 WAV / combo 空）→ 三种 Toast 各自正确
3. 三项齐全 → 弹窗 + "数字人启动中…" → ~200ms 后 "尚未部署 HeyGem 实时 SDK…" 错误覆盖
4. **关键**：错误覆盖期间，从主页选的扬声器应该已经在播 WAV
5. 标题栏 X 能关 / Esc 能关 / 主窗口关闭能级联关
6. 关闭后再点，弹窗能重新打开（单例守门生效但不卡死）

---

## 不要碰

- `AudioPlaybackWorker._pa_callback`：PA 回调线程，append 到 deque 是原子的，**不要**在这里 emit pyqtSignal（plan.R1）
- `HeyGemWorker._thread_id` 守门：CUDA 销毁顺序错乱 = SIGSEGV
- `HeyGemPreviewDialog.closeEvent` 的清理顺序：repaint timer 停 → 断 audio→video 信号 → video stop (BlockingQueued) → video thread quit → audio stop → audio thread quit → emit closed
- `tests/test_heygem_realtime.py` 里所有 AV sync 窗口测试是回归锁，改窗口常量要同步更新测试

---

## 下一轮对话的开场建议

> 我要继续 HeyGem 实时口型项目。请先读 `Aiszr/HEYGEM_HANDOFF.md` 和 `C:/Users/STG-DEVPC004/.claude/plans/recursive-meandering-snowflake.md`。
> 当前进度：[A 已验证 / 未验证]、HeyGem 部署情况：[xxx]、SDK 接入方式：[xxx]。
> 我要做：[实现 _RealHeyGemClient / 调 A/V 同步 / ...]
