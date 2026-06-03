"""DigitalHumanPage — extracted from ui.py."""
from __future__ import annotations
from app_paths import app_dir
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _build_input_field_stylesheet
from ui_theme import _hex_with_alpha
from ui_theme import _mix_hex_colors
from ui_theme import _placeholder_h
from ui_theme import apply_theme
from voice_models import VoiceSettings


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QTimer, QRect, QSize, QPropertyAnimation, QEasingCurve, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush, QIcon
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog,
    QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QComboBox, QScrollArea, QApplication, QApplication, QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog, QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox, QSizePolicy, QComboBox, QScrollArea)
from siui.core import SiColor, SiGlobal, GlobalFont, Si
from siui.gui import SiFont
from siui.components.page import SiPage
from siui.components.widgets import (SiDenseHContainer, SiDenseVContainer,
    SiLabel, SiLineEdit, SiPushButton, SiSvgLabel)
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.option_card import SiOptionCardLinear
from siui.components.combobox.combobox import SiComboBox
from siui.components.slider import SiSliderH
from siui.templates.application.components.dialog.modal import SiModalDialog
import ui_theme as theme
from loguru import logger
from ui_constants import _CARD_H
from ui_settings import _save_settings
from ui import _AddCard, _VideoThumbCard


class DigitalHumanPage(SiPage):
    back_requested = pyqtSignal()
    digital_human_start_requested = pyqtSignal(object)
    digital_human_stop_requested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)
        self._video_paths: list[str] = []
        self._selected_index: int = -1
        self._obs_host = "127.0.0.1"
        self._obs_port = 4455
        self._obs_password = ""

        container = SiTitledWidgetGroup(self)
        container.setSpacing(16)

        line_edit_ss = _build_input_field_stylesheet()

        # Back button
        back_area = SiDenseHContainer(self)
        back_area.setFixedHeight(32)
        back_btn = SiPushButton(self)
        back_btn.resize(80, 28)
        back_btn.attachment().setText("返回")
        back_btn.clicked.connect(self.back_requested.emit)
        back_area.addWidget(back_btn)
        container.addWidget(back_area)

        # Section: Script & Audio (unified streaming console)
        container.addTitle("带货话术")
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
        container.addWidget(self._product_edit)

        gen_row = QWidget(self)
        gen_layout = QHBoxLayout(gen_row)
        gen_layout.setContentsMargins(0, 2, 0, 2)
        gen_layout.setSpacing(8)
        self._generate_script_btn = QPushButton("AI 生成带货话术", self)
        self._generate_script_btn.setMinimumSize(160, 38)
        self._generate_script_btn.clicked.connect(self._on_generate_script)
        gen_layout.addWidget(self._generate_script_btn)
        gen_layout.addStretch(1)
        container.addWidget(gen_row)

        self._gen_status_label = QLabel("", self)
        self._gen_status_label.setWordWrap(True)
        self._gen_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")
        container.addWidget(self._gen_status_label)

        container.addWidget(_placeholder_h(8))
        sep1 = QFrame(self)
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet(f"color: {theme.CLR_BORDER}; border: none; background-color: {theme.CLR_BORDER}; max-height: 1px;")
        container.addWidget(sep1)
        container.addWidget(_placeholder_h(4))

        self._script_edit = QTextEdit(self)
        self._script_edit.setPlaceholderText("输入主播话术内容，或使用上方 AI 生成...")
        self._script_edit.setStyleSheet(text_ss)
        container.addWidget(self._script_edit)

        audio_btn_row = QWidget(self)
        audio_btn_layout = QHBoxLayout(audio_btn_row)
        audio_btn_layout.setContentsMargins(0, 2, 0, 2)
        audio_btn_layout.setSpacing(8)
        self._gen_audio_btn = QPushButton("生成音频", self)
        self._gen_audio_btn.setMinimumSize(100, 38)
        self._gen_audio_btn.clicked.connect(self._on_gen_audio)
        audio_btn_layout.addWidget(self._gen_audio_btn)
        self._preview_btn = QPushButton("▶ 试听", self)
        self._preview_btn.setMinimumSize(100, 38)
        self._preview_btn.clicked.connect(self._on_preview_audio)
        audio_btn_layout.addWidget(self._preview_btn)
        audio_btn_layout.addStretch(1)
        container.addWidget(audio_btn_row)

        self._audio_status_label = QLabel("", self)
        self._audio_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")
        container.addWidget(self._audio_status_label)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())

        # Section: Avatar gallery
        container.addTitle("主播形象（右键移除，左键选中）")
        self._gallery_inner = QWidget(self)
        self._gallery_inner.setFixedHeight(_CARD_H)
        self._gallery_inner.setStyleSheet("background: transparent;")
        self._gallery_inner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._gallery_layout = QHBoxLayout(self._gallery_inner)
        self._gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_layout.setSpacing(12)
        container.addWidget(self._gallery_inner)
        self._rebuild_gallery()

        # Section: Streaming control
        container.addTitle("推流控制")
        self._status_label = QLabel("空闲", self)
        self._status_label.setWordWrap(True)
        container.addWidget(self._status_label)

        btn_row = SiDenseHContainer(self)
        btn_row.setFixedHeight(40)
        self._start_btn = SiPushButton(self)
        self._start_btn.resize(120, 32)
        self._start_btn.attachment().setText("一键推流")
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)
        self._stop_btn = SiPushButton(self)
        self._stop_btn.resize(100, 32)
        self._stop_btn.attachment().setText("停止推流")
        self._stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self._stop_btn)
        container.addWidget(btn_row)

        # Save button
        container.addTitle("操作")
        save_area = SiDenseHContainer(self)
        save_area.setFixedHeight(36)
        self._save_btn = SiPushButton(self)
        self._save_btn.resize(100, 28)
        self._save_btn.attachment().setText("保存配置")
        self._save_btn.clicked.connect(self._on_save)
        save_area.addWidget(self._save_btn)
        container.addWidget(save_area)

        self.setAttachment(container)

    # -- Script & Audio --

    def _on_generate_script(self):
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
        self._generate_script_btn.setEnabled(False)
        import threading
        thread = threading.Thread(
            target=self._do_generate_script,
            args=(api_key, settings, product_text),
            daemon=True,
        )
        thread.start()

    def _do_generate_script(self, api_key: str, settings: dict, product_text: str):
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
            self._gen_status_label.setText("生成完成")
            self._script_edit.setPlainText(result)
        except Exception as e:
            self._gen_status_label.setText(f"生成失败：{e}")
        finally:
            self._generate_script_btn.setEnabled(True)

    def _on_gen_audio(self):
        script = self._script_edit.toPlainText().strip()
        if not script:
            self._audio_status_label.setText("请先输入主播话术")
            return
        self._voice_settings_state.anchor_script = script
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)

        from voice_manager import VOICE_DATA_DIR, synthesis_voice_id_for_role
        gen_dir = VOICE_DATA_DIR / "anchor" / "generated"
        if gen_dir.exists():
            for f in gen_dir.glob("*"):
                f.unlink(missing_ok=True)

        anchor = self._voice_settings_state.anchor
        if synthesis_voice_id_for_role(self._voice_settings_state, anchor) is None:
            self._audio_status_label.setText("请先在 AI 语音设置中完成主播音色克隆")
            return

        self._gen_audio_btn.setEnabled(False)
        self._audio_status_label.setText("正在生成音频...")
        import threading
        thread = threading.Thread(
            target=self._do_synthesize,
            args=(self._voice_settings_state,),
            daemon=True,
        )
        thread.start()

    def _do_synthesize(self, settings):
        try:
            from pathlib import Path as _P
            import asyncio
            import shutil
            from voice_manager import (
                DEFAULT_SPEED_RATIO,
                DEFAULT_VOLUME_RATIO,
                PROVIDER_TYPES,
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
                name = _P(result.output_path).name
                fixed_path = output_dir / "anchor.wav"
                shutil.copy2(str(result.output_path), str(fixed_path))
                self._audio_status_label.setText(f"音频已生成: {name}")
            else:
                self._audio_status_label.setText(f"生成失败: {result.message}")
        except Exception as e:
            self._audio_status_label.setText(f"生成失败: {e}")
        finally:
            self._gen_audio_btn.setEnabled(True)

    def _on_preview_audio(self):
        script = self._script_edit.toPlainText().strip()
        if not script:
            self._audio_status_label.setText("请先输入或生成主播话术")
            return
        self._preview_btn.setEnabled(False)
        self._audio_status_label.setText("正在合成试听...")
        import threading
        thread = threading.Thread(
            target=self._do_preview_synth,
            args=(script,),
            daemon=True,
        )
        thread.start()

    def _do_preview_synth(self, script: str):
        try:
            import asyncio
            from voice_manager import VoiceManager
            mgr = VoiceManager()
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    mgr.synthesize_and_play(script, 'anchor')
                )
            finally:
                loop.close()
            self._audio_status_label.setText("试听完成")
        except Exception as e:
            self._audio_status_label.setText(f"试听失败: {e}")
        finally:
            self._preview_btn.setEnabled(True)

    def load_voice_settings(self, value: object):
        if isinstance(value, dict):
            self._voice_settings_state = VoiceSettings.from_dict(value)
        else:
            self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())

    # -- Gallery management --

    def _rebuild_gallery(self):
        while self._gallery_layout.count():
            item = self._gallery_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, path in enumerate(self._video_paths):
            pixmap = self._load_thumbnail(path)
            card = _VideoThumbCard(i, path, pixmap, self._gallery_inner)
            card.clicked.connect(self._on_thumb_clicked)
            card.remove_requested.connect(self._on_thumb_remove)
            card.set_selected(i == self._selected_index)
            self._gallery_layout.addWidget(card)
        add_card = _AddCard(self._gallery_inner)
        add_card.clicked.connect(self._on_add_video)
        self._gallery_layout.addWidget(add_card)

    def _load_thumbnail(self, video_path: str) -> QPixmap | None:
        from pathlib import Path
        thumb_dir = app_dir() / "data" / "digital_human" / "thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / (Path(video_path).stem + ".jpg")
        if thumb_path.exists():
            return QPixmap(str(thumb_path))
        try:
            from ffmpeg_ops import _resolve_ffmpeg_path
            import subprocess
            ffmpeg = _resolve_ffmpeg_path()
            subprocess.run(
                [ffmpeg, "-y", "-i", video_path, "-ss", "0.5",
                 "-vframes", "1", "-q:v", "2", str(thumb_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if thumb_path.exists():
                return QPixmap(str(thumb_path))
        except Exception:
            pass
        return None

    def _on_thumb_clicked(self, index: int):
        self._selected_index = index
        for i in range(self._gallery_layout.count() - 1):
            item = self._gallery_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _VideoThumbCard):
                item.widget().set_selected(item.widget().index == index)

    def _on_thumb_remove(self, index: int):
        if index < 0 or index >= len(self._video_paths):
            return
        self._video_paths.pop(index)
        if self._selected_index >= len(self._video_paths):
            self._selected_index = len(self._video_paths) - 1
        self._rebuild_gallery()

    def _on_add_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择绿幕视频", "",
            "视频文件 (*.mp4 *.avi *.mov);;所有文件 (*)",
        )
        if not paths:
            return
        self._video_paths.extend(paths)
        if self._selected_index < 0:
            self._selected_index = 0
        self._rebuild_gallery()

    # -- Actions --

    def _on_start(self):
        config = self.get_config_dict()
        self.digital_human_start_requested.emit(config)

    def _on_stop(self):
        self.digital_human_stop_requested.emit()

    def _on_save(self):
        data = _load_settings()
        data["digital_human"] = {
            "video_paths": list(self._video_paths),
            "selected_index": self._selected_index,
            **self.get_config_dict(),
        }
        _save_settings(data)

    def get_config_dict(self) -> dict:
        return {
            "video_path": self._video_paths[self._selected_index] if 0 <= self._selected_index < len(self._video_paths) else "",
            "obs_scene": "",
            "obs_host": self._obs_host,
            "obs_port": self._obs_port,
            "obs_password": self._obs_password,
        }

    def set_obs_connection_settings(self, host: str, port: int, password: str):
        self._obs_host = host
        self._obs_port = port
        self._obs_password = password

    def update_status(self, payload: dict):
        message = payload.get("message", "")
        state = payload.get("state", "")
        text = message if message else state
        self._status_label.setText(text)

    def load_settings(self, value: object):
        self._selected_index = -1
        if isinstance(value, dict):
            paths = value.get("video_paths", [])
            if isinstance(paths, list):
                self._video_paths = [p for p in paths if isinstance(p, str)]
            single = value.get("video_path", "")
            if single and single not in self._video_paths:
                self._video_paths.append(single)
            sel = value.get("selected_index", 0)
            if self._video_paths:
                self._selected_index = max(0, min(sel, len(self._video_paths) - 1))
            self._rebuild_gallery()

    def _apply_theme_styles(self):
        accent_btn = f"""
            QPushButton {{
                background-color: {theme.CLR_ACCENT};
                color: {theme.CLR_ACCENT_TEXT};
                border: none;
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 15px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {theme.CLR_ACCENT_LIGHT}; }}
            QPushButton:pressed {{ background-color: {_mix_hex_colors(theme.CLR_ACCENT, "#000000", 0.12)}; }}
            QPushButton:disabled {{ background-color: {theme.CLR_BORDER}; color: {theme.CLR_TEXT_SEC}; }}
        """
        secondary_btn = f"""
            QPushButton {{
                background-color: {theme.CLR_BG_ELEVATED};
                color: {theme.CLR_TEXT_PRI};
                border: none;
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 15px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {theme.CLR_BG_CARD}; }}
            QPushButton:pressed {{ background-color: {_mix_hex_colors(theme.CLR_BG_CARD, "#000000", 0.15)}; padding-top: 9px; padding-bottom: 7px; padding-left: 20px; padding-right: 20px; }}
        """
        if hasattr(self, "_generate_script_btn"):
            self._generate_script_btn.setStyleSheet(accent_btn)
        if hasattr(self, "_gen_audio_btn"):
            self._gen_audio_btn.setStyleSheet(secondary_btn)
        if hasattr(self, "_preview_btn"):
            self._preview_btn.setStyleSheet(secondary_btn)

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
        if hasattr(self, "_product_edit"):
            self._product_edit.setStyleSheet(text_ss)
        if hasattr(self, "_script_edit"):
            self._script_edit.setStyleSheet(text_ss)
        if hasattr(self, "_gen_status_label"):
            self._gen_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")
        if hasattr(self, "_audio_status_label"):
            self._audio_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")
        if hasattr(self, "_status_label"):
            self._status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")

