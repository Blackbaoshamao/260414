"""Centralized path resolution for development and PyInstaller bundles."""

import sys
import os
from pathlib import Path


def app_dir() -> Path:
    """Return the directory where user data files live.

    - Development: the directory containing this source file.
    - PyInstaller --onefile: the directory of the .exe.
    - PyInstaller --onedir: the directory of the .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
