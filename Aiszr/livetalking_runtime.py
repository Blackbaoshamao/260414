"""Runtime adapter for LiveTalking RTMP lip-sync output."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx


LogCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]


@dataclass(slots=True)
class LiveTalkingRuntimeConfig:
    livetalking_root: str = ""
    mediamtx_exe_path: str = ""
    python_exe_path: str = ""
    wav2lip384_weight_path: str = ""
    avatar_id: str = ""
    listen_port: int = 8010
    push_url: str = "rtmp://127.0.0.1:1935/live/aiszr"
    batch_size: int = 4
    modelres: int = 384
    avatar_video_path: str = ""
    output_dir: str = ""


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def external_deps_root() -> Path:
    return project_root().parent / "external_deps"


def bundled_wav2lip_package_root() -> Path:
    return Path(r"D:\下载\wav2lip免费整合包\wav2lip256&384加密版V1.1")


def default_livetalking_root() -> Path:
    return external_deps_root() / "LiveTalking"


def default_mediamtx_exe_path() -> Path:
    return external_deps_root() / "mediamtx" / "mediamtx.exe"


def default_python_exe_path() -> Path:
    bundled_python = bundled_wav2lip_package_root() / "wav2lip_voxcpm" / "python.exe"
    if bundled_python.is_file():
        return bundled_python
    return Path(sys.executable)


def default_wav2lip384_weight_path() -> Path:
    packaged = external_deps_root() / "weights" / "wav2lip384" / "wav2lip384.pth"
    if packaged.is_file():
        return packaged
    downloaded = bundled_wav2lip_package_root() / "weights" / "wav2lip" / "wav2lip384.pth"
    if downloaded.is_file():
        return downloaded
    return packaged


def find_single_wav(folder_path: str) -> Path | None:
    if not folder_path:
        return None
    folder = Path(folder_path).expanduser()
    if not folder.is_dir():
        return None
    wavs = [
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() == ".wav"
    ]
    if not wavs:
        return None
    return max(wavs, key=lambda path: (path.stat().st_mtime, path.name.lower()))


class LiveTalkingRuntime:
    """Starts MediaMTX and LiveTalking, with optional WAV upload looping."""

    SESSION_ID = "0"

    def __init__(
        self,
        log_callback: LogCallback | None = None,
        error_callback: ErrorCallback | None = None,
    ):
        self._log = log_callback or (lambda message: None)
        self._on_error = error_callback or (lambda message: None)
        self._mediamtx_proc: asyncio.subprocess.Process | None = None
        self._livetalking_proc: asyncio.subprocess.Process | None = None
        self._audio_loop_task: asyncio.Task | None = None
        self._pump_tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    async def start(
        self,
        config: LiveTalkingRuntimeConfig,
        wav_path: str | None = None,
        *,
        loop_audio: bool = True,
    ) -> dict:
        self._stop_event.clear()
        try:
            resolved = self._resolve_config(config)
            self._validate_config(resolved)

            normalized_wav = (
                await self._normalize_wav(wav_path, resolved)
                if wav_path is not None
                else None
            )
            avatar_id = await self._ensure_avatar(resolved)

            await self._start_mediamtx(resolved)
            await self._start_livetalking(resolved, avatar_id)
            await self._wait_until_ready(resolved.listen_port)

            if normalized_wav is not None:
                # Send once before returning so the UI does not enter STREAMING while
                # LiveTalking is still rejecting audio uploads.
                await self.send_audio_once(resolved.listen_port, normalized_wav)
                if loop_audio:
                    self._audio_loop_task = asyncio.create_task(
                        self._loop_audio(resolved.listen_port, normalized_wav, wait_first=True)
                    )
                    self._audio_loop_task.add_done_callback(self._on_audio_loop_done)
            self._log(f"LiveTalking RTMP: {resolved.push_url}")
            return {
                "ok": True,
                "rtmp_url": resolved.push_url,
                "audio_path": str(normalized_wav) if normalized_wav is not None else "",
                "avatar_id": avatar_id,
                "listen_port": resolved.listen_port,
            }
        except Exception:
            with contextlib.suppress(Exception):
                await self.stop()
            raise

    async def stop(self) -> None:
        self._stop_event.set()

        if self._audio_loop_task is not None:
            self._audio_loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._audio_loop_task
            self._audio_loop_task = None

        await self._terminate_process(self._livetalking_proc, "LiveTalking")
        self._livetalking_proc = None
        await self._terminate_process(self._mediamtx_proc, "MediaMTX")
        self._mediamtx_proc = None

        for task in self._pump_tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._pump_tasks, return_exceptions=True)
        self._pump_tasks.clear()

    def _resolve_config(self, config: LiveTalkingRuntimeConfig) -> LiveTalkingRuntimeConfig:
        return LiveTalkingRuntimeConfig(
            livetalking_root=str(Path(config.livetalking_root).expanduser())
            if config.livetalking_root else str(default_livetalking_root()),
            mediamtx_exe_path=str(Path(config.mediamtx_exe_path).expanduser())
            if config.mediamtx_exe_path else str(default_mediamtx_exe_path()),
            python_exe_path=str(Path(config.python_exe_path).expanduser())
            if config.python_exe_path else str(default_python_exe_path()),
            wav2lip384_weight_path=str(Path(config.wav2lip384_weight_path).expanduser())
            if config.wav2lip384_weight_path else str(default_wav2lip384_weight_path()),
            avatar_id=config.avatar_id.strip(),
            listen_port=int(config.listen_port or 8010),
            push_url=config.push_url.strip() or "rtmp://127.0.0.1:1935/live/aiszr",
            batch_size=max(1, int(config.batch_size or 4)),
            modelres=max(1, int(config.modelres or 384)),
            avatar_video_path=config.avatar_video_path,
            output_dir=config.output_dir,
        )

    def _validate_config(self, config: LiveTalkingRuntimeConfig) -> None:
        livetalking_root = Path(config.livetalking_root)
        checks = (
            (livetalking_root / "app.py", "LiveTalking app.py"),
            (Path(config.mediamtx_exe_path), "MediaMTX exe"),
            (Path(config.python_exe_path), "Python exe"),
            (Path(config.wav2lip384_weight_path), "wav2lip384 weight"),
        )
        missing = [label for path, label in checks if not path.is_file()]
        if missing:
            raise FileNotFoundError("缺少 LiveTalking 运行文件: " + ", ".join(missing))
        if config.avatar_video_path and not Path(config.avatar_video_path).is_file():
            raise FileNotFoundError(f"主播视频不存在: {config.avatar_video_path}")

    async def _normalize_wav(self, wav_path: str, config: LiveTalkingRuntimeConfig) -> Path:
        from ffmpeg_ops import _resolve_ffmpeg_path

        src = Path(wav_path)
        if not src.is_file():
            raise FileNotFoundError(f"WAV 不存在: {src}")
        out_dir = Path(config.output_dir) if config.output_dir else project_root() / "Aiszr" / "data" / "digital_human"
        out_dir = out_dir / "livetalking_audio"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "active_16k_mono.wav"

        cmd = [
            _resolve_ffmpeg_path(),
            "-y",
            "-i", str(src),
            "-ac", "1",
            "-ar", "16000",
            "-sample_fmt", "s16",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not out_path.is_file():
            tail = stderr.decode(errors="replace")[-500:]
            raise RuntimeError(f"WAV 自动转换失败: {tail}")
        self._log(f"LiveTalking 音频就绪: {out_path.name}")
        return out_path

    async def _ensure_avatar(self, config: LiveTalkingRuntimeConfig) -> str:
        root = Path(config.livetalking_root)
        avatar_id = config.avatar_id or self._avatar_id_for_video(config.avatar_video_path, config.modelres)
        avatar_dir = root / "data" / "avatars" / avatar_id
        if self._avatar_complete(avatar_dir):
            self._log(f"LiveTalking avatar 已存在: {avatar_id}")
            return avatar_id

        if not config.avatar_video_path:
            raise FileNotFoundError("LiveTalking 需要主播视频来生成 avatar")

        self._log(f"生成 LiveTalking avatar: {avatar_id}")
        cmd = [
            config.python_exe_path,
            "-m", "avatars.wav2lip.genavatar",
            "--video_path", str(Path(config.avatar_video_path)),
            "--avatar_id", avatar_id,
            "--save_path", "data/avatars",
            "--img_size", str(config.modelres),
            "--face_det_batch_size", "1",
        ]
        env = os.environ.copy()
        env.setdefault("CUDA_LAUNCH_BLOCKING", "1")
        env.setdefault("LIVETALKING_AVATAR_DEVICE", "cpu")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=20 * 1024 * 1024,
        )
        stdout, stderr = await self._communicate_or_finish_avatar(proc, avatar_dir)
        avatar_ready = self._avatar_complete(avatar_dir)
        if proc.returncode != 0 and not avatar_ready:
            output = (stdout + stderr).decode(errors="replace")
            log_path = self._write_avatar_failure_log(config, avatar_id, cmd, output)
            raise RuntimeError(
                f"LiveTalking avatar 生成失败，完整日志: {log_path}\n"
                f"{output[-1500:]}"
            )
        if not avatar_ready:
            raise RuntimeError(f"LiveTalking avatar 生成后文件不完整: {avatar_dir}")
        return avatar_id

    async def _communicate_or_finish_avatar(
        self,
        proc: asyncio.subprocess.Process,
        avatar_dir: Path,
    ) -> tuple[bytes, bytes]:
        loop = asyncio.get_running_loop()
        complete_since: float | None = None
        deadline = loop.time() + 20 * 60
        while proc.returncode is None:
            now = loop.time()
            if now > deadline:
                proc.kill()
                await proc.wait()
                break
            if self._avatar_complete(avatar_dir):
                if complete_since is None:
                    complete_since = now
                elif now - complete_since >= 2.0:
                    self._log("LiveTalking avatar 文件已完整，结束预处理进程")
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                    break
            else:
                complete_since = None
            await asyncio.sleep(0.5)
        return await proc.communicate()

    def _avatar_complete(self, avatar_dir: Path) -> bool:
        full_imgs = avatar_dir / "full_imgs"
        face_imgs = avatar_dir / "face_imgs"
        return (
            (avatar_dir / "coords.pkl").is_file()
            and full_imgs.is_dir()
            and face_imgs.is_dir()
            and any(full_imgs.glob("*.png"))
            and any(face_imgs.glob("*.png"))
        )

    def _write_avatar_failure_log(
        self,
        config: LiveTalkingRuntimeConfig,
        avatar_id: str,
        cmd: list[str],
        output: str,
    ) -> Path:
        out_dir = (
            Path(config.output_dir)
            if config.output_dir
            else project_root() / "Aiszr" / "data" / "digital_human"
        )
        log_dir = out_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"livetalking_avatar_{avatar_id}_error.log"
        log_path.write_text(
            "Command:\n"
            + " ".join(cmd)
            + "\n\nEnvironment:\n"
            + "CUDA_LAUNCH_BLOCKING=1\n"
            + "LIVETALKING_AVATAR_DEVICE=cpu\n\n"
            + "Output:\n"
            + output,
            encoding="utf-8",
        )
        return log_path

    def _avatar_id_for_video(self, video_path: str, modelres: int) -> str:
        digest = hashlib.sha1(str(Path(video_path).resolve()).encode("utf-8")).hexdigest()[:10]
        return f"aiszr_wav2lip{modelres}_{digest}"

    async def _start_mediamtx(self, config: LiveTalkingRuntimeConfig) -> None:
        if self._mediamtx_proc and self._mediamtx_proc.returncode is None:
            return
        exe = Path(config.mediamtx_exe_path)
        self._log("启动 MediaMTX RTMP 服务")
        self._mediamtx_proc = await asyncio.create_subprocess_exec(
            str(exe),
            cwd=str(exe.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._pump_process_logs(self._mediamtx_proc, "MediaMTX")
        await asyncio.sleep(0.8)
        if self._mediamtx_proc.returncode is not None:
            raise RuntimeError("MediaMTX 启动后立即退出")

    async def _start_livetalking(self, config: LiveTalkingRuntimeConfig, avatar_id: str) -> None:
        if self._livetalking_proc and self._livetalking_proc.returncode is None:
            return
        root = Path(config.livetalking_root)
        self._log("启动 LiveTalking RTMP 推理服务")
        cmd = [
            config.python_exe_path,
            "app.py",
            "--transport", "rtmp",
            "--model", "wav2lip",
            "--avatar_id", avatar_id,
            "--batch_size", str(config.batch_size),
            "--modelres", str(config.modelres),
            "--modelfile", str(Path(config.wav2lip384_weight_path)),
            "--push_url", config.push_url,
            "--listenport", str(config.listen_port),
        ]
        self._livetalking_proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=20 * 1024 * 1024,
        )
        self._pump_process_logs(self._livetalking_proc, "LiveTalking")

    async def _wait_until_ready(self, port: int) -> None:
        url = f"http://127.0.0.1:{port}/api/admin/config"
        deadline = asyncio.get_running_loop().time() + 120
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_running_loop().time() < deadline:
                if self._livetalking_proc and self._livetalking_proc.returncode is not None:
                    raise RuntimeError("LiveTalking 启动后立即退出")
                with contextlib.suppress(Exception):
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return
                await asyncio.sleep(0.5)
        raise TimeoutError("LiveTalking 启动超时")

    async def _loop_audio(self, port: int, wav_path: Path, *, wait_first: bool = False) -> None:
        duration = _wav_duration_sec(wav_path) or 1.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            if wait_first:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, duration))
            while not self._stop_event.is_set():
                await self.send_audio_once(port, wav_path, client=client)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, duration))

    async def send_audio_once(
        self,
        port: int,
        wav_path: Path,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        url = f"http://127.0.0.1:{port}/humanaudio"
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
        try:
            with wav_path.open("rb") as handle:
                files = {"file": (wav_path.name, handle, "audio/wav")}
                data = {"sessionid": self.SESSION_ID}
                resp = await client.post(url, data=data, files=files)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"LiveTalking 接收音频失败: {payload}")
            self._log(f"LiveTalking 已发送音频: {wav_path.name}")
        finally:
            if owns_client:
                await client.aclose()

    async def _send_audio_once(
        self,
        port: int,
        wav_path: Path,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        await self.send_audio_once(port, wav_path, client=client)

    def _on_audio_loop_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or self._stop_event.is_set():
            return
        exc = task.exception()
        if exc is None:
            return
        message = f"LiveTalking 音频循环中断: {exc}"
        self._log(message)
        self._on_error(message)
        asyncio.create_task(self.stop())

    def _pump_process_logs(self, proc: asyncio.subprocess.Process, name: str) -> None:
        if proc.stdout is not None:
            self._pump_tasks.append(asyncio.create_task(self._pump_stream(proc.stdout, name)))
        if proc.stderr is not None:
            self._pump_tasks.append(asyncio.create_task(self._pump_stream(proc.stderr, name)))

    async def _pump_stream(self, stream: asyncio.StreamReader, name: str) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                self._log(f"{name}: {text[:300]}")

    async def _terminate_process(
        self,
        proc: asyncio.subprocess.Process | None,
        name: str,
    ) -> None:
        if proc is None or proc.returncode is not None:
            return
        self._log(f"停止 {name}")
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


def _wav_duration_sec(path: Path) -> float:
    with contextlib.suppress(Exception):
        with wave.open(str(path), "rb") as wav_file:
            rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            if rate:
                return frames / float(rate)
    return 0.0
