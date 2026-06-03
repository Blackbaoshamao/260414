from pathlib import Path
import pytest

import local_voice_runtime as lvr
from local_voice_runtime import LocalVoiceRuntime, default_gpt_sovits_python


@pytest.mark.asyncio
async def test_local_voice_runtime_reuses_listening_service(monkeypatch):
    runtime = LocalVoiceRuntime("http://127.0.0.1:9880")
    starts = []

    async def listening():
        return True

    async def start_process():
        starts.append(True)
        return lvr.LocalVoiceRuntimeResult(True, "started")

    monkeypatch.setattr(runtime, "_is_listening", listening)
    monkeypatch.setattr(runtime, "_start_process", start_process)

    result = await runtime.ensure_running()

    assert result.ok is True
    assert starts == []


@pytest.mark.asyncio
async def test_local_voice_runtime_starts_bundled_api(monkeypatch, tmp_path):
    root = tmp_path / "GPT-SoVITS"
    config = root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config", encoding="utf-8")
    api_script = root / "api_v2.py"
    api_script.write_text("print('api')", encoding="utf-8")
    python_exe = root / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    calls = []
    listening = [False, True]

    class FakeProc:
        returncode = None
        stdout = None

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    async def fake_is_listening(self):
        return listening.pop(0) if listening else True

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(lvr, "default_gpt_sovits_root", lambda: root)
    monkeypatch.setattr(lvr, "default_gpt_sovits_python", lambda _root: python_exe)
    monkeypatch.setattr(lvr, "default_gpt_sovits_config", lambda _root: config)
    monkeypatch.setattr(lvr.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(LocalVoiceRuntime, "_is_listening", fake_is_listening)
    monkeypatch.setattr(lvr.asyncio, "sleep", fake_sleep)

    runtime = LocalVoiceRuntime("http://127.0.0.1:9880")
    result = await runtime.ensure_running()

    assert result.ok is True
    assert calls
    cmd, kwargs = calls[0]
    assert Path(cmd[0]) == python_exe
    assert Path(cmd[1]) == api_script
    assert "-a" in cmd and "127.0.0.1" in cmd
    assert "-p" in cmd and "9880" in cmd
    assert "-c" in cmd and str(config) in cmd
    assert kwargs["cwd"] == str(root)


def test_local_voice_runtime_uses_bundled_py312_python(tmp_path, monkeypatch):
    root = tmp_path / "GPT-SoVITS"
    bundled_python = root / "py312" / "python.exe"
    bundled_python.parent.mkdir(parents=True)
    bundled_python.write_text("", encoding="utf-8")
    monkeypatch.delenv("GPT_SOVITS_PYTHON", raising=False)

    assert lvr.default_gpt_sovits_python(root) == bundled_python
