"""Pipeline orchestrator for digital human HLS streaming to OBS."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import Enum, auto
from http.server import HTTPServer, SimpleHTTPRequestHandler

import httpx
from pathlib import Path
from threading import Thread
from typing import Callable

from loguru import logger

from audio_segmenter import AudioSegmenter
from digital_human_speech_scheduler import DigitalHumanSpeechScheduler
from ffmpeg_ops import check_ffmpeg_available
from obs_actions import ObsDigitalHumanConfigurator, ObsWebSocketClient
from voice_manager import VoiceActionResult


class PipelineState(Enum):
    IDLE = auto()
    SYNTHESIZING = auto()
    LIVETALKING_PREPARING = auto()
    LIVETALKING_STARTING = auto()
    STARTING_SERVER = auto()
    CONFIGURING_OBS = auto()
    PUSHING = auto()
    STREAMING = auto()
    STOPPING = auto()
    ERROR = auto()
    CANCELLED = auto()


@dataclass(slots=True)
class PipelineConfig:
    video_path: str = ""
    output_dir: str = ""

    def resolve_output_dir(self) -> str:
        if self.output_dir:
            return self.output_dir
        from app_paths import app_dir
        return str(app_dir() / "data" / "digital_human")
    rtmp_host: str = "127.0.0.1"
    rtmp_port: int = 1935
    obs_scene: str = ""
    obs_input_name: str = "AiszrDigitalHuman"
    obs_host: str = "127.0.0.1"
    obs_port: int = 4455
    obs_password: str = ""
    use_livetalking: bool = True
    audio_folder_path: str = ""
    livetalking_root: str = ""
    mediamtx_exe_path: str = ""
    livetalking_python_exe_path: str = ""
    wav2lip384_weight_path: str = ""
    livetalking_avatar_id: str = ""
    livetalking_listen_port: int = 8010
    livetalking_push_url: str = "rtmp://127.0.0.1:1935/live/aiszr"
    livetalking_batch_size: int = 4
    livetalking_modelres: int = 384


@dataclass(slots=True)
class AnchorAudioResult:
    ok: bool
    message: str = ""
    source_path: Path | None = None
    segments: list[Path] = field(default_factory=list)


class DigitalHumanPipeline:
    """Orchestrates LiveTalking wav2lip RTMP output and OBS config."""

    def __init__(
        self,
        voice_manager,
        log_callback: Callable[[str], None] | None = None,
    ):
        self._voice_manager = voice_manager
        self._log = log_callback or (lambda msg: logger.info("DH: {}", msg))
        self._state = PipelineState.IDLE
        self._obs_configurator: ObsDigitalHumanConfigurator | None = None
        self._obs_client: ObsWebSocketClient | None = None
        self._ffmpeg_proc: asyncio.subprocess.Process | None = None
        self._hls_dir: str | None = None
        self._http_server: HTTPServer | None = None
        self._http_thread: Thread | None = None
        self._livetalking_runtime = None
        self._speech_scheduler = None
        self._scheduler_client: httpx.AsyncClient | None = None
        self._cancel_event = asyncio.Event()

    @property
    def state(self) -> PipelineState:
        return self._state

    def _set_state(self, state: PipelineState):
        self._state = state
        self._log(f"状态: {state.name}")

    # Fixed HLS port so the URL `http://127.0.0.1:8780/stream.m3u8` stays
    # stable across restarts. This lets OBS pre-configure a Media Source
    # pointing at this URL once and have it auto-pick-up new pushes without
    # any WebSocket / reconfiguration round-trips. If the port is busy (e.g.
    # the previous Aiszr process didn't release it), fall back to random.
    HLS_PORT = 8780

    def _start_http_server(self, directory: str) -> int:
        handler = _make_hls_handler(directory)
        try:
            self._http_server = HTTPServer(("127.0.0.1", self.HLS_PORT), handler)
        except OSError:
            # Port busy — fall back to random (legacy behavior).
            self._http_server = HTTPServer(("127.0.0.1", 0), handler)
        port = self._http_server.server_address[1]
        self._http_thread = Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        return port

    def _stop_http_server(self):
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
        self._http_thread = None

    async def run(self, config: PipelineConfig) -> dict:
        self._cancel_event.clear()

        try:
            # Step 1: Pre-flight
            self._set_state(PipelineState.SYNTHESIZING)
            await check_ffmpeg_available()
            if self._cancel_event.is_set():
                return self._cancel_result()

            return await self._run_livetalking(config)

        except asyncio.CancelledError:
            await self._stop_livetalking_speech_stack()
            return self._cancel_result()
        except Exception as exc:
            import traceback
            self._set_state(PipelineState.ERROR)
            self._log(f"Pipeline 错误: {exc}\n{traceback.format_exc()}")
            return {"ok": False, "message": f"Pipeline 错误: {exc}"}

    async def stop(self) -> None:
        self._cancel_event.set()
        self._set_state(PipelineState.STOPPING)

        if self._ffmpeg_proc and self._ffmpeg_proc.returncode is None:
            with contextlib.suppress(Exception):
                self._ffmpeg_proc.terminate()
                await asyncio.wait_for(self._ffmpeg_proc.wait(), timeout=5)
            self._ffmpeg_proc = None

        await self._stop_livetalking_speech_stack()

        self._stop_http_server()

        if self._obs_configurator:
            with contextlib.suppress(Exception):
                await self._obs_configurator.teardown()
            self._obs_configurator = None

        if self._obs_client:
            with contextlib.suppress(Exception):
                await self._obs_client.close()
            self._obs_client = None

        if self._hls_dir:
            with contextlib.suppress(Exception):
                for f in Path(self._hls_dir).glob("*"):
                    f.unlink(missing_ok=True)
            self._hls_dir = None

        self._set_state(PipelineState.IDLE)

    async def _run_livetalking(self, config: PipelineConfig) -> dict:
        if not config.video_path or not Path(config.video_path).is_file():
            self._set_state(PipelineState.ERROR)
            return {"ok": False, "message": f"主播视频不存在: {config.video_path!r}"}

        self._set_state(PipelineState.LIVETALKING_PREPARING)
        anchor_audio = await self._prepare_anchor_segments(config)
        if not anchor_audio.ok:
            self._set_state(PipelineState.ERROR)
            self._log(f"LiveTalking 音频准备失败: {anchor_audio.message}")
            return {"ok": False, "message": f"LiveTalking 音频准备失败: {anchor_audio.message}"}
        if self._cancel_event.is_set():
            return self._cancel_result()

        from livetalking_runtime import LiveTalkingRuntime, LiveTalkingRuntimeConfig

        runtime_config = LiveTalkingRuntimeConfig(
            livetalking_root=config.livetalking_root,
            mediamtx_exe_path=config.mediamtx_exe_path,
            python_exe_path=config.livetalking_python_exe_path,
            wav2lip384_weight_path=config.wav2lip384_weight_path,
            avatar_id=config.livetalking_avatar_id,
            listen_port=config.livetalking_listen_port,
            push_url=config.livetalking_push_url,
            batch_size=config.livetalking_batch_size,
            modelres=config.livetalking_modelres,
            avatar_video_path=config.video_path,
            output_dir=config.resolve_output_dir(),
        )

        self._set_state(PipelineState.LIVETALKING_STARTING)
        self._livetalking_runtime = LiveTalkingRuntime(
            log_callback=self._log,
            error_callback=self._on_livetalking_runtime_error,
        )
        try:
            runtime_result = await self._livetalking_runtime.start(
                runtime_config,
                None,
                loop_audio=False,
            )
        except asyncio.CancelledError:
            await self._stop_livetalking_speech_stack()
            raise
        except Exception:
            with contextlib.suppress(Exception):
                await self._livetalking_runtime.stop()
            self._livetalking_runtime = None
            raise
        if self._cancel_event.is_set():
            await self._stop_livetalking_speech_stack()
            return self._cancel_result()

        listen_port = int(runtime_result.get("listen_port") or config.livetalking_listen_port)

        async def send_scheduler_wav(path) -> None:
            runtime = self._livetalking_runtime
            if runtime is None:
                raise RuntimeError("LiveTalking runtime is not running")
            normalized_wav = await runtime._normalize_wav(str(path), runtime_config)
            await runtime.send_audio_once(listen_port, normalized_wav, client=scheduler_client)

        scheduler_client = httpx.AsyncClient(timeout=30.0)
        self._scheduler_client = scheduler_client

        try:
            first_segment = Path(anchor_audio.segments[0])
            scheduler_segments = [Path(path) for path in anchor_audio.segments]
            if len(scheduler_segments) > 1:
                scheduler_segments = scheduler_segments[1:] + scheduler_segments[:1]

            await send_scheduler_wav(first_segment)

            self._speech_scheduler = DigitalHumanSpeechScheduler(
                anchor_segments=scheduler_segments,
                send_wav=send_scheduler_wav,
                log_callback=self._log,
                initial_delay_path=first_segment,
            )
            scheduler_task = self._speech_scheduler.start()
            scheduler_task.add_done_callback(self._on_speech_scheduler_done)

            self._set_state(PipelineState.CONFIGURING_OBS)
            stream_url = runtime_result.get("rtmp_url", config.livetalking_push_url)
            obs_result = await self._configure_obs(config, stream_url)
        except asyncio.CancelledError:
            await self._stop_livetalking_speech_stack()
            raise
        except Exception:
            await self._stop_livetalking_speech_stack()
            raise

        self._set_state(PipelineState.STREAMING)
        result = {
            "ok": True,
            "message": "LiveTalking RTMP 推流已开始",
            "state": "streaming",
            "stream_url": stream_url,
        }
        if not obs_result.get("ok"):
            result["obs_warning"] = (
                f"OBS 自动配置失败：{obs_result.get('message', '未知错误')}。"
                f"请手动在 OBS 添加 Media Source，URL：{stream_url}"
            )
        return result

    def _on_livetalking_runtime_error(self, message: str) -> None:
        if self._cancel_event.is_set():
            return
        self._log(message)
        self._set_state(PipelineState.ERROR)

    def _on_speech_scheduler_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or self._cancel_event.is_set():
            return
        exc = task.exception()
        if exc is None:
            return
        self._log(f"Digital human speech scheduler failed: {exc}")
        self._set_state(PipelineState.ERROR)
        asyncio.create_task(self._stop_livetalking_speech_stack())

    async def _stop_livetalking_speech_stack(self) -> None:
        if self._speech_scheduler:
            with contextlib.suppress(Exception):
                await self._speech_scheduler.stop()
            self._speech_scheduler = None

        if self._scheduler_client:
            with contextlib.suppress(Exception):
                await self._scheduler_client.aclose()
            self._scheduler_client = None

        if self._livetalking_runtime:
            with contextlib.suppress(Exception):
                await self._livetalking_runtime.stop()
            self._livetalking_runtime = None

    async def _prepare_anchor_segments(self, config: PipelineConfig) -> AnchorAudioResult:
        audio_result = await self._resolve_livetalking_audio(config)
        if not audio_result.ok:
            return AnchorAudioResult(False, audio_result.message)
        if not audio_result.output_path:
            return AnchorAudioResult(False, "音频文件不存在: ''")

        source = Path(audio_result.output_path)
        if not source.is_file():
            return AnchorAudioResult(False, f"音频文件不存在: {audio_result.output_path!r}")

        segment_dir = Path(config.resolve_output_dir()) / "anchor_segments"
        try:
            segments = AudioSegmenter().segment(source, segment_dir)
        except Exception as exc:
            self._log(f"anchor audio segment failed; fallback to full wav: {exc}")
            segments = [source]

        normalized_segments = [Path(segment) for segment in segments]
        if not normalized_segments:
            normalized_segments = [source]

        return AnchorAudioResult(
            True,
            audio_result.message,
            source_path=source,
            segments=normalized_segments,
        )

    async def _resolve_livetalking_audio(self, config: PipelineConfig):
        from livetalking_runtime import find_single_wav

        folder_wav = find_single_wav(config.audio_folder_path)
        if folder_wav is not None:
            self._log(f"使用 WAV 文件夹音频: {folder_wav.name}")
            return VoiceActionResult(True, "使用 WAV 文件夹音频", output_path=str(folder_wav))
        if config.audio_folder_path:
            self._log("WAV 文件夹没有音频，回退到 TTS")
        return await self._synthesize_audio(config)

    async def _synthesize_audio(self, config: PipelineConfig):
        from voice_manager import (
            DEFAULT_SPEED_RATIO,
            DEFAULT_VOLUME_RATIO,
            VOICE_DATA_DIR,
            synthesis_voice_id_for_role,
        )

        settings = self._voice_manager.settings
        anchor = settings.anchor
        text = settings.anchor_script.strip()
        if not text:
            return VoiceActionResult(False, "主播话术为空，请先在 AI 语音设置中填写主播话术")

        synth_voice_id = synthesis_voice_id_for_role(settings, anchor)
        if synth_voice_id is None:
            return VoiceActionResult(False, "主播音色未配置，请先在 AI 语音设置中完成克隆")

        output_dir = VOICE_DATA_DIR / "anchor" / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use the fixed audio file generated by the Anchor Settings dialog
        anchor_wav = output_dir / "anchor.wav"
        if settings.provider != "local_voice" and anchor_wav.is_file():
            self._log(f"使用已生成音频: anchor.wav")
            return VoiceActionResult(True, "使用已生成音频", output_path=str(anchor_wav))

        # Fallback: generate via TTS provider
        provider = self._voice_manager.provider()
        speed = DEFAULT_SPEED_RATIO
        volume = DEFAULT_VOLUME_RATIO * (anchor.volume_gain / 100.0)
        result = await provider.synthesize(
            text=text,
            voice_id=synth_voice_id,
            model_id=settings.model_id,
            output_dir=output_dir,
            speed=speed,
            volume=max(0.0, min(2.0, volume)),
        )
        if result.ok:
            self._log(f"音频就绪: {Path(result.output_path).name}")
        return result

    async def _configure_obs(self, config: PipelineConfig, stream_path: str) -> dict:
        """Best-effort OBS Media Source config. Returns {ok, message} so the
        caller (`run`) can surface a warning if config failed — but
        regardless of outcome, ffmpeg keeps pushing HLS, so the caller
        will still report STREAMING state."""
        try:
            self._obs_client = ObsWebSocketClient()
            await self._obs_client.connect(
                config.obs_host, config.obs_port, config.obs_password,
            )
            scene_name = config.obs_scene
            if not scene_name:
                resp = await self._obs_client.request("GetCurrentProgramScene")
                scene_name = resp.get("currentProgramSceneName", "")
                if not scene_name:
                    self._log("无法获取 OBS 当前场景")
                    return {"ok": False, "message": "无法获取 OBS 当前场景"}
                self._log(f"使用 OBS 当前场景: {scene_name}")
            self._obs_configurator = ObsDigitalHumanConfigurator(
                self._obs_client, self._log,
            )
            result = await self._obs_configurator.setup(
                scene_name=scene_name,
                input_name=config.obs_input_name,
                stream_path=stream_path,
            )
            if not result.get("ok"):
                msg = result.get("message", "未知错误")
                self._log(f"OBS 自动配置失败（不影响推流）: {msg}")
                return {"ok": False, "message": msg}
            return {"ok": True}
        except Exception as exc:
            self._log(f"OBS 自动配置失败（不影响推流）: {exc}")
            return {"ok": False, "message": str(exc)}

    def _cancel_result(self) -> dict:
        self._set_state(PipelineState.CANCELLED)
        return {"ok": False, "message": "推流已取消"}

    def enqueue_insertion_audio(self, wav_path, *, text: str = "") -> bool:
        if self._speech_scheduler is None:
            return False
        return self._speech_scheduler.enqueue_insertion(wav_path, text=text)


def _make_hls_handler(directory: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format, *args):
            pass
    return Handler
