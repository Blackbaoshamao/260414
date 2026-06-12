"""General Settings page."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMessageBox, QWidget, QVBoxLayout, QHBoxLayout,
)
from loguru import logger
from qfluentwidgets import (
    SettingCard, ComboBox, PushButton, PrimaryPushButton,
    FluentIcon, StrongBodyLabel,
)

from ui_constants import _DATA_SOURCE_OPTIONS, DEFAULT_DATA_SOURCE, _DEFAULT_MESSAGE_FILTERS
from ui_settings import _load_settings, _save_settings
from fluent_page import FluentPage
from maintenance import clear_software_cache, clear_software_data
import ui_theme as theme
from ui_theme import patch_setting_card_padding


class GeneralSettingsPage(FluentPage):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(16)

        # Data source section
        source_label = StrongBodyLabel("数据源")
        source_label.setStyleSheet(f"color: {theme.CLR_TEXT_PRI};")
        layout.addWidget(source_label)

        source_card = SettingCard(FluentIcon.DOCUMENT, "弹幕来源", "选择直播数据获取方式")
        self._source_combo = ComboBox()
        self._source_combo.setFixedWidth(200)
        for text, value in _DATA_SOURCE_OPTIONS:
            self._source_combo.addItem(text, userData=value)
        source_card.hBoxLayout.addWidget(self._source_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(source_card)
        layout.addWidget(source_card)

        # About section
        about_label = StrongBodyLabel("关于")
        about_label.setStyleSheet(f"color: {theme.CLR_TEXT_PRI};")
        layout.addWidget(about_label)

        about_card = SettingCard(FluentIcon.INFO, "Aiszr v0.55", "AI 数字人直播助手")
        patch_setting_card_padding(about_card)
        layout.addWidget(about_card)

        # Actions section
        actions_label = StrongBodyLabel("操作")
        actions_label.setStyleSheet(f"color: {theme.CLR_TEXT_PRI};")
        layout.addWidget(actions_label)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        self._save_btn = PrimaryPushButton("保存设置")
        self._save_btn.setFixedWidth(100)
        self._save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self._save_btn)

        self._reset_btn = PushButton("恢复默认")
        self._reset_btn.setFixedWidth(100)
        self._reset_btn.clicked.connect(self._on_reset)
        btn_layout.addWidget(self._reset_btn)

        self._clear_cache_btn = PushButton("清除缓存")
        self._clear_cache_btn.setFixedWidth(110)
        self._clear_cache_btn.clicked.connect(self._on_clear_cache)
        btn_layout.addWidget(self._clear_cache_btn)

        self._clear_data_btn = PushButton("清除软件数据")
        self._clear_data_btn.setFixedWidth(130)
        self._clear_data_btn.clicked.connect(self._on_clear_data)
        btn_layout.addWidget(self._clear_data_btn)

        btn_layout.addStretch(1)
        layout.addWidget(btn_row)

        layout.addStretch(1)
        self.setAttachment(container)

    def _apply_theme_styles(self):
        pass

    def _on_save(self):
        data = _load_settings()
        data["data_source"] = self._source_combo.currentData() or DEFAULT_DATA_SOURCE
        _save_settings(data)
        logger.info("Settings saved")

    def _on_reset(self):
        self._source_combo.setCurrentIndex(0)
        data = _load_settings()
        data["data_source"] = DEFAULT_DATA_SOURCE
        data["message_filters"] = dict(_DEFAULT_MESSAGE_FILTERS)
        _save_settings(data)
        logger.info("Settings reset to defaults")

    def _on_clear_cache(self):
        answer = QMessageBox.question(
            self,
            "清除缓存",
            "确认清除可重新生成的软件缓存？不会删除设置、授权、音色记录或主播形象原始数据。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = clear_software_cache()
        except Exception as exc:
            logger.exception("Failed to clear software cache")
            QMessageBox.warning(self, "清除缓存", f"清除缓存失败：{exc}")
            return
        QMessageBox.information(self, "清除缓存", f"已清除 {result.deleted_count} 项缓存。")
        logger.info("Software cache cleared: {}", result.deleted_paths)

    def _on_clear_data(self):
        answer = QMessageBox.question(
            self,
            "清除软件数据",
            "确认清除软件数据？此操作不可恢复，将删除设置、授权、声音/数字人数据和浏览器会话。建议完成后重启软件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = clear_software_data()
        except Exception as exc:
            logger.exception("Failed to clear software data")
            QMessageBox.warning(self, "清除软件数据", f"清除软件数据失败：{exc}")
            return
        QMessageBox.information(self, "清除软件数据", f"已清除 {result.deleted_count} 项软件数据。请重启软件。")
        logger.info("Software data cleared: {}", result.deleted_paths)
