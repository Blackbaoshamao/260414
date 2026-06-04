# Settings Clear Data Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Settings-page actions for clearing regenerable software cache and fully resetting software data.

**Architecture:** Put filesystem deletion behind a small whitelist-based maintenance helper, then let `GeneralSettingsPage` show confirmation dialogs and call that helper. The helper must resolve paths under `app_dir()` and refuse to delete anything outside the application data root.

**Tech Stack:** Python 3.10+, PyQt5/PyQt-SiliconUI, pytest, pathlib/shutil/os.

---

### Task 1: Whitelist-Based Maintenance Helper

**Files:**
- Create: `Aiszr/maintenance.py`
- Test: `Aiszr/tests/test_maintenance.py`

- [ ] **Step 1: Write failing tests for cache and data cleanup**

```python
from pathlib import Path

from maintenance import clear_software_cache, clear_software_data


def test_clear_software_cache_deletes_only_regenerable_paths(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / "debug_payload.log").write_text("debug", encoding="utf-8")
    (tmp_path / "data" / "voice" / "anchor" / "generated").mkdir(parents=True)
    (tmp_path / "data" / "voice" / "anchor" / "generated" / "anchor.wav").write_bytes(b"wav")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    result = clear_software_cache(tmp_path)

    assert result.deleted_count >= 4
    assert not (tmp_path / "__pycache__").exists()
    assert not (tmp_path / ".pytest_cache").exists()
    assert not (tmp_path / "debug_payload.log").exists()
    assert not (tmp_path / "data" / "voice" / "anchor" / "generated").exists()
    assert (tmp_path / "settings.json").exists()
    assert (tmp_path / "auth.json").exists()


def test_clear_software_data_resets_user_data_but_not_source_like_files(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "voice").mkdir(parents=True)
    (tmp_path / "data" / "voice" / "sample.wav").write_bytes(b"wav")
    (tmp_path / "browser_data").mkdir()
    (tmp_path / "browser_data" / "state").write_text("x", encoding="utf-8")
    (tmp_path / "ui.py").write_text("source", encoding="utf-8")
    (tmp_path / ".env").write_text("secret", encoding="utf-8")

    result = clear_software_data(tmp_path)

    assert result.deleted_count >= 4
    assert not (tmp_path / "settings.json").exists()
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "browser_data").exists()
    assert (tmp_path / "ui.py").exists()
    assert (tmp_path / ".env").exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_maintenance.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'maintenance'`.

- [ ] **Step 3: Implement helper**

Create `Aiszr/maintenance.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from app_paths import app_dir


@dataclass(frozen=True)
class ClearResult:
    deleted_count: int
    deleted_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]


CACHE_RELATIVE_PATHS = (
    "__pycache__",
    ".pytest_cache",
    "debug_payload.log",
    "crash_trace.log",
    "startup_error.log",
    "replay_log.jsonl",
    "data/replay_log.jsonl",
    "data/log",
    "data/heygem_debug",
    "data/heygem_debug_quality",
    "data/digital_human/thumbs",
    "data/voice/anchor/generated",
    "data/voice/copilot/generated",
)

DATA_RELATIVE_PATHS = (
    "settings.json",
    "auth.json",
    "data",
    "browser_data",
    "wechat_browser_data",
    "debug_payload.log",
    "crash_trace.log",
    "startup_error.log",
    "replay_log.jsonl",
)


def _safe_child(root: Path, relative_path: str) -> Path:
    base = root.resolve()
    target = (base / relative_path).resolve()
    if target == base or base not in target.parents:
        raise ValueError(f"Refusing to clear unsafe path: {target}")
    return target


def _remove_existing(paths: list[Path]) -> ClearResult:
    deleted: list[str] = []
    skipped: list[str] = []
    for path in paths:
        if not path.exists():
            skipped.append(str(path))
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(str(path))
    return ClearResult(len(deleted), tuple(deleted), tuple(skipped))


def _clear_paths(root: Path, relative_paths: tuple[str, ...]) -> ClearResult:
    resolved = [_safe_child(root, item) for item in relative_paths]
    return _remove_existing(resolved)


def clear_software_cache(root: str | Path | None = None) -> ClearResult:
    return _clear_paths(Path(root) if root is not None else app_dir(), CACHE_RELATIVE_PATHS)


def clear_software_data(root: str | Path | None = None) -> ClearResult:
    return _clear_paths(Path(root) if root is not None else app_dir(), DATA_RELATIVE_PATHS)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_maintenance.py -q`

Expected: PASS.

### Task 2: Settings Page Buttons and Confirmation Dialogs

**Files:**
- Modify: `Aiszr/ui_pages/general_settings.py`
- Test: `Aiszr/tests/test_general_settings_maintenance.py`

- [ ] **Step 1: Write AST tests for settings page integration**

```python
import ast
from pathlib import Path


def _source():
    return (Path(__file__).resolve().parents[1] / "ui_pages" / "general_settings.py").read_text(encoding="utf-8")


def test_settings_page_has_clear_cache_and_data_buttons():
    source = _source()

    assert "清除缓存" in source
    assert "清除软件数据" in source
    assert "clear_software_cache" in source
    assert "clear_software_data" in source


def test_clear_data_handler_requires_confirmation():
    tree = ast.parse(_source())
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_clear_data"
    )
    handler_source = ast.unparse(handler)

    assert "QMessageBox.question" in handler_source
    assert "不可恢复" in handler_source
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_general_settings_maintenance.py -q`

Expected: FAIL because buttons and handlers do not exist.

- [ ] **Step 3: Implement UI wiring**

Modify `GeneralSettingsPage` imports:

```python
from PyQt5.QtWidgets import QMessageBox
from maintenance import clear_software_cache, clear_software_data
```

Add two buttons in the `操作` button row after `恢复默认`:

```python
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
```

Add handlers:

```python
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
    result = clear_software_cache()
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
    result = clear_software_data()
    QMessageBox.information(self, "清除软件数据", f"已清除 {result.deleted_count} 项软件数据。请重启软件。")
    logger.info("Software data cleared: {}", result.deleted_paths)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_general_settings_maintenance.py -q`

Expected: PASS.

### Task 3: Verification

**Files:**
- Modify: none unless tests reveal issues.

- [ ] **Step 1: Run maintenance and settings tests**

Run: `python -m pytest tests/test_maintenance.py tests/test_general_settings_maintenance.py -q`

Expected: all pass.

- [ ] **Step 2: Run current voice-related regression tests**

Run: `python -m pytest tests/test_voice_manager_cache.py tests/test_voice_provider_settings.py -q`

Expected: all pass.

- [ ] **Step 3: Compile touched files**

Run: `python -m py_compile maintenance.py ui_pages\general_settings.py tests\test_maintenance.py tests\test_general_settings_maintenance.py`

Expected: exit code 0.

- [ ] **Step 4: Run diff whitespace check**

Run: `git diff --check -- Aiszr/maintenance.py Aiszr/ui_pages/general_settings.py Aiszr/tests/test_maintenance.py Aiszr/tests/test_general_settings_maintenance.py`

Expected: exit code 0.
