"""Pipeline orchestrator for digital human HLS streaming to OBS."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from dataclasses import dataclass
from enum import Enum, auto
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Callable

from loguru import logger

from ffmpeg_ops import (
    check_ffmpeg_available,
    start_hls_push,
)
from obs_actions import ObsDigitalHumanConfigurator, ObsWebSocketClient


class PipelineState(Enum):
    IDLE = auto()
    SYNTHESIZING = auto()
    HEYGEM_SYNTHESIZING = auto()
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
    # HeyGem 数字人合成 — 开关 + anchor 视频路径
    # use_heygem=True 时跳过绿幕循环路径，先把 TTS WAV submit 给 HeyGem
    # 合成出音视频对口型的 mp4，然后 HLS 循环推流
    use_heygem: bool = False
    heygem_avatar_video_path: str = ""
    heygem_timeout_sec: float = 600.0


class DigitalHumanPipeline:
    """Orchestrates: TTS -> HLS stream -> HTTP serve -> OBS config."""

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

            # Step 2: TTS synthesis
            audio_result = await self._synthesize_audio(config)
            if not audio_result.ok:
                self._set_state(PipelineState.ERROR)
                self._log(f"TTS 合成失败: {audio_result.message}")
                return {"ok": False, "message": f"TTS 合成失败: {audio_result.message}"}
            if self._cancel_event.is_set():
                return self._cancel_result()

            # Step 3: Validate inputs
            if not audio_result.output_path or not Path(audio_result.output_path).is_file():
                self._set_state(PipelineState.ERROR)
                self._log(f"音频文件不存在: {audio_result.output_path!r}")
                return {"ok": False, "message": f"音频文件不存在: {audio_result.output_path!r}"}

            # Step 3.5: HeyGem 数字人合成（可选）
            # use_heygem=True 时把 TTS WAV + anchor mp4 提交给 HeyGem，
            # 拿到对口型的音视频 mp4，覆盖 video_path，置空 audio_path，
            # 后续 HLS push 走单输入循环路径
            hls_audio_path: str | None = audio_result.output_path
            hls_video_path = config.video_path
            if config.use_heygem:
                heygem_result = await self._heygem_synthesize(config, audio_result.output_path)
                if not heygem_result.get("ok"):
                    self._set_state(PipelineState.ERROR)
                    self._log(f"HeyGem 失败: {heygem_result.get('message', '未知错误')}")
                    return heygem_result
                hls_video_path = heygem_result["mp4_path"]
                hls_audio_path = None  # mp4 自带音频
                if self._cancel_event.is_set():
                    return self._cancel_result()

            if not hls_video_path or not Path(hls_video_path).is_file():
                self._set_state(PipelineState.ERROR)
                self._log(f"视频文件不存在: {hls_video_path!r}")
                return {"ok": False, "message": f"视频文件不存在: {hls_video_path!r}"}

            # Step 4: Prepare HLS output directory + HTTP server
            self._set_state(PipelineState.STARTING_SERVER)
            hls_dir = Path(config.resolve_output_dir()).resolve() / "hls"
            hls_dir.mkdir(parents=True, exist_ok=True)
            for f in hls_dir.glob("*"):
                f.unlink(missing_ok=True)
            self._hls_dir = str(hls_dir)

            http_port = self._start_http_server(str(hls_dir))
            m3u8_path = str(hls_dir / "stream.m3u8")
            m3u8_url = f"http://127.0.0.1:{http_port}/stream.m3u8"
            self._log(f"HLS 服务: {m3u8_url}")

            # ── ffmpeg HLS 推流 ──
            # use_heygem=False: video=绿幕循环 + audio=TTS WAV
            # use_heygem=True : video=HeyGem mp4（含音频），audio_path=None
            self._set_state(PipelineState.PUSHING)
            self._ffmpeg_proc = await start_hls_push(
                video_path=hls_video_path,
                audio_path=hls_audio_path,
                hls_dir=str(hls_dir),
            )
            # Wait for m3u8 playlist to appear (timeout 15s)
            for _ in range(150):
                if self._cancel_event.is_set():
                    return self._cancel_result()
                if self._ffmpeg_proc.returncode is not None:
                    stderr = await self._ffmpeg_proc.stderr.read()
                    self._set_state(PipelineState.ERROR)
                    self._log(f"ffmpeg 提前退出: {stderr.decode(errors='replace')[:500]}")
                    return {"ok": False, "message": "推流失败: ffmpeg 提前退出"}
                if Path(m3u8_path).exists():
                    break
                await asyncio.sleep(0.1)
            else:
                self._set_state(PipelineState.ERROR)
                return {"ok": False, "message": "推流超时：HLS 文件未生成"}

            # Step 6: OBS auto-config AFTER stream is live. OBS misconfig
            # is non-fatal — surface via `obs_warning` so the UI can show
            # a warning Toast.
            self._set_state(PipelineState.CONFIGURING_OBS)
            obs_result = await self._configure_obs(config, m3u8_url)

            self._set_state(PipelineState.STREAMING)
            result = {
                "ok": True,
                "message": "推流已开始",
                "state": "streaming",
            }
            if not obs_result.get("ok"):
                result["obs_warning"] = (
                    f"OBS 自动配置失败：{obs_result.get('message', '未知错误')}。"
                    f"请手动在 OBS 添加 Media Source，URL：{m3u8_url}"
                )
            return result

        except asyncio.CancelledError:
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

    async def _synthesize_audio(self, config: PipelineConfig):
        from voice_manager import DEFAULT_SPEED_RATIO, DEFAULT_VOLUME_RATIO, VOICE_DATA_DIR, VoiceActionResult

        settings = self._voice_manager.settings
        anchor = settings.anchor
        text = settings.anchor_script.strip()
        if not text:
            return VoiceActionResult(False, "主播话术为空，请先在 AI 语音设置中填写主播话术")

        voice_entry = settings.find_voice(anchor.voice_id)
        if not voice_entry or not voice_entry.clone_voice_id:
            return VoiceActionResult(False, "主播音色未配置，请先在 AI 语音设置中完成克隆")

        output_dir = VOICE_DATA_DIR / "anchor" / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use the fixed audio file generated by the Anchor Settings dialog
        anchor_wav = output_dir / "anchor.wav"
        if anchor_wav.is_file():
            self._log(f"使用已生成音频: anchor.wav")
            return VoiceActionResult(True, "使用已生成音频", output_path=str(anchor_wav))

        # Fallback: generate via TTS provider
        provider = self._voice_manager.provider()
        speed = DEFAULT_SPEED_RATIO
        volume = DEFAULT_VOLUME_RATIO * (anchor.volume_gain / 100.0)
        result = await provider.synthesize(
            text=text,
            voice_id=voice_entry.clone_voice_id,
            model_id=settings.model_id,
            output_dir=output_dir,
            speed=speed,
            volume=max(0.0, min(2.0, volume)),
        )
        if result.ok:
            self._log(f"音频就绪: {Path(result.output_path).name}")
        return result

    async def _heygem_synthesize(
        self, config: PipelineConfig, wav_path: str,
    ) -> dict:
        """提交 WAV + anchor 给 HeyGem，等出 mp4。

        HeyGem 客户端是同步阻塞（requests.post + sleep poll），
        放 asyncio.to_thread 里跑避免阻塞 Qt event loop。

        返回 dict：成功 {ok: True, mp4_path: str}；失败 {ok: False, message: str}
        """
        from heygem_realtime.batch_client import (
            DEFAULT_DATA_ROOT,
            HeyGemBatchClient,
            HeyGemBatchError,
            HeyGemServiceNotReady,
        )

        self._log(f"HeyGem 入参 anchor={config.heygem_avatar_video_path!r} wav={wav_path!r}")
        if not config.heygem_avatar_video_path:
            return {
                "ok": False,
                "message": "已勾选「使用 HeyGem」但未选择 anchor 视频，请先在推流控制区点「选择 anchor mp4」",
            }
        avatar = Path(config.heygem_avatar_video_path)
        if not avatar.is_file():
            return {"ok": False, "message": f"HeyGem anchor 视频不存在: {avatar}"}

        # HeyGem 硬编码 /code/data/temp/，wav 必须在 <data_root>/temp/ 下
        # TTS 输出在 voice_manager 的 anchor/generated/，先 copy 过去
        temp_dir = (DEFAULT_DATA_ROOT / "temp").resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        src_wav = Path(wav_path).resolve()
        try:
            src_wav.relative_to(temp_dir)
            wav_in_temp = src_wav
        except ValueError:
            wav_in_temp = temp_dir / f"aiszr_anchor_{src_wav.stem}.wav"
            shutil.copy2(src_wav, wav_in_temp)
            self._log(f"WAV → {wav_in_temp.name}")

        # avatar 也要在 temp/ 下，不在就 copy
        try:
            avatar.resolve().relative_to(temp_dir)
            avatar_in_temp = avatar.resolve()
        except ValueError:
            avatar_in_temp = temp_dir / f"aiszr_avatar_{avatar.stem}.mp4"
            if not avatar_in_temp.is_file():
                shutil.copy2(avatar, avatar_in_temp)
            self._log(f"anchor → {avatar_in_temp.name}")

        self._set_state(PipelineState.HEYGEM_SYNTHESIZING)
        self._log(f"submit wav={wav_in_temp.name} avatar={avatar_in_temp.name}")

        client = HeyGemBatchClient(
            data_root=DEFAULT_DATA_ROOT,
            timeout_sec=config.heygem_timeout_sec,
        )

        def _run_sync():
            if not client.is_alive():
                raise HeyGemServiceNotReady(
                    f"{client.base_url} 不可达 — docker 容器没起来？"
                )
            return client.synthesize(
                wav_path=wav_in_temp,
                avatar_video_path=avatar_in_temp,
                chaofen=0,
            )

        try:
            result = await asyncio.to_thread(_run_sync)
        except HeyGemServiceNotReady as exc:
            return {"ok": False, "message": f"HeyGem 服务不可达：{exc}"}
        except HeyGemBatchError as exc:
            return {"ok": False, "message": f"HeyGem 合成失败：{exc}"}
        except Exception as exc:
            import traceback
            return {"ok": False, "message": f"HeyGem 未知错误：{exc}\n{traceback.format_exc()[:800]}"}

        if not result.mp4_abs_path.is_file():
            return {
                "ok": False,
                "message": f"HeyGem 报成功但 mp4 不存在: {result.mp4_abs_path}",
            }

        self._log(
            f"HeyGem OK {result.elapsed_sec:.1f}s mp4={result.mp4_abs_path.name}"
        )
        return {"ok": True, "mp4_path": str(result.mp4_abs_path)}

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


def _make_hls_handler(directory: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format, *args):
            pass
    return Handler
