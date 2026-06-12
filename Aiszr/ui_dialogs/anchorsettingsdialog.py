"""AnchorSettingsDialog — extracted from ui.py."""
from __future__ import annotations
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _hex_with_alpha
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme
from voice_models import VoiceSettings


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFrame, QSpinBox, QDoubleSpinBox, QComboBox, QListWidgetItem)
import ui_theme as theme
from ui_settings import _save_settings
from ui_components import MacButton


class AnchorSettingsDialog(QDialog):
    voice_settings_changed = pyqtSignal(object)
    _generate_finished = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("主播设置")
        self.resize(560, 600)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title_label = QLabel("主播设置", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        layout.addSpacing(4)
        self._product_label = QLabel("商品详情", self)
        self._product_label.setFont(theme.FONT_HEADLINE)
        layout.addWidget(self._product_label)

        text_ss = f"""
            QTextEdit {{
                background-color: {theme.CLR_BG_ELEVATED};
                color: {theme.CLR_TEXT_PRI};
                border: none;
                border-radius: 12px;
                padding: 12px 14px;
                font-size: 15px;
                selection-background-color: {_hex_with_alpha(theme.CLR_ACCENT_LIGHT, 100)};
                selection-color: {theme.CLR_TEXT_PRI};
            }}
            QTextEdit:focus {{
                border: 2px solid {_mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT_LIGHT, 0.5)};
                padding: 11px 13px;
            }}
        """
        self._product_edit = QTextEdit(self)
        self._product_edit.setFixedHeight(90)
        self._product_edit.setPlaceholderText("输入商品名称、卖点、价格等详细信息...")
        self._product_edit.setStyleSheet(text_ss)
        layout.addWidget(self._product_edit)

        gen_row = QWidget(self)
        gen_layout = QHBoxLayout(gen_row)
        gen_layout.setContentsMargins(0, 2, 0, 2)
        gen_layout.setSpacing(theme.SPACING_SM)
        self._generate_btn = MacButton("AI 生成带货话术", variant="primary", parent=self)
        self._generate_btn.setMinimumSize(160, 38)
        gen_layout.addWidget(self._generate_btn)
        gen_layout.addStretch(1)
        layout.addWidget(gen_row)

        self._gen_status_label = QLabel("", self)
        self._gen_status_label.setWordWrap(True)
        layout.addWidget(self._gen_status_label)

        layout.addSpacing(8)
        from ui_components import MacSeparator
        layout.addWidget(MacSeparator())
        layout.addSpacing(4)

        self._script_label = QLabel("主播话术", self)
        self._script_label.setFont(theme.FONT_HEADLINE)
        layout.addWidget(self._script_label)

        self._script_edit = QTextEdit(self)
        self._script_edit.setPlaceholderText("输入主播话术内容，或使用上方 AI 生成...")
        self._script_edit.setStyleSheet(text_ss)
        layout.addWidget(self._script_edit)

        self._save_btn = MacButton("生成音频", variant="secondary", parent=self)
        self._save_btn.setMinimumSize(100, 38)
        layout.addWidget(self._save_btn)

        self._status_label = QLabel("", self)
        layout.addWidget(self._status_label)

        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_finished.connect(self._on_generate_done)
        self._save_btn.clicked.connect(self._on_save)
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        self._product_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        self._script_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        self._generate_btn.apply_theme_styles()
        self._save_btn.apply_theme_styles()

    def _on_generate_clicked(self):
        product_text = self._product_edit.toPlainText().strip()
        if not product_text:
            self._gen_status_label.setText("请先输入商品详情")
            return
        settings = _load_settings()
        api_key = str(settings.get("api_key", "")).strip()
        if not api_key:
            self._gen_status_label.setText("请先在 AI 配置页面设置 API Key")
            return
        self._gen_status_label.setText("正在生成...")
        self._generate_btn.setEnabled(False)
        import threading
        thread = threading.Thread(
            target=self._do_generate,
            args=(api_key, settings, product_text),
            daemon=True,
        )
        thread.start()

    def _do_generate(self, api_key: str, settings: dict, product_text: str):
        try:
            import httpx
            base_url = str(settings.get("base_url", "https://api.deepseek.com/v1")).strip()
            model = str(settings.get("model", "deepseek-chat")).strip()
            client = httpx.Client(
                base_url=base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=60.0,
            )
            resp = client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一名顶级带货主播的话术策划师。根据用户提供的商品信息，生成一段具有顶级带货主播水准的口播话术。话术要有节奏感、感染力和说服力，善于制造紧迫感、建立信任、激发购买欲。规则：1.只用用户提供的信息，绝不编造价格、配置、赠品、库存等未提及的内容；2.不加【章节标题】或结构标记，直接输出连续的话术文本；3.风格要像经验丰富的高级带货主播自然说话，语气有张有弛，能拿捏观众情绪；4.只输出话术原文，不要加任何括号说明、表演指导、语速提示、情绪标注等非话术内容。"},
                        {"role": "user", "content": product_text},
                    ],
                    "max_tokens": 8000,
                    "temperature": 0.85,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            result = data["choices"][0]["message"]["content"]
            client.close()
            self._generate_finished.emit(result, True)
        except Exception as e:
            self._generate_finished.emit(str(e), False)

    def _on_generate_done(self, text: str, ok: bool):
        self._generate_btn.setEnabled(True)
        if ok:
            self._script_edit.setPlainText(text)
            self._gen_status_label.setText("生成完成")
        else:
            self._gen_status_label.setText(f"生成失败：{text}")

    def _on_save(self):
        script = self._script_edit.toPlainText().strip()
        if not script:
            self._status_label.setText("请先输入主播话术")
            return
        self._voice_settings_state.anchor_script = script
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())

        # Delete old generated audio files, keep only the latest
        from voice_manager import VOICE_DATA_DIR, synthesis_voice_id_for_role
        gen_dir = VOICE_DATA_DIR / "anchor" / "generated"
        if gen_dir.exists():
            for f in gen_dir.glob("*"):
                f.unlink(missing_ok=True)

        # Generate new audio via TTS
        anchor = self._voice_settings_state.anchor
        if synthesis_voice_id_for_role(self._voice_settings_state, anchor) is None:
            self._status_label.setText("请先在 AI 语音设置中完成主播音色克隆")
            return

        self._save_btn.setEnabled(False)
        self._status_label.setText("正在生成音频...")

        import threading
        thread = threading.Thread(
            target=self._do_synthesize,
            args=(self._voice_settings_state,),
            daemon=True,
        )
        thread.start()

    def _do_synthesize(self, settings: VoiceSettings):
        try:
            from pathlib import Path as _P
            import asyncio
            from voice_manager import (
                DEFAULT_SPEED_RATIO,
                DEFAULT_VOLUME_RATIO,
                VOICE_DATA_DIR,
                synthesis_voice_id_for_role,
            )

            anchor = settings.anchor
            synth_voice_id = synthesis_voice_id_for_role(settings, anchor)
            if synth_voice_id is None:
                raise ValueError("请先在 AI 语音设置中完成主播音色克隆")
            output_dir = VOICE_DATA_DIR / "anchor" / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)

            async def _synth():
                from voice_manager import PROVIDER_TYPES
                provider_cls = PROVIDER_TYPES.get(settings.provider)
                if not provider_cls:
                    raise ValueError(f"未知语音供应商: {settings.provider}")
                provider = provider_cls(settings.api.get(settings.provider, {}))
                speed = DEFAULT_SPEED_RATIO
                volume = DEFAULT_VOLUME_RATIO * (anchor.volume_gain / 100.0)
                return await provider.synthesize(
                    text=settings.anchor_script.strip(),
                    voice_id=synth_voice_id,
                    model_id=settings.model_id,
                    output_dir=output_dir,
                    speed=speed,
                    volume=max(0.0, min(2.0, volume)),
                )

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_synth())
            finally:
                loop.close()

            if result.ok and result.output_path:
                import shutil
                name = _P(result.output_path).name
                # Copy to fixed filename so pipeline always uses this exact file
                fixed_path = output_dir / "anchor.wav"
                shutil.copy2(str(result.output_path), str(fixed_path))
                self._generate_finished.emit(f"音频已生成: {name}", True)
            else:
                self._generate_finished.emit(f"生成失败: {result.message}", False)
        except Exception as e:
            self._generate_finished.emit(f"生成失败: {e}", False)

    def _on_generate_done(self, text: str, ok: bool):
        self._generate_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        if ok:
            if text.startswith("音频已生成"):
                # Audio synthesis result
                self._status_label.setText(text)
            else:
                # AI script generation result
                self._script_edit.setPlainText(text)
                self._gen_status_label.setText("生成完成")
        else:
            self._status_label.setText(text)
            self._gen_status_label.setText(f"生成失败：{text}")

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        self._script_edit.blockSignals(True)
        self._script_edit.setPlainText(settings.anchor_script)
        self._script_edit.blockSignals(False)


# ---------------------------------------------------------------------------
# CopilotSettingsDialog
# ---------------------------------------------------------------------------
