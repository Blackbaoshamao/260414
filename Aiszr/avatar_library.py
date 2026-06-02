"""Local avatar asset library for LiveTalking-backed digital humans."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from app_paths import app_dir


LIBRARY_VERSION = 1
DEFAULT_QUALITY = "720p"
AVATAR_FPS = 25
MAX_AVATAR_RECORDS = 6
STATUS_IMPORTED = "IMPORTED"
STATUS_NORMALIZING = "NORMALIZING"
STATUS_CHECKING = "CHECKING"
STATUS_PROCESSING_GPU = "PROCESSING_GPU"
STATUS_PROCESSING_CPU = "PROCESSING_CPU"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"


def data_root() -> Path:
    return app_dir() / "data" / "digital_human"


def library_path() -> Path:
    return data_root() / "avatar_library.json"


def assets_dir() -> Path:
    return data_root() / "assets"


def normalized_dir() -> Path:
    return data_root() / "normalized"


def _now() -> float:
    return time.time()


def _safe_slug(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    slug = "".join(keep).strip("_")
    return slug[:48] or "avatar"


def new_record_id(source_path: str) -> str:
    src = Path(source_path)
    stat = src.stat()
    seed = f"{src.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{time.time_ns()}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def legacy_avatar_id_for_video(video_path: str, modelres: int = 384) -> str:
    digest = hashlib.sha1(str(Path(video_path).resolve()).encode("utf-8")).hexdigest()[:10]
    return f"aiszr_wav2lip{modelres}_{digest}"


def generated_avatar_id(record_id: str, quality: str, modelres: int = 384) -> str:
    quality_tag = "1080" if quality == "1080p" else "720"
    return f"aiszr_wav2lip{modelres}_{record_id}_{quality_tag}"


@dataclass(slots=True)
class AvatarRecord:
    id: str
    display_name: str
    source_path: str = ""
    original_video_path: str = ""
    normalized_video_path: str = ""
    livetalking_avatar_id: str = ""
    quality: str = DEFAULT_QUALITY
    fps: int = AVATAR_FPS
    status: str = STATUS_IMPORTED
    stage: str = "待处理"
    progress: int = 0
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, value: dict) -> "AvatarRecord":
        fields = {name for name in cls.__dataclass_fields__}
        payload = {k: v for k, v in (value or {}).items() if k in fields}
        if not payload.get("id"):
            raise ValueError("missing avatar record id")
        payload.setdefault("display_name", payload.get("id", "avatar"))
        record = cls(**payload)
        if record.quality not in ("720p", "1080p"):
            record.quality = DEFAULT_QUALITY
        if not record.fps:
            record.fps = AVATAR_FPS
        return record

    def to_dict(self) -> dict:
        return asdict(self)

    def video_path_for_pipeline(self) -> str:
        for candidate in (self.normalized_video_path, self.original_video_path, self.source_path):
            if candidate and Path(candidate).is_file():
                return candidate
        return ""

    def is_ready(self) -> bool:
        return self.status == STATUS_READY and bool(self.livetalking_avatar_id)


class AvatarLibrary:
    def __init__(self, path: Path | None = None):
        self.path = path or library_path()
        self.records: list[AvatarRecord] = []
        self.selected_id = ""
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        with self._lock:
            self.records = []
            self.selected_id = ""
            if not self.path.is_file():
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return
            if not isinstance(payload, dict):
                return
            self.selected_id = str(payload.get("selected_id") or "")
            for item in payload.get("records", []):
                if isinstance(item, dict):
                    try:
                        self.records.append(AvatarRecord.from_dict(item))
                    except Exception:
                        continue

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": LIBRARY_VERSION,
                "selected_id": self.selected_id,
                "records": [record.to_dict() for record in self.records],
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def list(self) -> list[AvatarRecord]:
        with self._lock:
            return list(self.records)

    def get(self, record_id: str) -> AvatarRecord | None:
        with self._lock:
            for record in self.records:
                if record.id == record_id:
                    return record
        return None

    def selected(self) -> AvatarRecord | None:
        with self._lock:
            return self.get(self.selected_id) if self.selected_id else None

    def selected_index(self) -> int:
        with self._lock:
            for index, record in enumerate(self.records):
                if record.id == self.selected_id:
                    return index
        return -1

    def set_selected(self, record_id: str) -> None:
        with self._lock:
            if self.get(record_id):
                self.selected_id = record_id
                self.save()

    def upsert(self, record: AvatarRecord) -> None:
        with self._lock:
            record.updated_at = _now()
            for index, existing in enumerate(self.records):
                if existing.id == record.id:
                    self.records[index] = record
                    self.save()
                    return
            self.records.append(record)
            if not self.selected_id:
                self.selected_id = record.id
            self.save()

    def import_video(self, source_path: str, quality: str = DEFAULT_QUALITY) -> AvatarRecord:
        with self._lock:
            if len(self.records) >= MAX_AVATAR_RECORDS:
                raise ValueError(f"主播形象最多支持 {MAX_AVATAR_RECORDS} 个")
            src = Path(source_path)
            if not src.is_file():
                raise FileNotFoundError(f"视频不存在: {source_path}")
            quality = quality if quality in ("720p", "1080p") else DEFAULT_QUALITY
            record_id = new_record_id(str(src))
            ext = src.suffix.lower() or ".mp4"
            target_dir = assets_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{record_id}_{_safe_slug(src.stem)}{ext}"
            shutil.copy2(src, target)
            now = _now()
            record = AvatarRecord(
                id=record_id,
                display_name=src.name,
                source_path=str(src),
                original_video_path=str(target),
                quality=quality,
                fps=AVATAR_FPS,
                status=STATUS_IMPORTED,
                stage="待处理",
                created_at=now,
                updated_at=now,
            )
            self.records.append(record)
            self.selected_id = record.id
            self.save()
            return record

    def migrate_video_paths(self, video_paths: Iterable[str], selected_index: int = 0, *, livetalking_root: str = "") -> None:
        with self._lock:
            existing = {
                str(Path(p).resolve()).lower()
                for record in self.records
                for p in (record.source_path, record.original_video_path, record.normalized_video_path)
                if p
            }
            created: list[AvatarRecord] = []
            paths = [p for p in video_paths if isinstance(p, str) and Path(p).is_file()]
            for path_text in paths:
                if len(self.records) >= MAX_AVATAR_RECORDS:
                    break
                resolved = str(Path(path_text).resolve()).lower()
                if resolved in existing:
                    continue
                src = Path(path_text)
                record_id = new_record_id(str(src))
                target_dir = assets_dir()
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{record_id}_{_safe_slug(src.stem)}{src.suffix.lower() or '.mp4'}"
                try:
                    shutil.copy2(src, target)
                except Exception:
                    target = src
                now = _now()
                legacy_avatar_id = legacy_avatar_id_for_video(str(src))
                status = STATUS_IMPORTED
                stage = "待处理"
                progress = 0
                avatar_id = ""
                if livetalking_root:
                    avatar_dir = Path(livetalking_root) / "data" / "avatars" / legacy_avatar_id
                    if avatar_cache_complete(avatar_dir):
                        status = STATUS_READY
                        stage = "可推流"
                        progress = 100
                        avatar_id = legacy_avatar_id
                record = AvatarRecord(
                    id=record_id,
                    display_name=src.name,
                    source_path=str(src),
                    original_video_path=str(target),
                    normalized_video_path=str(target) if status == STATUS_READY else "",
                    livetalking_avatar_id=avatar_id,
                    quality=DEFAULT_QUALITY,
                    fps=AVATAR_FPS,
                    status=status,
                    stage=stage,
                    progress=progress,
                    created_at=now,
                    updated_at=now,
                )
                self.records.append(record)
                created.append(record)
                existing.add(resolved)
            if self.records and not self.selected_id:
                index = max(0, min(int(selected_index or 0), len(self.records) - 1))
                self.selected_id = self.records[index].id
            elif created and 0 <= selected_index < len(created):
                self.selected_id = created[selected_index].id
            self.save()

    def remove(self, record_id: str, *, livetalking_root: str = "") -> AvatarRecord | None:
        with self._lock:
            record = self.get(record_id)
            if not record:
                return None
            self.records = [item for item in self.records if item.id != record_id]
            if self.selected_id == record_id:
                self.selected_id = self.records[0].id if self.records else ""
            self.save()
        self.delete_record_files(record, livetalking_root=livetalking_root)
        return record

    def delete_record_files(self, record: AvatarRecord, *, livetalking_root: str = "") -> None:
        root = data_root().resolve()
        for value in (record.original_video_path, record.normalized_video_path):
            if not value:
                continue
            path = Path(value)
            try:
                resolved = path.resolve()
                if root in resolved.parents and path.is_file():
                    path.unlink(missing_ok=True)
            except Exception:
                pass
        if livetalking_root and record.livetalking_avatar_id.startswith("aiszr_"):
            avatar_dir = Path(livetalking_root) / "data" / "avatars" / record.livetalking_avatar_id
            with contextlib_suppress():
                shutil.rmtree(avatar_dir, ignore_errors=True)


def avatar_cache_complete(avatar_dir: Path) -> bool:
    full_imgs = avatar_dir / "full_imgs"
    face_imgs = avatar_dir / "face_imgs"
    return (
        (avatar_dir / "coords.pkl").is_file()
        and full_imgs.is_dir()
        and face_imgs.is_dir()
        and any(full_imgs.glob("*.png"))
        and any(face_imgs.glob("*.png"))
    )


class contextlib_suppress:
    def __enter__(self):
        return None

    def __exit__(self, *_exc):
        return True
