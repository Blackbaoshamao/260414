from __future__ import annotations

from dataclasses import dataclass
import os
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

CACHE_WALK_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".worktrees",
    "worktrees",
    "node_modules",
}

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


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _iter_pycache_dirs(root: Path):
    base = root.resolve()
    for current, dirnames, _filenames in os.walk(base):
        dirnames[:] = [name for name in dirnames if name not in CACHE_WALK_EXCLUDED_DIRS]
        current_path = Path(current)
        if current_path.name == "__pycache__":
            yield current_path
            dirnames[:] = []


def _clear_paths(root: Path, relative_paths: tuple[str, ...]) -> ClearResult:
    resolved = [_safe_child(root, item) for item in relative_paths]
    return _remove_existing(_dedupe_paths(resolved))


def clear_software_cache(root: str | Path | None = None) -> ClearResult:
    target_root = Path(root) if root is not None else app_dir()
    resolved = [_safe_child(target_root, item) for item in CACHE_RELATIVE_PATHS]
    resolved.extend(_iter_pycache_dirs(target_root))
    return _remove_existing(_dedupe_paths(resolved))


def clear_software_data(root: str | Path | None = None) -> ClearResult:
    return _clear_paths(Path(root) if root is not None else app_dir(), DATA_RELATIVE_PATHS)
