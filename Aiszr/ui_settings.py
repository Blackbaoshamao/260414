"""Settings persistence — extracted from ui.py."""

import json
import os
from loguru import logger
from app_paths import app_dir


SETTINGS_FILE = str(app_dir() / "settings.json")

_settings_cache = None
_settings_mtime = 0

def _load_settings(force: bool = False) -> dict:
    global _settings_cache, _settings_mtime
    if not force and _settings_cache is not None:
        try:
            mtime = os.path.getmtime(SETTINGS_FILE)
            if mtime == _settings_mtime:
                return dict(_settings_cache)
        except OSError:
            pass
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                _settings_cache = json.load(f)
            try:
                _settings_mtime = os.path.getmtime(SETTINGS_FILE)
            except OSError:
                pass
            return dict(_settings_cache)
        except Exception:
            pass
    return {}


def _save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Save settings failed: {}", e)

