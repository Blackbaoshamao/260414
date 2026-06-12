"""ObsActionPage — OBS 联动设置页（Sonoma 风重做）。"""
from __future__ import annotations
from obs_actions import DEFAULT_OBS_ACTION_SETTINGS
from obs_actions import ObsActionRule
from obs_actions import ObsActionSettings
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import SiSwitch
from ui_theme import _build_input_field_stylesheet
from ui_theme import _install_secret_reveal_action
from ui_theme import apply_theme
from ui_theme import patch_setting_card_padding


from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QDialog, QSpinBox, QSizePolicy,
)
from fluent_page import FluentPage
from qfluentwidgets import SettingCard, PushButton, PrimaryPushButton, FluentIcon
import ui_theme as theme
from ui_components import MacCard, MacButton, MacLineEdit, MacSpinBox
from ui_constants import _CARD_H, _CARD_W
from ui_settings import _save_settings
from loguru import logger


class ObsActionPage(FluentPage):
    obs_settings_changed = pyqtSignal(object)
    obs_status_check_requested = pyqtSignal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)
        self._obs_rules: list[ObsActionRule] = list(DEFAULT_OBS_ACTION_SETTINGS.rules)
        self._suspend_status_requests = False

        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 0, 8, 0)
        container_layout.setSpacing(8)

        # ── Enable card ──────────────────────────────────
        _title = QLabel("OBS 动作联动", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        self._obs_enable_panel = MacCard(self)
        self._obs_enable_panel.setMinimumHeight(90)
        enable_top_row = QHBoxLayout()
        enable_top_row.setContentsMargins(0, 0, 0, 0)
        enable_top_row.setSpacing(theme.SPACING_SM)

        enable_text_col = QVBoxLayout()
        enable_text_col.setContentsMargins(0, 0, 0, 0)
        enable_text_col.setSpacing(2)
        self._obs_enable_title_label = QLabel("启用联动")
        self._obs_enable_title_label.setFont(theme.FONT_BODY_EMPH)
        enable_text_col.addWidget(self._obs_enable_title_label)
        self._obs_enable_desc_label = QLabel("监听聊天关键词并切换到对应的 OBS 动作场景")
        self._obs_enable_desc_label.setWordWrap(True)
        self._obs_enable_desc_label.setFont(theme.FONT_CAPTION)
        enable_text_col.addWidget(self._obs_enable_desc_label)
        enable_top_row.addLayout(enable_text_col, stretch=1)

        enable_right = QWidget()
        enable_layout = QHBoxLayout(enable_right)
        enable_layout.setContentsMargins(0, 0, 0, 0)
        enable_layout.setSpacing(theme.SPACING_SM)
        self._obs_enabled_switch = SiSwitch(enable_right)
        self._obs_enabled_switch.setChecked(DEFAULT_OBS_ACTION_SETTINGS.enabled)
        self._obs_enabled_switch.toggled.connect(self._on_obs_enabled_toggled)
        enable_layout.addWidget(self._obs_enabled_switch)
        self._obs_runtime_status_label = QLabel("未检测")
        self._obs_runtime_status_label.setMinimumWidth(92)
        self._obs_runtime_status_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self._obs_runtime_status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        enable_layout.addWidget(self._obs_runtime_status_label)
        enable_top_row.addWidget(enable_right, stretch=0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        self._obs_enable_panel.body().addLayout(enable_top_row)
        container_layout.addWidget(self._obs_enable_panel)

        # ── Runtime status card ──────────────────────────
        self._obs_runtime_panel = MacCard(self)
        self._obs_runtime_panel.setMinimumHeight(112)
        self._obs_runtime_detail_label = QLabel("点击“立即检测”检查 OBS 连接和场景配置。")
        self._obs_runtime_detail_label.setWordWrap(True)
        self._obs_runtime_panel.body().addWidget(self._obs_runtime_detail_label)
        runtime_btn_row = QHBoxLayout()
        runtime_btn_row.setContentsMargins(0, 0, 0, 0)
        runtime_btn_row.addStretch(1)
        self._obs_test_btn = MacButton("立即检测", variant="secondary")
        self._obs_test_btn.setMinimumSize(120, 34)
        self._obs_test_btn.clicked.connect(self._request_obs_status_check)
        runtime_btn_row.addWidget(self._obs_test_btn)
        self._obs_runtime_panel.body().addLayout(runtime_btn_row)
        container_layout.addWidget(self._obs_runtime_panel)

        # ── Endpoint card (host + port) ──────────────────
        self._obs_endpoint_panel = MacCard(self)
        self._obs_endpoint_panel.setMinimumHeight(118)
        self._obs_endpoint_title_label = QLabel("OBS 地址")
        self._obs_endpoint_title_label.setFont(theme.FONT_BODY_EMPH)
        self._obs_endpoint_panel.body().addWidget(self._obs_endpoint_title_label)
        self._obs_endpoint_desc_label = QLabel("填写 OBS WebSocket 地址和端口")
        self._obs_endpoint_desc_label.setWordWrap(True)
        self._obs_endpoint_desc_label.setFont(theme.FONT_CAPTION)
        self._obs_endpoint_panel.body().addWidget(self._obs_endpoint_desc_label)
        endpoint_row = QHBoxLayout()
        endpoint_row.setContentsMargins(0, 0, 0, 0)
        endpoint_row.setSpacing(theme.SPACING_SM)
        self._obs_host_input = MacLineEdit()
        self._obs_host_input.setFixedSize(180, 32)
        self._obs_host_input.setText(DEFAULT_OBS_ACTION_SETTINGS.host)
        endpoint_row.addWidget(self._obs_host_input)
        self._obs_port_spin = MacSpinBox()
        self._obs_port_spin.setRange(1, 65535)
        self._obs_port_spin.setFixedSize(100, 32)
        self._obs_port_spin.setValue(DEFAULT_OBS_ACTION_SETTINGS.port)
        endpoint_row.addWidget(self._obs_port_spin)
        endpoint_row.addStretch(1)
        self._obs_endpoint_panel.body().addLayout(endpoint_row)
        container_layout.addWidget(self._obs_endpoint_panel)

        # ── qfluentwidgets SettingCard sub-settings ──────
        obs_password_card = SettingCard(FluentIcon.FINGERPRINT, "OBS 密码", "如果开启了认证，请填写 WebSocket 密码", parent=self)
        self._obs_password_input = MacLineEdit(secret=True)
        self._obs_password_input.setFixedSize(220, 32)
        self._obs_password_input.setEchoMode(QLineEdit.Password)
        obs_password_card.hBoxLayout.addWidget(self._obs_password_input, 0, Qt.AlignRight)
        patch_setting_card_padding(obs_password_card)
        container_layout.addWidget(obs_password_card)

        obs_main_scene_card = SettingCard(FluentIcon.HOME, "主场景", "动作结束后自动切回这个主场景", parent=self)
        self._obs_main_scene_input = MacLineEdit()
        self._obs_main_scene_input.setFixedSize(220, 32)
        obs_main_scene_card.hBoxLayout.addWidget(self._obs_main_scene_input, 0, Qt.AlignRight)
        patch_setting_card_padding(obs_main_scene_card)
        container_layout.addWidget(obs_main_scene_card)

        obs_ignore_card = SettingCard(FluentIcon.SYNC, "播放中忽略新触发", "开启后，动作播放期间不会再响应新的关键词", parent=self)
        self._obs_ignore_switch = SiSwitch(self)
        self._obs_ignore_switch.setChecked(DEFAULT_OBS_ACTION_SETTINGS.ignore_during_playback)
        obs_ignore_card.hBoxLayout.addWidget(self._obs_ignore_switch, 0, Qt.AlignRight)
        patch_setting_card_padding(obs_ignore_card)
        container_layout.addWidget(obs_ignore_card)

        global_cd_card = SettingCard(FluentIcon.STOP_WATCH, "全局冷却", "动作播完后，整套动作系统暂停触发的时间", parent=self)
        self._obs_global_cooldown_spin = MacSpinBox()
        self._obs_global_cooldown_spin.setRange(0, 3600)
        self._obs_global_cooldown_spin.setSuffix(" 秒")
        self._obs_global_cooldown_spin.setFixedSize(110, 32)
        self._obs_global_cooldown_spin.setValue(DEFAULT_OBS_ACTION_SETTINGS.global_cooldown_sec)
        global_cd_card.hBoxLayout.addWidget(self._obs_global_cooldown_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(global_cd_card)
        container_layout.addWidget(global_cd_card)

        match_window_card = SettingCard(FluentIcon.APPLICATION, "统计窗口", "在这个时间窗口里累计关键词命中次数", parent=self)
        self._obs_match_window_spin = MacSpinBox()
        self._obs_match_window_spin.setRange(1, 3600)
        self._obs_match_window_spin.setSuffix(" 秒")
        self._obs_match_window_spin.setFixedSize(110, 32)
        self._obs_match_window_spin.setValue(DEFAULT_OBS_ACTION_SETTINGS.match_window_sec)
        match_window_card.hBoxLayout.addWidget(self._obs_match_window_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(match_window_card)
        container_layout.addWidget(match_window_card)

        min_hits_card = SettingCard(FluentIcon.INFO, "最少命中次数", "达到这个命中次数后才真正触发动作", parent=self)
        self._obs_min_hits_spin = MacSpinBox()
        self._obs_min_hits_spin.setRange(1, 20)
        self._obs_min_hits_spin.setFixedSize(90, 32)
        self._obs_min_hits_spin.setValue(DEFAULT_OBS_ACTION_SETTINGS.min_hits)
        min_hits_card.hBoxLayout.addWidget(self._obs_min_hits_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(min_hits_card)
        container_layout.addWidget(min_hits_card)

        # ── Rules entry card ─────────────────────────────
        _title = QLabel("动作规则库", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        self._rules_entry_panel = MacCard(self)
        self._rules_entry_panel.setMinimumHeight(128)
        self._rules_summary_label = QLabel()
        self._rules_summary_label.setWordWrap(True)
        self._rules_entry_panel.body().addWidget(self._rules_summary_label)
        rules_button_row = QHBoxLayout()
        rules_button_row.setContentsMargins(0, 0, 0, 0)
        rules_button_row.addStretch(1)
        self._manage_rules_btn = MacButton("打开规则库", variant="secondary")
        self._manage_rules_btn.setMinimumSize(132, 34)
        self._manage_rules_btn.clicked.connect(self._on_manage_rules)
        rules_button_row.addWidget(self._manage_rules_btn)
        self._rules_entry_panel.body().addLayout(rules_button_row)
        container_layout.addWidget(self._rules_entry_panel)

        # ── Save / Reset ─────────────────────────────────
        _title = QLabel("操作", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        btn_area = QWidget(self)
        btn_layout = QHBoxLayout(btn_area)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        btn_area.setFixedHeight(36)
        self._save_btn = PrimaryPushButton("保存配置", parent=self)
        self._save_btn.setFixedSize(100, 28)
        self._save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self._save_btn)
        self._reset_btn = PushButton("恢复默认", parent=self)
        self._reset_btn.setFixedSize(100, 28)
        self._reset_btn.clicked.connect(self._on_reset)
        btn_layout.addWidget(self._reset_btn)
        btn_layout.addStretch(1)
        container_layout.addWidget(btn_area)

        self.setAttachment(container)
        self._refresh_rules_summary()

    def _apply_theme_styles(self):
        # Mac* widgets refresh themselves
        for w in self.findChildren(QWidget):
            fn = getattr(w, "apply_theme_styles", None)
            if callable(fn):
                fn()
        # _install_secret_reveal_action re-applies the eye icon (color depends on theme)
        _install_secret_reveal_action(self._obs_password_input)
        # Clear inline label colors so they inherit the global QLabel rule
        for lbl in (
            self._obs_enable_desc_label, self._obs_runtime_detail_label,
            self._obs_endpoint_desc_label, self._rules_summary_label,
        ):
            lbl.setStyleSheet("")
        # Title labels stay at CLR_TEXT_PRI weight 600
        for lbl in (self._obs_enable_title_label, self._obs_endpoint_title_label):
            lbl.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;"
            )

    def load_obs_action_settings(self, value: object):
        settings = ObsActionSettings.from_dict(value)
        self._obs_enabled_switch.setChecked(settings.enabled)
        self._obs_host_input.setText(settings.host)
        self._obs_port_spin.setValue(settings.port)
        self._obs_password_input.setText(settings.password)
        self._obs_main_scene_input.setText(settings.main_scene)
        self._obs_ignore_switch.setChecked(settings.ignore_during_playback)
        self._obs_global_cooldown_spin.setValue(settings.global_cooldown_sec)
        self._obs_match_window_spin.setValue(settings.match_window_sec)
        self._obs_min_hits_spin.setValue(settings.min_hits)
        self._obs_rules = list(settings.rules)
        self._refresh_rules_summary()

    def get_obs_action_settings(self) -> ObsActionSettings:
        return ObsActionSettings(
            enabled=self._obs_enabled_switch.isChecked(),
            host=self._obs_host_input.text().strip() or DEFAULT_OBS_ACTION_SETTINGS.host,
            port=self._obs_port_spin.value(),
            password=self._obs_password_input.text(),
            main_scene=self._obs_main_scene_input.text().strip(),
            ignore_during_playback=self._obs_ignore_switch.isChecked(),
            global_cooldown_sec=self._obs_global_cooldown_spin.value(),
            match_window_sec=self._obs_match_window_spin.value(),
            min_hits=self._obs_min_hits_spin.value(),
            rules=tuple(self._obs_rules),
        )

    def _refresh_rules_summary(self):
        if not self._obs_rules:
            self._rules_summary_label.setText("当前还没有动作规则。点击下方按钮后，可以在独立面板里新增和管理规则。")
            return

        preview_lines = [f"已配置 {len(self._obs_rules)} 条动作规则。"]
        for rule in self._obs_rules[:3]:
            preview_lines.append(f"• {rule.display_name} -> {rule.target_scene}")
        if len(self._obs_rules) > 3:
            preview_lines.append(f"• 其余 {len(self._obs_rules) - 3} 条规则可在规则库里查看")
        self._rules_summary_label.setText("\n".join(preview_lines))

    def _on_manage_rules(self):
        from ui_dialogs.obsrulesmanagerdialog import ObsRulesManagerDialog
        dialog = ObsRulesManagerDialog(self._obs_rules, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self._obs_rules = dialog.get_rules()
        self._refresh_rules_summary()

    def _on_save(self):
        data = _load_settings()
        obs_settings = self.get_obs_action_settings()
        data["obs_actions"] = obs_settings.to_dict()
        _save_settings(data)
        self.obs_settings_changed.emit(obs_settings.to_dict())
        logger.info("OBS settings saved")

    def _on_reset(self):
        self.load_obs_action_settings(DEFAULT_OBS_ACTION_SETTINGS.to_dict())
        data = _load_settings()
        data["obs_actions"] = DEFAULT_OBS_ACTION_SETTINGS.to_dict()
        _save_settings(data)
        self.obs_settings_changed.emit(DEFAULT_OBS_ACTION_SETTINGS.to_dict())
        logger.info("OBS settings reset to defaults")

    def _on_obs_enabled_toggled(self, checked: bool):
        if self._suspend_status_requests:
            return
        if not checked:
            self.update_obs_runtime_status(
                {
                    "state": "disabled",
                    "connected": False,
                    "short_text": "未启用",
                    "message": "OBS 联动未启用",
                }
            )
            return
        self._request_obs_status_check()

    def _request_obs_status_check(self):
        if self._suspend_status_requests:
            return
        self.update_obs_runtime_status(
            {
                "state": "checking",
                "connected": False,
                "short_text": "检测中",
                "message": "正在检查 OBS 连接状态...",
            }
        )
        self.obs_status_check_requested.emit(self.get_obs_action_settings().to_dict())

    def update_obs_runtime_status(self, payload: object):
        if not isinstance(payload, dict):
            return

        state = str(payload.get("state", "")).strip() or "unknown"
        short_text = str(payload.get("short_text", "")).strip() or "未检测"
        message = str(payload.get("message", "")).strip() or "OBS 状态未知"

        if state == "connected":
            color = theme.CLR_GREEN
        elif state == "warning":
            color = theme.CLR_YELLOW
        elif state in {"disconnected", "error"}:
            color = theme.CLR_RED
        elif state in {"checking", "discovering"}:
            color = theme.CLR_YELLOW
        else:
            color = theme.CLR_TEXT_TERT

        self._obs_runtime_status_label.setText(f'<span style="color:{color}">●</span> {short_text}')
        self._obs_runtime_status_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
        )
        self._obs_runtime_detail_label.setText(message)

    def set_discovered_host(self, host: str):
        self._obs_host_input.setText(host)

# ---------------------------------------------------------------------------
# DigitalHumanPage placeholder (kept for back-compat — ui.py no longer instantiates)
# ---------------------------------------------------------------------------

_CARD_W, _CARD_H = 140, 200
