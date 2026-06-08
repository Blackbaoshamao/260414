import wave

import ui_dialogs.voiceclonedialog as voiceclonedialog
from ui_dialogs.voiceclonedialog import VoiceCloneDialog
from voice_models import VoiceSettings


class _TextBox:
    def __init__(self, value=""):
        self.value = value

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value


class _Button:
    def __init__(self):
        self.enabled = True
        self.text_value = ""

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, value):
        self.text_value = value


class _Signal:
    def __init__(self):
        self.payloads = []

    def emit(self, payload):
        self.payloads.append(payload)


def _write_wav(path, seconds=0.1, rate=24000):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * frames)


def test_voice_clone_upload_validates_selected_wav_without_crashing(monkeypatch, tmp_path):
    sample = tmp_path / "sample.wav"
    _write_wav(sample, seconds=5.0)
    dummy = type(
        "DummyDialog",
        (),
        {
            "_status_label": _TextBox(),
            "_sample_path_edit": _TextBox(),
            "_sample_label": _TextBox(),
            "_voice_settings_state": VoiceSettings(),
            "_sample_path": "",
        },
    )()
    monkeypatch.setattr(
        voiceclonedialog.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(sample), ""),
    )
    monkeypatch.setattr(
        voiceclonedialog.QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: ([str(sample)], ""),
    )

    VoiceCloneDialog._on_upload_clicked(dummy)

    assert dummy._sample_path == str(sample)
    assert dummy._sample_path_edit.value == str(sample)


def test_voice_clone_click_saves_local_voice_without_crashing(monkeypatch, tmp_path):
    sample = tmp_path / "sample.wav"
    _write_wav(sample)
    saved = []
    dummy = type(
        "DummyDialog",
        (),
        {
            "_name_edit": _TextBox("anchor"),
            "_sample_path_edit": _TextBox(str(sample)),
            "_status_label": _TextBox(),
            "_clone_btn": _Button(),
            "_voice_settings_state": VoiceSettings(),
            "_sample_path": str(sample),
            "voice_settings_changed": _Signal(),
            "voice_action_requested": _Signal(),
            "_start_loading": lambda self: None,
        },
    )()
    monkeypatch.setattr(voiceclonedialog, "_load_settings", lambda: {})
    monkeypatch.setattr(voiceclonedialog, "_save_settings", lambda data: saved.append(data))
    monkeypatch.setattr(voiceclonedialog.QTimer, "singleShot", lambda _ms, callback: callback())

    VoiceCloneDialog._on_clone_clicked(dummy)

    assert saved
    assert dummy._voice_settings_state.voices[0].sample_wav_path == str(sample)
    assert dummy._voice_settings_state.anchor.voice_id == dummy._voice_settings_state.voices[0].id
    assert dummy.voice_action_requested.payloads[0]["type"] == "clone"
