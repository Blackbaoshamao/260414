"""General Settings page."""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from loguru import logger
from siui.components.page import SiPage
from siui.components.widgets import SiDenseHContainer, SiPushButton
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.option_card import SiOptionCardLinear
from siui.components.combobox.combobox import SiComboBox

from ui_constants import _DATA_SOURCE_OPTIONS, DEFAULT_DATA_SOURCE, _DEFAULT_MESSAGE_FILTERS
from ui_settings import _load_settings, _save_settings
from ui import _make_back_button


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
        about_card.setTitle("Aiszr v0.2.0", "AI 数字人直播助手，基于 PyQt-SiliconUI 构建")
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
