"""Runtime manager for bundled GPT-SoVITS local voice service."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app_paths import app_dir


DEFAULT_LOCAL_VOICE_ENDPOINT = "http://127.0.0.1:9880"


@dataclass(slots=True)
class LocalVoiceRuntimeResult:
    ok: bool
    message: str
    endpoint: str = DEFAULT_LOCAL_VOICE_ENDPOINT


def default_gpt_sovits_root() -> Path:
    env_root = os.getenv("GPT_SOVITS_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    base = app_dir()
    candidates = (
        base / "GPT-SoVITS",
        base.parent / "external_deps" / "GPT-SoVITS",
        base.parent / "GPT-SoVITS",
    )
    for candidate in candidates:
        if (candidate / "api_v2.py").is_file():
            return candidate
    return candidates[0]


def default_gpt_sovits_python(root: Path) -> Path:
    env_python = os.getenv("GPT_SOVITS_PYTHON", "").strip()
    if env_python:
        return Path(env_python).expanduser()
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / "runtime" / "python.exe",
        root / "py312" / "python.exe",
        root / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def default_gpt_sovits_config(root: Path) -> Path:
    env_config = os.getenv("GPT_SOVITS_CONFIG", "").strip()
    if env_config:
        return Path(env_config).expanduser()
    return root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"


def _endpoint_host_port(endpoint: str) -> tuple[str, int]:
    parsed = urlparse((endpoint or DEFAULT_LOCAL_VOICE_ENDPOINT).strip())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


class LocalVoiceRuntime:
    def __init__(self, endpoint: str = DEFAULT_LOCAL_VOICE_ENDPOINT):
        self.endpoint = (endpoint or DEFAULT_LOCAL_VOICE_ENDPOINT).strip().rstrip("/")
        self._proc: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task | None = None

    async def ensure_running(self) -> LocalVoiceRuntimeResult:
        if await self._is_listening():
            return LocalVoiceRuntimeResult(True, "GPT-SoVITS service is ready", self.endpoint)
        if self._proc is not None and self._proc.returncode is None:
            return await self._wait_until_ready()
        start_result = await self._start_process()
        if not start_result.ok:
            return start_result
        return await self._wait_until_ready()

    async def stop(self) -> None:
        if self._pump_task:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump_task
            self._pump_task = None
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def _start_process(self) -> LocalVoiceRuntimeResult:
        root = default_gpt_sovits_root()
        api_script = root / "api_v2.py"
        if not api_script.is_file():
            return LocalVoiceRuntimeResult(
                False,
                f"GPT-SoVITS runtime missing: {api_script}",
                self.endpoint,
            )
        config_path = default_gpt_sovits_config(root)
        if not config_path.is_file():
            return LocalVoiceRuntimeResult(
                False,
                f"GPT-SoVITS config missing: {config_path}",
                self.endpoint,
            )
        python_exe = default_gpt_sovits_python(root)
        if not python_exe.is_file():
            return LocalVoiceRuntimeResult(
                False,
                f"GPT-SoVITS python missing: {python_exe}",
                self.endpoint,
            )
        host, port = _endpoint_host_port(self.endpoint)
        cmd = [
            str(python_exe),
            str(api_script),
            "-a",
            host,
            "-p",
            str(port),
            "-c",
            str(config_path),
        ]
        kwargs = {
            "cwd": str(root),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        self._pump_task = asyncio.create_task(self._drain_stdout(self._proc))
        return LocalVoiceRuntimeResult(True, "GPT-SoVITS service starting", self.endpoint)

    async def _wait_until_ready(self, timeout_sec: float = 120.0) -> LocalVoiceRuntimeResult:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                return LocalVoiceRuntimeResult(
                    False,
                    f"GPT-SoVITS service exited: {self._proc.returncode}",
                    self.endpoint,
                )
            if await self._is_listening():
                return LocalVoiceRuntimeResult(True, "GPT-SoVITS service is ready", self.endpoint)
            await asyncio.sleep(0.5)
        return LocalVoiceRuntimeResult(False, "GPT-SoVITS service startup timed out", self.endpoint)

    async def _is_listening(self) -> bool:
        host, port = _endpoint_host_port(self.endpoint)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=1.0,
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            with contextlib.suppress(Exception):
                reader.feed_eof()
            return True
        except Exception:
            return False

    async def switch_weights(self, gpt_ckpt: str = "", sovits_ckpt: str = "") -> tuple[bool, str]:
        """热切换 GPT/SoVITS 模型权重。"""
        if not await self._is_listening():
            return False, "服务未启动"
        import httpx

        endpoint = self.endpoint
        if sovits_ckpt:
            try:
                resp = await asyncio.to_thread(
                    httpx.get, f"{endpoint}/set_sovits_weights",
                    params={"weights_path": sovits_ckpt}, timeout=10.0,
                )
                if resp.status_code != 200:
                    return False, f"切换 SoVITS 权重失败：{resp.text}"
            except Exception as e:
                return False, f"切换 SoVITS 权重异常：{e}"
        if gpt_ckpt:
            try:
                resp = await asyncio.to_thread(
                    httpx.get, f"{endpoint}/set_gpt_weights",
                    params={"weights_path": gpt_ckpt}, timeout=10.0,
                )
                if resp.status_code != 200:
                    return False, f"切换 GPT 权重失败：{resp.text}"
            except Exception as e:
                return False, f"切换 GPT 权重异常：{e}"
        return True, "模型权重已切换"

    @staticmethod
    async def _drain_stdout(proc: asyncio.subprocess.Process) -> None:
        stream = proc.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.readline()
            if not chunk:
                break


_runtime: LocalVoiceRuntime | None = None


async def ensure_local_voice_runtime(endpoint: str = DEFAULT_LOCAL_VOICE_ENDPOINT) -> LocalVoiceRuntimeResult:
    global _runtime
    normalized = (endpoint or DEFAULT_LOCAL_VOICE_ENDPOINT).strip().rstrip("/")
    if _runtime is None or _runtime.endpoint != normalized:
        if _runtime is not None:
            await _runtime.stop()
        _runtime = LocalVoiceRuntime(normalized)
    return await _runtime.ensure_running()


async def stop_local_voice_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.stop()
        _runtime = None


async def switch_local_voice_weights(gpt_ckpt: str = "", sovits_ckpt: str = "") -> tuple[bool, str]:
    """切换本地语音服务的模型权重。"""
    global _runtime
    if _runtime is None:
        return False, "本地语音服务未初始化"
    return await _runtime.switch_weights(gpt_ckpt, sovits_ckpt)


def resolve_gpt_sovits_root() -> Path:
    """解析 GPT-SoVITS 根目录。"""
    return default_gpt_sovits_root()


def resolve_python_exe() -> str:
    """解析 GPT-SoVITS 使用的 Python 可执行文件。"""
    root = default_gpt_sovits_root()
    return str(default_gpt_sovits_python(root))
