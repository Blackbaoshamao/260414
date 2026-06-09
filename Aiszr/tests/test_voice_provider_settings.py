import ast
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import local_voice_runtime as local_voice_runtime_module
import voice_manager as voice_manager_module
from ui_constants import _VOICE_PROVIDER_API_FIELDS
from local_voice_runtime import DEFAULT_LOCAL_VOICE_ENDPOINT
from voice_manager import LocalVoiceProvider, default_anchor_wav_path
from voice_models import (
    VOICE_MODELS,
    VOICE_PROVIDER_LABELS,
    VOICE_PROVIDERS,
    VoiceEntry,
    VoiceProviderApiConfig,
    VoiceRoleConfig,
    VoiceSettings,
)


@pytest.fixture(autouse=True)
def _local_runtime_ready(monkeypatch):
    async def ready(endpoint=DEFAULT_LOCAL_VOICE_ENDPOINT):
        return SimpleNamespace(ok=True, message="ready", endpoint=endpoint)

    monkeypatch.setattr(voice_manager_module, "ensure_local_voice_runtime", ready)


def _write_wav(path):
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 64)


def _write_wav_seconds(path, seconds=4):
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * int(24000 * seconds))


def _wav_bytes(tmp_path):
    path = tmp_path / "response.wav"
    _write_wav(path)
    return path.read_bytes()


def test_gpt_sovits_provider_is_registered():
    assert "local_voice" in VOICE_PROVIDERS
    assert VOICE_PROVIDER_LABELS["local_voice"] == "GPT-SoVITS 本地语音"
    assert VOICE_MODELS["local_voice"] == ("gpt-sovits-v2",)


def test_voice_settings_default_to_local_voice():
    settings = VoiceSettings()

    assert settings.provider == "local_voice"
    assert settings.model_id == "gpt-sovits-v2"
    assert settings.api["local_voice"].endpoint == DEFAULT_LOCAL_VOICE_ENDPOINT
    assert settings.api["local_voice"].prompt_lang == "zh"
    assert settings.api["local_voice"].text_lang == "zh"


def test_voice_settings_invalid_provider_falls_back_to_local_voice():
    settings = VoiceSettings.from_dict({"provider": "missing", "model_id": "missing"})

    assert settings.provider == "local_voice"
    assert settings.model_id == "gpt-sovits-v2"


def test_voice_settings_preserve_saved_aliyun_provider():
    settings = VoiceSettings.from_dict(
        {
            "provider": "aliyun_bailian",
            "model_id": "qwen3-tts-vc-2026-01-22",
            "api": {"aliyun_bailian": {"api_key": "saved-key"}},
        }
    )

    assert settings.provider == "aliyun_bailian"
    assert settings.model_id == "qwen3-tts-vc-2026-01-22"
    assert settings.api["aliyun_bailian"].api_key == "saved-key"


def test_voice_settings_accepts_gpt_sovits_config_fields(tmp_path):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)

    settings = VoiceSettings.from_dict(
        {
            "provider": "local_voice",
            "model_id": "gpt-sovits-v2",
            "api": {
                "local_voice": {
                    "endpoint": "http://127.0.0.1:9880",
                    "reference_audio": str(reference),
                    "prompt_text": "主播参考音频文本",
                    "prompt_lang": "zh",
                    "text_lang": "zh",
                }
            },
        }
    )

    api = settings.api["local_voice"]
    assert settings.provider == "local_voice"
    assert settings.model_id == "gpt-sovits-v2"
    assert api.endpoint == "http://127.0.0.1:9880"
    assert api.reference_audio == str(reference)
    assert api.prompt_text == "主播参考音频文本"
    assert api.prompt_lang == "zh"
    assert api.text_lang == "zh"
    assert settings.to_dict()["api"]["local_voice"]["reference_audio"] == str(reference)


def test_gpt_sovits_ui_fields_are_hidden_for_auto_config():
    fields = _VOICE_PROVIDER_API_FIELDS["local_voice"]["fields"]

    assert fields == {}


def test_gpt_sovits_provider_does_not_require_manual_endpoint():
    result = LocalVoiceProvider(VoiceProviderApiConfig()).missing_credentials()

    assert result == []


@pytest.mark.asyncio
async def test_gpt_sovits_create_clone_uses_reference_wav_path(tmp_path):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    provider = LocalVoiceProvider(VoiceProviderApiConfig(endpoint="http://127.0.0.1:9880"))

    result = await provider.create_clone(str(reference), model_id="gpt-sovits-v2")

    assert result.ok is True
    assert result.clone_voice_id == str(reference.resolve())
    assert result.clone_status == "ready"


@pytest.mark.asyncio
async def test_gpt_sovits_synthesize_posts_to_tts_and_writes_wav(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    calls = []

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    provider = LocalVoiceProvider(
        VoiceProviderApiConfig(
            endpoint="http://127.0.0.1:9880/",
            prompt_text="参考音频文本",
            prompt_lang="zh",
            text_lang="zh",
        )
    )

    result = await provider.synthesize(
        "欢迎来到直播间",
        str(reference),
        tmp_path,
        model_id="gpt-sovits-v2",
        speed=1.25,
    )

    assert result.ok is True
    assert result.output_path.endswith(".wav")
    assert calls[0][0] == "http://127.0.0.1:9880/tts"
    assert calls[0][2] == 300.0
    payload = calls[0][1]
    assert payload["text"] == "欢迎来到直播间"
    assert payload["text_lang"] == "zh"
    assert payload["ref_audio_path"] == str(reference)
    assert payload["prompt_text"] == "参考音频文本"
    assert payload["prompt_lang"] == "zh"
    assert payload["text_split_method"] == "cut0"
    assert payload["batch_size"] == 1
    assert payload["media_type"] == "wav"
    assert payload["streaming_mode"] is False
    assert payload["speed_factor"] == pytest.approx(1.25)
    assert payload["top_k"] == 10
    assert payload["top_p"] == pytest.approx(0.85)
    assert payload["temperature"] == pytest.approx(0.7)
    assert payload["parallel_infer"] is False
    assert payload["repetition_penalty"] == pytest.approx(1.2)
    assert payload["seed"] == 1234


@pytest.mark.asyncio
async def test_gpt_sovits_synthesize_falls_back_to_config_reference_audio(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    calls = []

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, *, json, timeout):
        calls.append(json)
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    provider = LocalVoiceProvider(
        VoiceProviderApiConfig(
            endpoint="http://127.0.0.1:9880",
            reference_audio=str(reference),
        )
    )

    result = await provider.synthesize("测试", "", tmp_path)

    assert result.ok is True
    assert calls[0]["ref_audio_path"] == str(reference)


@pytest.mark.asyncio
async def test_gpt_sovits_trained_model_uses_pretrained_gpt_by_default(tmp_path, monkeypatch):
    model_dir = tmp_path / "trained_models" / "voice"
    sliced_dir = model_dir / "sliced"
    asr_dir = model_dir / "asr_output"
    sovits_dir = model_dir / "logs_s2_v2"
    sliced_dir.mkdir(parents=True)
    asr_dir.mkdir()
    sovits_dir.mkdir()
    reference = sliced_dir / "ref.wav"
    _write_wav_seconds(reference)
    (asr_dir / "sliced.list").write_text(
        f"{reference}|sliced|ZH|参考文本\n",
        encoding="utf-8",
    )
    fine_tuned_gpt = model_dir / "voice-e6.ckpt"
    fine_tuned_gpt.write_bytes(b"bad gpt")
    sovits_ckpt = sovits_dir / "G_100.pth"
    sovits_ckpt.write_bytes(b"sovits")
    pretrained_gpt = tmp_path / "pretrained.ckpt"
    pretrained_gpt.write_bytes(b"pretrained")
    calls = []
    switch_calls = []

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, *, json, timeout):
        calls.append(json)
        return FakeResponse()

    async def fake_switch(gpt_ckpt="", sovits_ckpt=""):
        switch_calls.append({"gpt_ckpt": gpt_ckpt, "sovits_ckpt": sovits_ckpt})
        return True, "ok"

    monkeypatch.delenv("GPT_SOVITS_USE_FINE_TUNED_GPT", raising=False)
    monkeypatch.setattr(voice_manager_module, "_default_local_voice_gpt_ckpt", lambda version="v2": pretrained_gpt)
    monkeypatch.setattr(local_voice_runtime_module, "switch_local_voice_weights", fake_switch)
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))

    provider = LocalVoiceProvider(VoiceProviderApiConfig(endpoint="http://127.0.0.1:9880"))

    result = await provider.synthesize(
        "测试",
        str(reference),
        tmp_path,
        trained_model_dir=str(model_dir),
    )

    assert result.ok is True
    assert switch_calls[0]["gpt_ckpt"] == str(pretrained_gpt)
    assert switch_calls[0]["gpt_ckpt"] != str(fine_tuned_gpt)
    assert switch_calls[0]["sovits_ckpt"] == str(sovits_ckpt)
    assert calls[0]["prompt_text"] == "参考文本"


@pytest.mark.asyncio
async def test_gpt_sovits_synthesize_reports_json_error(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)

    class FakeResponse:
        status_code = 400
        content = b'{"message":"ref_audio_path is required"}'
        headers = {"content-type": "application/json"}
        text = '{"message":"ref_audio_path is required"}'

        def json(self):
            return {"message": "ref_audio_path is required"}

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=lambda *args, **kwargs: FakeResponse()))
    provider = LocalVoiceProvider(VoiceProviderApiConfig(endpoint="http://127.0.0.1:9880"))

    result = await provider.synthesize("测试", str(reference), tmp_path)

    assert result.ok is False
    assert "ref_audio_path is required" in result.message


@pytest.mark.asyncio
async def test_gpt_sovits_cache_changes_when_prompt_or_language_changes(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    calls = []

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, *, json, timeout):
        calls.append(json)
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    first = LocalVoiceProvider(
        VoiceProviderApiConfig(
            endpoint="http://127.0.0.1:9880",
            prompt_text="第一段参考文本",
            prompt_lang="zh",
            text_lang="zh",
        )
    )
    second = LocalVoiceProvider(
        VoiceProviderApiConfig(
            endpoint="http://127.0.0.1:9880",
            prompt_text="second prompt",
            prompt_lang="en",
            text_lang="en",
        )
    )

    first_result = await first.synthesize("same text", str(reference), tmp_path)
    second_result = await second.synthesize("same text", str(reference), tmp_path)

    assert first_result.ok is True
    assert second_result.ok is True
    assert len(calls) == 2
    assert calls[0]["prompt_text"] == "第一段参考文本"
    assert calls[1]["prompt_text"] == "second prompt"
    assert calls[1]["prompt_lang"] == "en"
    assert calls[1]["text_lang"] == "en"
    assert first_result.output_path != second_result.output_path


@pytest.mark.asyncio
async def test_gpt_sovits_endpoint_accepts_tts_path(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    urls = []

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, *, json, timeout):
        urls.append(url)
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    provider = LocalVoiceProvider(VoiceProviderApiConfig(endpoint="http://127.0.0.1:9880/tts"))

    result = await provider.synthesize("测试", str(reference), tmp_path)

    assert result.ok is True
    assert urls == ["http://127.0.0.1:9880/tts"]


@pytest.mark.asyncio
async def test_default_anchor_wav_path_matches_gpt_sovits_cache_key(tmp_path, monkeypatch):
    reference = tmp_path / "anchor.wav"
    _write_wav(reference)
    voice_data_dir = tmp_path / "voice"
    monkeypatch.setattr(voice_manager_module, "VOICE_DATA_DIR", voice_data_dir)
    settings = VoiceSettings.from_dict(
        {
            "provider": "local_voice",
            "model_id": "gpt-sovits-v2",
            "api": {
                "local_voice": {
                    "endpoint": "http://127.0.0.1:9880",
                    "reference_audio": str(reference),
                    "prompt_text": "参考文本",
                    "prompt_lang": "zh",
                    "text_lang": "zh",
                }
            },
        }
    )
    settings.anchor_script = "same text"
    settings.voices.append(
        VoiceEntry(
            id="anchor-voice",
            name="anchor",
            clone_voice_id=str(reference),
            clone_status="ready",
        )
    )
    settings.anchor = VoiceRoleConfig(voice_id="anchor-voice")

    class FakeResponse:
        status_code = 200
        content = _wav_bytes(tmp_path)
        headers = {"content-type": "audio/wav"}
        text = ""

        def json(self):
            return {}

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(post=lambda *args, **kwargs: FakeResponse()),
    )
    provider = LocalVoiceProvider(settings.api["local_voice"])

    result = await provider.synthesize(
        settings.anchor_script,
        str(reference),
        voice_data_dir / "anchor" / "generated",
        model_id=settings.model_id,
    )

    assert result.ok is True
    assert Path(result.output_path) == default_anchor_wav_path(settings)


def test_digitalhuman_preview_passes_settings_to_voice_manager():
    source_path = Path(__file__).resolve().parents[1] / "ui_pages" / "digitalhumanpage.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    preview = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_do_preview_synth"
    )
    calls = [
        node
        for node in ast.walk(preview)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "VoiceManager"
    ]

    assert len(calls) == 1
    assert len(calls[0].args) == 1


def test_voice_config_preview_click_can_stop_current_audio():
    source_path = Path(__file__).resolve().parents[1] / "ui_pages" / "voiceconfigpage.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    preview = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_preview_clicked"
    )
    call_names = {
        node.func.id
        for node in ast.walk(preview)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "stop_all_audio" in call_names
    assert "_preview_playing_role" in source
    assert "_preview_token" in source
    assert "preview_token" in source
    assert "⏸ 暂停" in source


def test_voice_worker_echoes_preview_token_for_stale_result_filtering():
    source_path = Path(__file__).resolve().parents[1] / "ui.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_action = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_run_voice_action"
    )

    assert "preview_token" in ast.unparse(run_action)


def test_voice_config_preview_status_uses_audio_playing_text():
    source_path = Path(__file__).resolve().parents[1] / "ui_pages" / "voiceconfigpage.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    preview = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_preview_clicked"
    )
    preview_source = ast.unparse(preview)

    assert "音频播放中" in preview_source
    assert "试听生成中" not in preview_source


def test_voice_config_runtime_status_is_below_anchor_voice_section():
    source_path = Path(__file__).resolve().parents[1] / "ui_pages" / "voiceconfigpage.py"
    source = source_path.read_text(encoding="utf-8")
    anchor_params_index = source.index("container.addWidget(anchor_params)")
    status_index = source.index("container.addWidget(self._copilot_runtime_status)")
    copilot_title_index = source.index('container.addTitle("助播音色")')

    assert anchor_params_index < status_index < copilot_title_index


def test_digitalhuman_preview_state_signal_has_slot():
    source_path = Path(__file__).resolve().parents[1] / "ui_pages" / "digitalhumanpage.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    method_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }

    assert "_preview_state_changed.connect(self._apply_preview_state)" in source
    assert "_apply_preview_state" in method_names
