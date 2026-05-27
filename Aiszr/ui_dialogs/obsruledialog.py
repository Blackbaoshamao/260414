"""ObsRuleDialog — extracted from ui.py."""
from __future__ import annotations
from obs_actions import ObsActionRule
from ui_theme import _build_input_field_stylesheet
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFrame, QSpinBox, QDoubleSpinBox, QComboBox, QListWidgetItem)
from siui.core import SiGlobal
from siui.components.widgets import SiPushButton, SiDenseHContainer, SiLineEdit, SiLabel
from siui.components.combobox.combobox import SiComboBox
from siui.components.option_card import SiOptionCardLinear
import ui_theme as theme
from ui_components import MacButton, MacLineEdit, MacSpinBox


class ObsRuleDialog(QDialog):
    def __init__(self, rule: ObsActionRule | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("动作规则")
        self.setModal(True)
        self.resize(430, 240)

        rule = rule or ObsActionRule()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setContentsMargins(12, 12, 12, 0)
        form.setSpacing(12)

        self._enabled_check = QCheckBox("启用规则", self)
        self._enabled_check.setChecked(rule.enabled)
        form.addRow("", self._enabled_check)

        self._name_input = MacLineEdit(placeholder="例如：售后动作")
        self._name_input.setText(rule.name)
        form.addRow("规则名", self._name_input)

        self._keywords_input = MacLineEdit(placeholder="多个关键词用逗号分隔")
        self._keywords_input.setText("，".join(rule.keywords))
        form.addRow("关键词", self._keywords_input)

        self._scene_input = MacLineEdit(placeholder="OBS 场景名")
        self._scene_input.setText(rule.target_scene)
        form.addRow("目标场景", self._scene_input)

        self._cooldown_spin = MacSpinBox()
        self._cooldown_spin.setRange(0, 3600)
        self._cooldown_spin.setSuffix(" 秒")
        self._cooldown_spin.setValue(rule.cooldown_sec or 60)
        form.addRow("单动作冷却", self._cooldown_spin)

        layout.addLayout(form)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self._accept_if_valid)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._apply_theme_styles()

    def _apply_theme_styles(self):
        try:
            SiGlobal.siui.reloadStyleSheetRecursively(self)
        except Exception:
            pass

        self.setStyleSheet(
            f"QDialog {{ background-color: {theme.CLR_BG}; color: {theme.CLR_TEXT_PRI}; }}"
        )
        # Mac* inputs self-manage styles
        for w in (self._name_input, self._keywords_input,
                  self._scene_input, self._cooldown_spin):
            w.apply_theme_styles()
        # QDialogButtonBox internal buttons: wrap with Mac look via stylesheet
        button_ss = self._dialog_button_stylesheet()
        for button in self._buttons.buttons():
            button.setStyleSheet(button_ss)

    def _dialog_button_stylesheet(self) -> str:
        """Mac-style stylesheet for QDialogButtonBox children (cannot replace
        them with MacButton because QDialogButtonBox owns the QPushButton
        instances internally)."""
        fill = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.08)
        fill_hover = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.16)
        border_color = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_TEXT_PRI, 0.18)
        return f"""
            QPushButton {{
                background-color: {fill};
                color: {theme.CLR_TEXT_PRI};
                border: 1px solid {border_color};
                border-radius: {theme.RADIUS_MD}px;
                padding: 6px 14px;
                font-weight: 500;
                min-width: 72px;
            }}
            QPushButton:hover {{
                background-color: {fill_hover};
                border-color: {theme.CLR_ACCENT};
            }}
            QPushButton:default {{
                background-color: {theme.CLR_ACCENT};
                color: {theme.CLR_ACCENT_TEXT};
                border-color: {theme.CLR_ACCENT};
            }}
            QPushButton:default:hover {{
                background-color: {theme.CLR_ACCENT_LIGHT};
                border-color: {theme.CLR_ACCENT_LIGHT};
            }}
        """

    def _accept_if_valid(self):
        keywords = self._parse_keywords(self._keywords_input.text())
        scene_name = self._scene_input.text().strip()
        if not keywords:
            QMessageBox.warning(self, "规则无效", "请至少填写一个关键词。")
            return
        if not scene_name:
            QMessageBox.warning(self, "规则无效", "请填写 OBS 目标场景名。")
            return
        self.accept()

    @staticmethod
    def _parse_keywords(raw_text: str) -> tuple[str, ...]:
        return tuple(
            keyword.strip()
            for keyword in raw_text.replace("，", ",").split(",")
            if keyword.strip()
        )

    def get_rule(self) -> ObsActionRule:
        keywords = self._parse_keywords(self._keywords_input.text())
        scene_name = self._scene_input.text().strip()
        name = self._name_input.text().strip() or scene_name or (keywords[0] if keywords else "")
        return ObsActionRule(
            enabled=self._enabled_check.isChecked(),
            name=name,
            keywords=keywords,
            target_scene=scene_name,
            cooldown_sec=self._cooldown_spin.value(),
        )


# ---------------------------------------------------------------------------
# ObsRulesManagerDialog
# ---------------------------------------------------------------------------

