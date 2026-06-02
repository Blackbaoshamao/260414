"""Background queue for preparing LiveTalking avatar caches."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from avatar_library import (
    AVATAR_FPS,
    AvatarLibrary,
    AvatarRecord,
    STATUS_CHECKING,
    STATUS_FAILED,
    STATUS_NORMALIZING,
    STATUS_PROCESSING_CPU,
    STATUS_PROCESSING_GPU,
    STATUS_READY,
    avatar_cache_complete,
    generated_avatar_id,
    normalized_dir,
)
from ffmpeg_ops import _resolve_ffmpeg_path
from livetalking_runtime import default_livetalking_root, default_python_exe_path


StatusCallback = Callable[[dict], None]
LogCallback = Callable[[str], None]


class AvatarPreprocessManager:
    def __init__(
        self,
        library: AvatarLibrary,
        *,
        status_callback: StatusCallback | None = None,
        log_callback: LogCallback | None = None,
        livetalking_root: str | None = None,
        python_exe_path: str | None = None,
    ):
        self.library = library
        self.status_callback = status_callback or (lambda payload: None)
        self.log = log_callback or (lambda message: None)
        self.livetalking_root = Path(livetalking_root) if livetalking_root else default_livetalking_root()
        self.python_exe_path = Path(python_exe_path) if python_exe_path else default_python_exe_path()
        self._queue: list[str] = []
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = False
        self._current_id = ""
        self._current_proc: subprocess.Popen | None = None
        self._cuda_available: bool | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(target=self._run_loop, name="AvatarPreprocess", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop = True
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()

    def enqueue(self, record_id: str, *, priority: bool = False) -> None:
        with self._lock:
            if record_id in self._queue or record_id == self._current_id:
                if priority and record_id in self._queue:
                    self._queue.remove(record_id)
                    self._queue.insert(0, record_id)
                return
            if priority:
                self._queue.insert(0, record_id)
            else:
                self._queue.append(record_id)
            self._cancelled.discard(record_id)
        self.start()

    def enqueue_pending(self) -> None:
        for record in self.library.list():
            if record.status != STATUS_READY:
                self.enqueue(record.id, priority=(record.id == self.library.selected_id))

    def cancel(self, record_id: str) -> None:
        with self._lock:
            self._cancelled.add(record_id)
            self._queue = [item for item in self._queue if item != record_id]
            if record_id == self._current_id and self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if self._stop:
                    return
                record_id = self._queue.pop(0) if self._queue else ""
                self._current_id = record_id
            if not record_id:
                return
            try:
                if record_id in self._cancelled:
                    continue
                self._process(record_id)
            except Exception as exc:
                record = self.library.get(record_id)
                if record:
                    self._update(record, STATUS_FAILED, f"处理失败: {exc}", 0, error=str(exc))
            finally:
                with self._lock:
                    self._current_id = ""
                    self._current_proc = None

    def _process(self, record_id: str) -> None:
        record = self.library.get(record_id)
        if not record:
            return
        video_path = Path(record.original_video_path or record.source_path)
        if not video_path.is_file():
            self._update(record, STATUS_FAILED, "源视频不存在", 0, error="源视频不存在")
            return

        avatar_id = generated_avatar_id(record.id, record.quality)
        avatar_dir = self.livetalking_root / "data" / "avatars" / avatar_id
        if avatar_cache_complete(avatar_dir):
            record.livetalking_avatar_id = avatar_id
            self._update(record, STATUS_READY, "可推流", 100)
            return

        normalized_path = self._normalize_video(record, video_path)
        record.normalized_video_path = str(normalized_path)
        record.livetalking_avatar_id = avatar_id
        self.library.upsert(record)

        self._precheck(record, normalized_path)
        if self._is_cancelled(record.id):
            return

        attempts: list[tuple[str, str, int]] = []
        if self._has_cuda():
            attempts.extend([
                (STATUS_PROCESSING_GPU, "cuda", 8),
                (STATUS_PROCESSING_GPU, "cuda", 4),
            ])
        attempts.append((STATUS_PROCESSING_CPU, "cpu", 1))

        last_error = ""
        for status, device, batch_size in attempts:
            if self._is_cancelled(record.id):
                return
            stage = "GPU生成中" if device == "cuda" else "CPU兼容生成中"
            self._update(record, status, stage, 45 if device == "cuda" else 60)
            self._clear_avatar_dir(avatar_dir)
            ok, message = self._generate_avatar(
                record=record,
                video_path=normalized_path,
                avatar_id=avatar_id,
                device=device,
                batch_size=batch_size,
            )
            if ok:
                if device == "cpu" and last_error:
                    record.error = "GPU失败，已使用CPU兼容模式完成"
                self._update(record, STATUS_READY, "可推流", 100, error=record.error)
                return
            last_error = message
            self.log(f"Avatar {record.id} {device} batch={batch_size} failed: {message}")

        self._clear_avatar_dir(avatar_dir)
        self._update(record, STATUS_FAILED, "处理失败", 0, error=last_error or "avatar生成失败")

    def _normalize_video(self, record: AvatarRecord, video_path: Path) -> Path:
        height = 1080 if record.quality == "1080p" else 720
        output = normalized_dir() / f"{record.id}_{height}p_{AVATAR_FPS}fps.mp4"
        if output.is_file():
            self._update(record, STATUS_NORMALIZING, "视频已标准化", 20)
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        self._update(record, STATUS_NORMALIZING, "视频标准化中", 10)
        vf = f"fps={AVATAR_FPS},scale=-2:{height}:flags=lanczos"
        cmd = [
            _resolve_ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-map", "0:v:0",
            "-an",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not output.is_file():
            raise RuntimeError((proc.stderr or proc.stdout or "视频标准化失败")[-1000:])
        self._update(record, STATUS_NORMALIZING, "视频标准化完成", 25)
        return output

    def _precheck(self, record: AvatarRecord, video_path: Path) -> None:
        self._update(record, STATUS_CHECKING, "视频体检中", 30)
        script = Path(__file__).resolve().with_name("livetalking_avatar_precheck.py")
        env = self._base_env(device="cpu")
        cmd = [
            str(self.python_exe_path),
            str(script),
            str(video_path),
            "--sample-count", "12",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.livetalking_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        result = _last_json(proc.stdout)
        if proc.returncode != 0 or not result.get("ok"):
            message = result.get("message") or (proc.stderr or proc.stdout or "视频体检失败")
            raise RuntimeError(str(message)[-1000:])
        ratio = float(result.get("ratio") or 0.0)
        self._update(record, STATUS_CHECKING, f"视频体检通过 {ratio:.0%}", 35)

    def _generate_avatar(
        self,
        *,
        record: AvatarRecord,
        video_path: Path,
        avatar_id: str,
        device: str,
        batch_size: int,
    ) -> tuple[bool, str]:
        avatar_dir = self.livetalking_root / "data" / "avatars" / avatar_id
        log_dir = normalized_dir().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"avatar_{record.id}_{device}_b{batch_size}.log"
        cmd = [
            str(self.python_exe_path),
            "-m", "avatars.wav2lip.genavatar",
            "--video_path", str(video_path),
            "--avatar_id", avatar_id,
            "--save_path", "data/avatars",
            "--img_size", "384",
            "--face_det_batch_size", str(batch_size),
        ]
        env = self._base_env(device=device)
        complete_since = 0.0
        deadline = time.monotonic() + 30 * 60
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write("Command:\n" + " ".join(cmd) + "\n\n")
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.livetalking_root),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with self._lock:
                self._current_proc = proc
            while proc.poll() is None:
                if self._is_cancelled(record.id):
                    proc.terminate()
                    return False, "任务已取消"
                if time.monotonic() > deadline:
                    proc.kill()
                    return False, "avatar生成超时"
                if avatar_cache_complete(avatar_dir):
                    if complete_since == 0.0:
                        complete_since = time.monotonic()
                    elif time.monotonic() - complete_since >= 2.0:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        break
                else:
                    complete_since = 0.0
                time.sleep(1.0)
            proc.wait(timeout=10)
        if avatar_cache_complete(avatar_dir):
            return True, ""
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        except Exception:
            pass
        return False, tail or f"avatar生成失败: {log_path}"

    def _has_cuda(self) -> bool:
        if self._cuda_available is not None:
            return self._cuda_available
        cmd = [
            str(self.python_exe_path),
            "-c",
            "import torch, json; print(json.dumps({'cuda': bool(torch.cuda.is_available())}))",
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.livetalking_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            result = _last_json(proc.stdout)
            self._cuda_available = bool(result.get("cuda"))
        except Exception:
            self._cuda_available = False
        return self._cuda_available

    def _base_env(self, *, device: str) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["LIVETALKING_AVATAR_DEVICE"] = device
        root_text = str(self.livetalking_root)
        old_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root_text + (os.pathsep + old_pythonpath if old_pythonpath else "")
        return env

    def _update(self, record: AvatarRecord, status: str, stage: str, progress: int, *, error: str = "") -> None:
        latest = self.library.get(record.id) or record
        latest.status = status
        latest.stage = stage
        latest.progress = max(0, min(100, int(progress)))
        latest.error = error
        self.library.upsert(latest)
        self.status_callback({
            "record_id": latest.id,
            "status": latest.status,
            "stage": latest.stage,
            "progress": latest.progress,
            "error": latest.error,
        })

    def _is_cancelled(self, record_id: str) -> bool:
        with self._lock:
            return self._stop or record_id in self._cancelled

    def _clear_avatar_dir(self, avatar_dir: Path) -> None:
        if avatar_dir.is_dir() and avatar_dir.name.startswith("aiszr_"):
            shutil.rmtree(avatar_dir, ignore_errors=True)


def _last_json(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}
