"""General Settings page."""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QMessageBox
from loguru import logger
from siui.components.page import SiPage
from siui.components.widgets import SiDenseHContainer, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.option_card import SiOptionCardLinear
from siui.components.combobox.combobox import SiComboBox

from ui_constants import _DATA_SOURCE_OPTIONS, DEFAULT_DATA_SOURCE, _DEFAULT_MESSAGE_FILTERS
from ui_settings import _load_settings, _save_settings
from ui import _make_back_button
from maintenance import clear_software_cache, clear_software_data


class GeneralSettingsPage(SiPage):
    back_requested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)

        container = SiTitledWidgetGroup(self)
        container.setSpacing(16)

        container.addWidget(_make_back_button(self, self.back_requested))

        container.addTitle("数据源")
        source_card = SiOptionCardLinear(self)
        source_card.setTitle("弹幕来源", "选择直播数据获取方式")
        source_card.load("ic_fluent_database_filled")
        self._source_combo = SiComboBox(self)
        self._source_combo.setFixedSize(200, 32)
        for text, value in _DATA_SOURCE_OPTIONS:
            self._source_combo.addOption(text, value=value)
        self._source_combo.menu().setShowIcon(False)
        self._source_combo.menu().setIndex(0)
        source_card.addWidget(self._source_combo)
        container.addWidget(source_card)

        container.addTitle("关于")
        about_card = SiOptionCardLinear(self)
        about_card.setTitle("Aiszr v0.5", "AI 数字人直播助手")
        about_card.load("ic_fluent_info_filled")
        container.addWidget(about_card)

        container.addTitle("操作")
        btn_area = SiDenseHContainer(self)
        btn_area.setFixedHeight(36)
        self._save_btn = SiPushButton(self)
        self._save_btn.resize(100, 28)
        self._save_btn.attachment().setText("保存设置")
        self._save_btn.clicked.connect(self._on_save)
        btn_area.addWidget(self._save_btn)
        self._reset_btn = SiPushButton(self)
        self._reset_btn.resize(100, 28)
        self._reset_btn.attachment().setText("恢复默认")
        self._reset_btn.clicked.connect(self._on_reset)
        btn_area.addWidget(self._reset_btn)
        self._clear_cache_btn = SiPushButton(self)
        self._clear_cache_btn.resize(110, 28)
        self._clear_cache_btn.attachment().setText("清除缓存")
        self._clear_cache_btn.clicked.connect(self._on_clear_cache)
        btn_area.addWidget(self._clear_cache_btn)
        self._clear_data_btn = SiPushButton(self)
        self._clear_data_btn.resize(130, 28)
        self._clear_data_btn.attachment().setText("清除软件数据")
        self._clear_data_btn.clicked.connect(self._on_clear_data)
        btn_area.addWidget(self._clear_data_btn)
        container.addWidget(btn_area)

        self.setAttachment(container)

    def _apply_theme_styles(self):
        pass

    def _on_save(self):
        data = _load_settings()
        data["data_source"] = self._source_combo.menu().value() or DEFAULT_DATA_SOURCE
        _save_settings(data)
        logger.info("Settings saved")

    def _on_reset(self):
        self._source_combo.menu().setIndex(0)
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
