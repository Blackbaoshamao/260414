# Keyword Voice Insertion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build keyword-triggered anchor-voice speech insertion for the LiveTalking digital-human stream without rewriting the lip-sync model.

**Architecture:** Add an audio segmenter and speech scheduler in front of LiveTalking. The digital-human pipeline generates one full anchor WAV, splits it into short WAV segments, then loops those segments while allowing keyword reply WAVs to be inserted after the current segment finishes. Voice settings are snapshotted at stream start so insertion audio uses the same provider and anchor voice as the running stream.

**Segmentation clarification:** A single generated segment is normally treated as no split and falls back to the source WAV. The exception is a material silence trim: when long middle or trailing silence is removed, the segmenter may return one trimmed segment so the scheduler is not blocked by dead air before a keyword insertion.

**Tech Stack:** Python 3.10, asyncio, wave/PCM processing, PyQt5 signal wiring, pytest/pytest-asyncio, existing Aiszr voice provider and LiveTalking runtime abstractions.

---

## Scope Notes

This plan implements the scheduler and Aliyun-backed insertion path. It also adds a `local_voice` provider slot and config support, but does not select or integrate a specific local model. That remains behind the research gate from the design spec.

The current text comment injection path stays limited to the existing WeChat/视频号 keyword auto-reply path. Speech insertion is designed platform-agnostically once a message reaches keyword matching.

## File Structure

- Create `Aiszr/audio_segmenter.py`: silence-based WAV segmentation.
- Create `Aiszr/digital_human_speech_scheduler.py`: async scheduler that alternates anchor-loop audio and keyword insertion audio.
- Modify `Aiszr/livetalking_runtime.py`: expose single-WAV upload and allow startup without owning the fixed loop.
- Modify `Aiszr/digital_human_pipeline.py`: synthesize full anchor WAV, segment it, start scheduler, expose insertion enqueue method.
- Modify `Aiszr/voice_manager.py`: add non-playing anchor synthesis helper for insertion.
- Modify `Aiszr/voice_models.py`: add `local_voice` provider slot while keeping Aliyun default.
- Modify `Aiszr/ui_constants.py`: add local provider endpoint field metadata.
- Modify `Aiszr/ui.py`: pass keyword voice insertion requests to the running digital-human pipeline.
- Create/modify tests under `Aiszr/tests/`.

---

### Task 1: Add Silence-Based Audio Segmenter

**Files:**
- Create: `Aiszr/audio_segmenter.py`
- Create: `Aiszr/tests/test_audio_segmenter.py`

- [ ] **Step 1: Write failing tests for segmentation and fallback**

Create `Aiszr/tests/test_audio_segmenter.py`:

```python
import math
import wave
from pathlib import Path

from audio_segmenter import AudioSegmenter, AudioSegmenterConfig


def _write_tone_and_silence(path: Path, pattern: list[tuple[str, int]], rate: int = 16000) -> None:
    frames = bytearray()
    for kind, duration_ms in pattern:
        sample_count = int(rate * duration_ms / 1000)
        for i in range(sample_count):
            if kind == "tone":
                sample = int(12000 * math.sin(2 * math.pi * 440 * (i / rate)))
            else:
                sample = 0
            frames.extend(sample.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(bytes(frames))


def test_segmenter_splits_on_silence(tmp_path):
    src = tmp_path / "full.wav"
    _write_tone_and_silence(
        src,
        [
            ("tone", 2200),
            ("silence", 300),
            ("tone", 2300),
        ],
    )
    segmenter = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=100,
        )
    )

    segments = segmenter.segment(src, tmp_path / "segments")

    assert len(segments) == 2
    assert segments[0].name == "segment_0001.wav"
    assert segments[1].name == "segment_0002.wav"
    assert all(path.exists() and path.suffix == ".wav" for path in segments)


def test_segmenter_falls_back_to_full_wav_when_no_split(tmp_path):
    src = tmp_path / "full.wav"
    _write_tone_and_silence(src, [("tone", 2500)])
    segmenter = AudioSegmenter(
        AudioSegmenterConfig(
            silence_threshold_db=-25,
            min_segment_ms=2000,
            min_silence_ms=50,
            scan_step_ms=10,
            max_retained_silence_ms=100,
        )
    )

    segments = segmenter.segment(src, tmp_path / "segments")

    assert segments == [src]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_audio_segmenter.py -v
```

Expected: import failure for `audio_segmenter`.

- [ ] **Step 3: Implement `audio_segmenter.py`**

Create `Aiszr/audio_segmenter.py` with this structure:

```python
from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AudioSegmenterConfig:
    silence_threshold_db: float = -25.0
    min_segment_ms: int = 2000
    min_silence_ms: int = 50
    scan_step_ms: int = 10
    max_retained_silence_ms: int = 1000
    max_segments: int = 300


class AudioSegmenter:
    def __init__(self, config: AudioSegmenterConfig | None = None):
        self.config = config or AudioSegmenterConfig()

    def segment(self, wav_path: str | Path, output_dir: str | Path) -> list[Path]:
        src = Path(wav_path)
        out_dir = Path(output_dir)
        if not src.is_file():
            raise FileNotFoundError(f"WAV not found: {src}")
        audio = _read_wav(src)
        cut_points = self._find_cut_points(audio)
        if not cut_points:
            return [src]
        out_dir.mkdir(parents=True, exist_ok=True)
        segments = _write_segments(src, out_dir, audio, cut_points)
        return segments if segments else [src]

    def _find_cut_points(self, audio: "_WavAudio") -> list[int]:
        cfg = self.config
        frames_per_ms = audio.frame_rate / 1000.0
        min_segment_frames = int(cfg.min_segment_ms * frames_per_ms)
        min_silence_frames = int(cfg.min_silence_ms * frames_per_ms)
        max_retained_frames = int(cfg.max_retained_silence_ms * frames_per_ms)
        step_frames = max(1, int(cfg.scan_step_ms * frames_per_ms))
        cut_points: list[int] = []
        segment_start = 0
        silence_start: int | None = None

        for frame_index in range(0, audio.frame_count, step_frames):
            window = audio.samples[frame_index : frame_index + step_frames]
            silent = _dbfs(window) <= cfg.silence_threshold_db
            if silent and silence_start is None:
                silence_start = frame_index
            if not silent:
                silence_start = None
                continue
            if silence_start is None:
                continue
            silence_len = frame_index + len(window) - silence_start
            segment_len = frame_index - segment_start
            if silence_len >= min_silence_frames and segment_len >= min_segment_frames:
                cut_at = min(frame_index + max_retained_frames, audio.frame_count)
                cut_points.append(cut_at)
                if len(cut_points) >= cfg.max_segments:
                    break
                segment_start = cut_at
                silence_start = None

        return [cut for cut in cut_points if 0 < cut < audio.frame_count]
```

Also implement helper functions in the same file:

```python
@dataclass(slots=True)
class _WavAudio:
    params: wave._wave_params
    frame_rate: int
    frame_count: int
    sample_width: int
    channels: int
    raw_frames: bytes
    samples: list[int]


def _read_wav(path: Path) -> _WavAudio:
    with wave.open(str(path), "rb") as wav_file:
        params = wav_file.getparams()
        raw = wav_file.readframes(wav_file.getnframes())
    channels = params.nchannels
    sample_width = params.sampwidth
    if sample_width != 2:
        raise ValueError("Only PCM16 WAV is supported for segmentation")
    samples: list[int] = []
    stride = sample_width * channels
    for pos in range(0, len(raw), stride):
        channel_values = [
            int.from_bytes(raw[pos + ch * sample_width : pos + (ch + 1) * sample_width], "little", signed=True)
            for ch in range(channels)
        ]
        samples.append(int(sum(channel_values) / len(channel_values)))
    return _WavAudio(params, params.framerate, params.nframes, sample_width, channels, raw, samples)


def _dbfs(samples: list[int]) -> float:
    if not samples:
        return -120.0
    square_sum = sum(sample * sample for sample in samples)
    if square_sum <= 0:
        return -120.0
    rms = math.sqrt(square_sum / len(samples))
    return 20.0 * math.log10(max(rms, 1.0) / 32768.0)


def _write_segments(src: Path, out_dir: Path, audio: _WavAudio, cut_points: list[int]) -> list[Path]:
    points = [0] + cut_points + [audio.frame_count]
    segments: list[Path] = []
    bytes_per_frame = audio.sample_width * audio.channels
    for index, (start, end) in enumerate(zip(points, points[1:]), start=1):
        if end <= start:
            continue
        target = out_dir / f"segment_{index:04d}.wav"
        raw_start = start * bytes_per_frame
        raw_end = end * bytes_per_frame
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setparams(audio.params)
            wav_file.writeframes(audio.raw_frames[raw_start:raw_end])
        if target.stat().st_size > 44:
            segments.append(target)
    return segments if len(segments) > 1 else []
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_audio_segmenter.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/audio_segmenter.py Aiszr/tests/test_audio_segmenter.py
git commit -m "feat: add anchor audio segmentation"
```

---

### Task 2: Expose LiveTalking Single-Audio Upload

**Files:**
- Modify: `Aiszr/livetalking_runtime.py`
- Modify: `Aiszr/tests/test_livetalking_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Append to `Aiszr/tests/test_livetalking_runtime.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_runtime_start_can_skip_audio_loop(monkeypatch, tmp_path):
    runtime = LiveTalkingRuntime()
    config = LiveTalkingRuntimeConfig(
        livetalking_root=str(tmp_path),
        mediamtx_exe_path=str(tmp_path / "mediamtx.exe"),
        python_exe_path=str(tmp_path / "python.exe"),
        wav2lip384_weight_path=str(tmp_path / "wav2lip384.pth"),
        listen_port=8123,
    )
    sent = []

    monkeypatch.setattr(runtime, "_resolve_config", lambda cfg: config)
    monkeypatch.setattr(runtime, "_validate_config", lambda cfg: None)
    monkeypatch.setattr(runtime, "_ensure_avatar", lambda cfg: "avatar-1")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(runtime, "_start_mediamtx", _noop)
    monkeypatch.setattr(runtime, "_start_livetalking", _noop)
    monkeypatch.setattr(runtime, "_wait_until_ready", _noop)

    async def _send(port, wav_path, **kwargs):
        sent.append((port, str(wav_path)))

    monkeypatch.setattr(runtime, "send_audio_once", _send)

    result = await runtime.start(config, None, loop_audio=False)

    assert result["ok"] is True
    assert result["listen_port"] == 8123
    assert sent == []
    assert runtime._audio_loop_task is None
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_livetalking_runtime.py::test_runtime_start_can_skip_audio_loop -v
```

Expected: failure because `start()` does not accept `loop_audio`.

- [ ] **Step 3: Modify `LiveTalkingRuntime.start()` and expose `send_audio_once()`**

Change the signature and the audio startup block in `Aiszr/livetalking_runtime.py`:

```python
async def start(
    self,
    config: LiveTalkingRuntimeConfig,
    wav_path: str | None = None,
    *,
    loop_audio: bool = True,
) -> dict:
```

Inside `start()`, after `_wait_until_ready()`:

```python
normalized_wav: Path | None = None
if wav_path:
    normalized_wav = await self._normalize_wav(wav_path, resolved)
    await self.send_audio_once(resolved.listen_port, normalized_wav)
    if loop_audio:
        self._audio_loop_task = asyncio.create_task(
            self._loop_audio(resolved.listen_port, normalized_wav, wait_first=True)
        )
        self._audio_loop_task.add_done_callback(self._on_audio_loop_done)
```

Return:

```python
return {
    "ok": True,
    "rtmp_url": resolved.push_url,
    "audio_path": str(normalized_wav) if normalized_wav else "",
    "avatar_id": avatar_id,
    "listen_port": resolved.listen_port,
}
```

Rename the existing private upload method:

```python
async def send_audio_once(
    self,
    port: int,
    wav_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
```

Keep `_send_audio_once` as a backward-compatible wrapper if existing internals still call it:

```python
async def _send_audio_once(self, port: int, wav_path: Path, *, client: httpx.AsyncClient | None = None) -> None:
    await self.send_audio_once(port, wav_path, client=client)
```

Update `_loop_audio()` to call `send_audio_once()`.

- [ ] **Step 4: Run runtime tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_livetalking_runtime.py -v
```

Expected: all LiveTalking runtime tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/livetalking_runtime.py Aiszr/tests/test_livetalking_runtime.py
git commit -m "feat: expose livetalking audio upload"
```

---

### Task 3: Add Digital-Human Speech Scheduler

**Files:**
- Create: `Aiszr/digital_human_speech_scheduler.py`
- Create: `Aiszr/tests/test_digital_human_speech_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Create `Aiszr/tests/test_digital_human_speech_scheduler.py`:

```python
import asyncio
from pathlib import Path

import pytest

from digital_human_speech_scheduler import DigitalHumanSpeechScheduler


@pytest.mark.asyncio
async def test_scheduler_inserts_after_current_segment(tmp_path):
    normal_a = tmp_path / "a.wav"
    normal_b = tmp_path / "b.wav"
    insert = tmp_path / "insert.wav"
    for path in (normal_a, normal_b, insert):
        path.write_bytes(b"RIFFxxxxWAVE")

    sent = []

    async def _send(path: Path):
        sent.append(path.name)

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=[normal_a, normal_b],
        send_wav=_send,
        duration_fn=lambda path: 0.02,
    )
    await scheduler.start()
    await asyncio.sleep(0.005)
    assert sent == ["a.wav"]

    assert scheduler.enqueue_insertion(insert, text="keyword") is True
    await asyncio.sleep(0.06)
    await scheduler.stop()

    assert sent[:3] == ["a.wav", "insert.wav", "b.wav"]


@pytest.mark.asyncio
async def test_scheduler_rejects_insertions_when_queue_full(tmp_path):
    normal = tmp_path / "normal.wav"
    one = tmp_path / "one.wav"
    two = tmp_path / "two.wav"
    for path in (normal, one, two):
        path.write_bytes(b"RIFFxxxxWAVE")

    async def _send(path: Path):
        return None

    scheduler = DigitalHumanSpeechScheduler(
        anchor_segments=[normal],
        send_wav=_send,
        duration_fn=lambda path: 0.05,
        max_insertions=1,
    )

    assert scheduler.enqueue_insertion(one, text="one") is True
    assert scheduler.enqueue_insertion(two, text="two") is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_digital_human_speech_scheduler.py -v
```

Expected: import failure.

- [ ] **Step 3: Implement scheduler**

Create `Aiszr/digital_human_speech_scheduler.py`:

```python
from __future__ import annotations

import asyncio
import contextlib
import wave
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable


SendWav = Callable[[Path], Awaitable[None]]
DurationFn = Callable[[Path], float]


class DigitalHumanSpeechScheduler:
    def __init__(
        self,
        *,
        anchor_segments: list[str | Path],
        send_wav: SendWav,
        duration_fn: DurationFn | None = None,
        log_callback: Callable[[str], None] | None = None,
        max_insertions: int = 20,
    ):
        if not anchor_segments:
            raise ValueError("anchor_segments must not be empty")
        self._anchor_segments = [Path(path) for path in anchor_segments]
        self._send_wav = send_wav
        self._duration_fn = duration_fn or wav_duration_sec
        self._log = log_callback or (lambda message: None)
        self._max_insertions = max(1, int(max_insertions))
        self._insertions: deque[tuple[Path, str]] = deque()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._index = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def enqueue_insertion(self, wav_path: str | Path, *, text: str = "") -> bool:
        if len(self._insertions) >= self._max_insertions:
            self._log("keyword insertion queue full; dropping insertion")
            return False
        self._insertions.append((Path(wav_path), text))
        return True

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            path = self._next_path()
            try:
                await self._send_wav(path)
            except Exception as exc:
                self._log(f"speech scheduler upload failed: {exc}")
                if path not in self._anchor_segments:
                    continue
                raise
            duration = max(0.05, float(self._duration_fn(path) or 0.0))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=duration)

    def _next_path(self) -> Path:
        if self._insertions:
            path, _ = self._insertions.popleft()
            return path
        path = self._anchor_segments[self._index % len(self._anchor_segments)]
        self._index += 1
        return path


def wav_duration_sec(path: str | Path) -> float:
    with contextlib.suppress(Exception):
        with wave.open(str(path), "rb") as wav_file:
            rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            if rate:
                return frames / float(rate)
    return 0.0
```

- [ ] **Step 4: Run scheduler tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_digital_human_speech_scheduler.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/digital_human_speech_scheduler.py Aiszr/tests/test_digital_human_speech_scheduler.py
git commit -m "feat: add digital human speech scheduler"
```

---

### Task 4: Add Non-Playing Anchor Synthesis Helper

**Files:**
- Modify: `Aiszr/voice_manager.py`
- Modify: `Aiszr/tests/test_voice_manager_cache.py`

- [ ] **Step 1: Write failing voice-manager test**

Append to `Aiszr/tests/test_voice_manager_cache.py`:

```python
from voice_models import VoiceEntry, VoiceRoleConfig, VoiceSettings
from voice_manager import VoiceActionResult, VoiceManager


@pytest.mark.asyncio
async def test_synthesize_role_to_file_uses_anchor_voice_without_playing(monkeypatch, tmp_path):
    settings = VoiceSettings()
    settings.voices = [
        VoiceEntry(
            id="anchor-voice",
            name="anchor",
            clone_voice_id="clone-anchor",
            clone_status="ready",
        )
    ]
    settings.anchor = VoiceRoleConfig(voice_id="anchor-voice")
    manager = VoiceManager(settings)

    calls = []

    class FakeProvider:
        async def synthesize(self, text, voice_id, output_dir, *, model_id="", speed=1.2, volume=1.2):
            calls.append((text, voice_id, output_dir))
            out = tmp_path / "keyword.wav"
            out.write_bytes(b"RIFFxxxxWAVE")
            return VoiceActionResult(True, "ok", output_path=str(out))

    monkeypatch.setattr(manager, "provider", lambda: FakeProvider())

    result = await manager.synthesize_role_to_file("欢迎", "anchor")

    assert result.ok is True
    assert result.output_path.endswith("keyword.wav")
    assert calls[0][1] == "clone-anchor"
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_voice_manager_cache.py::test_synthesize_role_to_file_uses_anchor_voice_without_playing -v
```

Expected: failure because `synthesize_role_to_file()` is missing.

- [ ] **Step 3: Refactor voice synthesis**

Add this method to `VoiceManager`:

```python
async def synthesize_role_to_file(self, text: str, role_name: str) -> VoiceActionResult:
    if not text.strip():
        return VoiceActionResult(False, "合成文本为空")
    try:
        role = self._role(role_name)
    except KeyError:
        return VoiceActionResult(False, "语音角色无效")
    voice = self._voice(role.voice_id)
    if voice is None:
        return VoiceActionResult(False, "当前角色未选择声音")
    if not voice.clone_voice_id:
        return VoiceActionResult(False, "当前音色还没有可用的 voice_id，请先完成克隆")
    provider = self.provider()
    if voice.clone_status == "training":
        resolved = await provider.resolve_clone(voice.clone_voice_id)
        if resolved.ok and resolved.clone_status == "ready" and resolved.clone_voice_id:
            voice.clone_voice_id = resolved.clone_voice_id
            voice.clone_status = "ready"
            voice.last_error = ""
        else:
            return resolved
    result = await provider.synthesize(
        text=text,
        voice_id=voice.clone_voice_id,
        model_id=self.settings.model_id,
        output_dir=VOICE_DATA_DIR / role_name / "generated",
        speed=_role_speed_ratio(role),
        volume=_role_volume_ratio(role),
    )
    if result.ok:
        result.clone_voice_id = voice.clone_voice_id
        result.clone_status = voice.clone_status
    return result
```

Then simplify `synthesize_and_play()`:

```python
async def synthesize_and_play(self, text: str, role_name: str) -> VoiceActionResult:
    result = await self.synthesize_role_to_file(text, role_name)
    if not result.ok or not result.output_path:
        return result
    if is_audio_stopped():
        return result
    try:
        await play_wav_file(result.output_path)
    except Exception as exc:
        return VoiceActionResult(False, f"播放失败：{exc}", output_path=result.output_path)
    if result.message != "使用缓存试听音频":
        result.message = "试听已播放"
    return result
```

- [ ] **Step 4: Run voice-manager tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_voice_manager_cache.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/voice_manager.py Aiszr/tests/test_voice_manager_cache.py
git commit -m "feat: synthesize anchor voice without playback"
```

---

### Task 5: Integrate Scheduler Into Digital-Human Pipeline

**Files:**
- Modify: `Aiszr/digital_human_pipeline.py`
- Create: `Aiszr/tests/test_digital_human_pipeline_audio.py`

- [ ] **Step 1: Write failing pipeline helper tests**

Create `Aiszr/tests/test_digital_human_pipeline_audio.py`:

```python
from pathlib import Path

import pytest

from digital_human_pipeline import DigitalHumanPipeline, PipelineConfig
from voice_manager import VoiceActionResult


class FakeVoiceManager:
    settings = None


@pytest.mark.asyncio
async def test_pipeline_segments_anchor_audio_with_fallback(monkeypatch, tmp_path):
    full_wav = tmp_path / "anchor.wav"
    full_wav.write_bytes(b"RIFFxxxxWAVE")
    segment = tmp_path / "segment_0001.wav"
    segment.write_bytes(b"RIFFxxxxWAVE")
    pipeline = DigitalHumanPipeline(FakeVoiceManager())

    async def _fake_synthesize(config):
        return VoiceActionResult(True, "ok", output_path=str(full_wav))

    class FakeSegmenter:
        def segment(self, wav_path, output_dir):
            assert Path(wav_path) == full_wav
            return [segment]

    monkeypatch.setattr(pipeline, "_synthesize_audio", _fake_synthesize)
    monkeypatch.setattr("digital_human_pipeline.AudioSegmenter", lambda: FakeSegmenter())

    result = await pipeline._prepare_anchor_segments(PipelineConfig(output_dir=str(tmp_path)))

    assert result.ok is True
    assert result.output_path == str(full_wav)
    assert result.segments == [segment]
```

- [ ] **Step 2: Run new test and verify it fails**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_digital_human_pipeline_audio.py -v
```

Expected: failure because `_prepare_anchor_segments()` is missing.

- [ ] **Step 3: Add segment preparation result and helper**

In `Aiszr/digital_human_pipeline.py`, import:

```python
from audio_segmenter import AudioSegmenter
from digital_human_speech_scheduler import DigitalHumanSpeechScheduler
from voice_manager import VoiceActionResult
```

Add a dataclass near `PipelineConfig`:

```python
@dataclass(slots=True)
class AnchorAudioResult:
    ok: bool
    message: str
    output_path: str = ""
    segments: list[Path] = None
```

Use `field(default_factory=list)` if needed:

```python
from dataclasses import dataclass, field
```

Implement:

```python
async def _prepare_anchor_segments(self, config: PipelineConfig) -> AnchorAudioResult:
    audio_result = await self._resolve_livetalking_audio(config)
    if not audio_result.ok:
        return AnchorAudioResult(False, audio_result.message, audio_result.output_path, [])
    source = Path(audio_result.output_path)
    if not source.is_file():
        return AnchorAudioResult(False, f"音频文件不存在: {audio_result.output_path!r}", audio_result.output_path, [])
    try:
        segment_dir = Path(config.resolve_output_dir()) / "anchor_segments"
        segments = AudioSegmenter().segment(source, segment_dir)
    except Exception as exc:
        self._log(f"主播音频切片失败，回退整段 WAV: {exc}")
        segments = [source]
    return AnchorAudioResult(True, audio_result.message, str(source), segments)
```

- [ ] **Step 4: Update `_run_livetalking()` to start scheduler**

Replace the direct single-WAV runtime start path:

```python
anchor_audio = await self._prepare_anchor_segments(config)
if not anchor_audio.ok:
    self._set_state(PipelineState.ERROR)
    return {"ok": False, "message": f"LiveTalking 音频准备失败: {anchor_audio.message}"}
```

Start LiveTalking without owning the loop:

```python
runtime_result = await self._livetalking_runtime.start(runtime_config, None, loop_audio=False)
listen_port = int(runtime_result.get("listen_port") or config.livetalking_listen_port)
```

Create scheduler:

```python
self._speech_scheduler = DigitalHumanSpeechScheduler(
    anchor_segments=anchor_audio.segments,
    send_wav=lambda path: self._livetalking_runtime.send_audio_once(listen_port, Path(path)),
    log_callback=self._log,
)
await self._speech_scheduler.start()
```

Initialize `self._speech_scheduler = None` in `__init__()`. In `stop()`, stop it before stopping LiveTalking:

```python
if self._speech_scheduler:
    with contextlib.suppress(Exception):
        await self._speech_scheduler.stop()
    self._speech_scheduler = None
```

Add insertion method:

```python
def enqueue_insertion_audio(self, wav_path: str | Path, *, text: str = "") -> bool:
    if self._speech_scheduler is None:
        return False
    return self._speech_scheduler.enqueue_insertion(wav_path, text=text)
```

- [ ] **Step 5: Run pipeline audio tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_digital_human_pipeline_audio.py tests/test_livetalking_runtime.py -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add Aiszr/digital_human_pipeline.py Aiszr/tests/test_digital_human_pipeline_audio.py
git commit -m "feat: schedule digital human speech audio"
```

---

### Task 6: Wire Keyword Voice Insertions

**Files:**
- Modify: `Aiszr/ui.py`
- Create: `Aiszr/tests/test_keyword_voice_insertion.py`

- [ ] **Step 1: Write failing worker helper tests**

Create `Aiszr/tests/test_keyword_voice_insertion.py`:

```python
import pytest

from ui import CaptureWorker
from voice_manager import VoiceActionResult


@pytest.mark.asyncio
async def test_keyword_voice_insertion_uses_anchor_voice(monkeypatch, tmp_path):
    worker = CaptureWorker()
    wav = tmp_path / "insert.wav"
    wav.write_bytes(b"RIFFxxxxWAVE")
    calls = []

    class FakeVoiceManager:
        async def synthesize_role_to_file(self, text, role_name):
            calls.append((text, role_name))
            return VoiceActionResult(True, "ok", output_path=str(wav))

    class FakePipeline:
        def enqueue_insertion_audio(self, wav_path, *, text=""):
            calls.append((str(wav_path), text))
            return True

    worker._voice_manager = FakeVoiceManager()
    worker._digital_human_pipeline = FakePipeline()

    ok = await worker._enqueue_keyword_voice_insertion("欢迎进直播间")

    assert ok is True
    assert calls[0] == ("欢迎进直播间", "anchor")
    assert calls[1] == (str(wav), "欢迎进直播间")
```

- [ ] **Step 2: Run new test and verify it fails**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_keyword_voice_insertion.py -v
```

Expected: failure because `_enqueue_keyword_voice_insertion()` is missing.

- [ ] **Step 3: Add worker helper and update dispatch**

In `CaptureWorker`, add:

```python
async def _enqueue_keyword_voice_insertion(self, reply: str) -> bool:
    text = str(reply or "").strip()
    if not text:
        return False
    pipeline = self._digital_human_pipeline
    if pipeline is None:
        logger.debug("Keyword voice insertion skipped: digital human pipeline not running")
        return False
    result = await self._voice_manager.synthesize_role_to_file(text, "anchor")
    if not result.ok or not result.output_path:
        logger.warning("Keyword voice synthesis failed: {}", result.message)
        return False
    ok = pipeline.enqueue_insertion_audio(result.output_path, text=text)
    if not ok:
        logger.warning("Keyword voice insertion queue rejected reply")
    return ok
```

Change `_dispatch_keyword_reply()` signature:

```python
async def _dispatch_keyword_reply(
    self,
    keyword: str,
    reply: str,
    nickname: str,
    count: int,
    generate_voice: bool = False,
):
```

Before emitting `keyword_reply_fired`, add:

```python
if generate_voice:
    await self._enqueue_keyword_voice_insertion(reply)
```

In `_on_message()`, pass the rule flag:

```python
self._dispatch_keyword_reply(rule.keyword, rule.reply, nick, count, rule.generate_voice)
```

Do not change `keyword_reply_fired` signal args in this task. Text injection UI behavior remains unchanged.

- [ ] **Step 4: Run keyword and scheduler tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_keyword_voice_insertion.py tests/test_digital_human_speech_scheduler.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/ui.py Aiszr/tests/test_keyword_voice_insertion.py
git commit -m "feat: enqueue keyword voice insertions"
```

---

### Task 7: Add Local Voice Provider Slot

**Files:**
- Modify: `Aiszr/voice_models.py`
- Modify: `Aiszr/ui_constants.py`
- Modify: `Aiszr/voice_manager.py`
- Create: `Aiszr/tests/test_voice_provider_settings.py`

- [ ] **Step 1: Write failing provider settings tests**

Create `Aiszr/tests/test_voice_provider_settings.py`:

```python
from voice_manager import LocalVoiceProvider
from voice_models import VOICE_MODELS, VOICE_PROVIDER_LABELS, VOICE_PROVIDERS, VoiceProviderApiConfig, VoiceSettings


def test_local_voice_provider_is_registered():
    assert "local_voice" in VOICE_PROVIDERS
    assert VOICE_PROVIDER_LABELS["local_voice"] == "本地语音模型"
    assert VOICE_MODELS["local_voice"] == ("local-voice-http",)


def test_voice_settings_accepts_local_provider():
    settings = VoiceSettings.from_dict(
        {
            "provider": "local_voice",
            "model_id": "local-voice-http",
            "api": {"local_voice": {"endpoint": "http://127.0.0.1:9880"}},
        }
    )

    assert settings.provider == "local_voice"
    assert settings.model_id == "local-voice-http"
    assert settings.api["local_voice"].endpoint == "http://127.0.0.1:9880"


def test_local_voice_provider_validation_requires_endpoint():
    result = LocalVoiceProvider(VoiceProviderApiConfig()).missing_credentials()

    assert result == ["endpoint"]
```

- [ ] **Step 2: Run new test and verify it fails**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_voice_provider_settings.py -v
```

Expected: import or registration failure.

- [ ] **Step 3: Register local provider slot**

In `Aiszr/voice_models.py`:

```python
VOICE_PROVIDERS = ("aliyun_bailian", "local_voice")
VOICE_PROVIDER_LABELS = {
    "aliyun_bailian": "阿里云百炼",
    "local_voice": "本地语音模型",
}
VOICE_MODELS = {
    "aliyun_bailian": ("qwen3-tts-vc-2026-01-22",),
    "local_voice": ("local-voice-http",),
}
```

In `Aiszr/ui_constants.py`, add:

```python
"local_voice": {
    "hint": "本地语音模型暂作为预留接口；具体模型需完成开源免费模型调研后接入。",
    "fields": {
        "endpoint": ("Endpoint", "例如 http://127.0.0.1:9880"),
    },
},
```

In `Aiszr/voice_manager.py`, add:

```python
class LocalVoiceProvider(VoiceProviderBase):
    provider_name = "local_voice"
    provider_label = "本地语音模型"

    def required_fields(self) -> set[str]:
        return {"endpoint"}

    async def create_clone(
        self,
        wav_path: str,
        voice_id: str | None = None,
        *,
        model_id: str = "",
        requested_voice_id: str = "001",
    ) -> VoiceActionResult:
        return VoiceActionResult(
            False,
            "本地语音模型尚未接入具体开源模型；请先完成模型调研和接口适配。",
            clone_status="error",
        )

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_dir: Path,
        *,
        model_id: str = "",
        speed: float = DEFAULT_SPEED_RATIO,
        volume: float = DEFAULT_VOLUME_RATIO,
    ) -> VoiceActionResult:
        return VoiceActionResult(
            False,
            "本地语音模型尚未接入具体开源模型；请先完成模型调研和接口适配。",
        )
```

Register it:

```python
PROVIDER_TYPES = {
    "aliyun_bailian": AliyunBailianProvider,
    "local_voice": LocalVoiceProvider,
}
```

- [ ] **Step 4: Run provider tests**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_voice_provider_settings.py tests/test_voice_manager_cache.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add Aiszr/voice_models.py Aiszr/ui_constants.py Aiszr/voice_manager.py Aiszr/tests/test_voice_provider_settings.py
git commit -m "feat: add local voice provider slot"
```

---

### Task 8: Run Focused Integration Verification

**Files:**
- No new files unless a failure requires a targeted fix.

- [ ] **Step 1: Run focused test group**

Run:

```powershell
cd Aiszr
python -m pytest tests/test_audio_segmenter.py tests/test_digital_human_speech_scheduler.py tests/test_digital_human_pipeline_audio.py tests/test_keyword_voice_insertion.py tests/test_livetalking_runtime.py tests/test_voice_manager_cache.py tests/test_voice_provider_settings.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full Aiszr test suite**

Run:

```powershell
cd Aiszr
python -m pytest -q
```

Expected: all tests pass. If unrelated pre-existing failures appear, record the exact failing test names and confirm they are unrelated before proceeding.

- [ ] **Step 3: Manual smoke check without LiveTalking external services**

Run:

```powershell
cd Aiszr
python -m py_compile audio_segmenter.py digital_human_speech_scheduler.py livetalking_runtime.py digital_human_pipeline.py voice_manager.py ui.py
```

Expected: command exits successfully.

- [ ] **Step 4: Commit any final targeted fixes**

Only if Step 1-3 required small fixes:

```powershell
git add <changed-files>
git commit -m "fix: stabilize keyword voice insertion"
```

---

## Self-Review Checklist

Spec coverage:

- Anchor full WAV synthesis and hidden silence segmentation: Task 1 and Task 5.
- Current-segment-finish insertion behavior: Task 3.
- LiveTalking lip-sync preserved: Task 2 and Task 5 keep `/humanaudio` upload behavior.
- Anchor voice keyword synthesis: Task 4 and Task 6.
- Platform-agnostic speech insertion with current WeChat text injection unchanged: Task 6.
- Local provider research gate and provider slot: Task 7.
- Fallback and queue protection: Task 1, Task 3, Task 5, Task 6.

No unfinished implementation details are intentionally left. The concrete local voice model remains out of scope by design and is represented by a failing-but-registered provider slot with clear user-facing messages.
