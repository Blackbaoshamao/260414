"""ObsRulesManagerDialog — extracted from ui.py."""
from __future__ import annotations
from obs_actions import ObsActionRule
from ui_dialogs.obsruledialog import ObsRuleDialog
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QSize
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFrame, QSpinBox, QDoubleSpinBox, QComboBox, QListWidgetItem)
import ui_theme as theme
from ui_components import MacButton


class ObsRulesManagerDialog(QDialog):
    def __init__(self, rules: list[ObsActionRule] | tuple[ObsActionRule, ...], parent=None):
        super().__init__(parent)
        self.setWindowTitle("动作规则库")
        self.setModal(True)
        self.resize(760, 520)
        self._rules = list(rules)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self._hint_label = QLabel("在这里统一管理关键词到 OBS 场景的映射规则。")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self._rules_list = QListWidget(self)
        self._rules_list.itemDoubleClicked.connect(lambda _: self._on_edit_rule())
        layout.addWidget(self._rules_list, stretch=1)

        rules_btn_row = QHBoxLayout()
        rules_btn_row.setContentsMargins(0, 0, 0, 0)
        rules_btn_row.setSpacing(8)
        self._add_rule_btn = MacButton("新增规则", variant="primary", parent=self)
        self._add_rule_btn.setMinimumWidth(96)
        self._add_rule_btn.clicked.connect(self._on_add_rule)
        rules_btn_row.addWidget(self._add_rule_btn)
        self._edit_rule_btn = MacButton("编辑规则", variant="secondary", parent=self)
        self._edit_rule_btn.setMinimumWidth(96)
        self._edit_rule_btn.clicked.connect(self._on_edit_rule)
        rules_btn_row.addWidget(self._edit_rule_btn)
        self._remove_rule_btn = MacButton("删除规则", variant="destructive", parent=self)
        self._remove_rule_btn.setMinimumWidth(96)
        self._remove_rule_btn.clicked.connect(self._on_remove_rule)
        rules_btn_row.addWidget(self._remove_rule_btn)
        rules_btn_row.addStretch(1)
        layout.addLayout(rules_btn_row)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._refresh_rules_list()
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        pass

        self.setStyleSheet(
            f"QDialog {{ background-color: {theme.CLR_BG}; color: {theme.CLR_TEXT_PRI}; }}"
        )
        self._hint_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")
        self._rules_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {theme.CLR_INPUT_BG};
                color: {theme.CLR_TEXT_PRI};
                border: 1px solid {theme.CLR_BORDER};
                border-radius: 8px;
                padding: 6px;
            }}
            QListWidget::item {{
                padding: 10px 12px;
                border-radius: 6px;
                margin: 3px 0;
            }}
            QListWidget::item:selected {{
                background-color: {theme.CLR_ACCENT};
                color: {theme.CLR_ACCENT_TEXT};
            }}
        """)
        # MacButtons (add/edit/remove) self-manage; only QDialogButtonBox
        # children still need explicit styling
        for btn in (self._add_rule_btn, self._edit_rule_btn, self._remove_rule_btn):
            btn.apply_theme_styles()
        fill = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.08)
        fill_hover = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.16)
        border_color = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_TEXT_PRI, 0.18)
        dbb_ss = f"""
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
        for button in self._buttons.buttons():
            button.setStyleSheet(dbb_ss)

    def _refresh_rules_list(self):
        self._rules_list.clear()
        for rule in self._rules:
            status = "启用" if rule.enabled else "停用"
            keywords = " / ".join(rule.keywords) or "未填写关键词"
            item = self._rules_list.addItem(
                f"{rule.display_name}  [{status}]\n"
                f"关键词: {keywords}\n"
                f"目标场景: {rule.target_scene or '未填写场景'}    冷却: {rule.cooldown_sec} 秒"
            )
            if item is None:
                item = self._rules_list.item(self._rules_list.count() - 1)
            item.setSizeHint(QSize(0, 74))

    def _selected_rule_index(self) -> int:
        return self._rules_list.currentRow()

    def _on_add_rule(self):
        dialog = ObsRuleDialog(parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self._rules.append(dialog.get_rule())
        self._refresh_rules_list()
        self._rules_list.setCurrentRow(len(self._rules) - 1)

    def _on_edit_rule(self):
        index = self._selected_rule_index()
        if index < 0 or index >= len(self._rules):
            QMessageBox.information(self, "未选择规则", "请先在列表中选择要编辑的动作规则。")
            return
        dialog = ObsRuleDialog(self._rules[index], parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self._rules[index] = dialog.get_rule()
        self._refresh_rules_list()
        self._rules_list.setCurrentRow(index)

    def _on_remove_rule(self):
        index = self._selected_rule_index()
        if index < 0 or index >= len(self._rules):
            QMessageBox.information(self, "未选择规则", "请先在列表中选择要删除的动作规则。")
            return
        self._rules.pop(index)
        self._refresh_rules_list()
        if self._rules:
            self._rules_list.setCurrentRow(min(index, len(self._rules) - 1))

    def get_rules(self) -> list[ObsActionRule]:
        return list(self._rules)



# ---------------------------------------------------------------------------
# ObsActionPage
# ---------------------------------------------------------------------------

