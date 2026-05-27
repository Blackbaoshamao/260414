"""ffmpeg subprocess operations for digital human video compositing and RTMP push."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger


@dataclass(slots=True)
class FFmpegResult:
    ok: bool
    message: str
    duration_sec: float = 0.0


class FFmpegNotFoundError(FileNotFoundError):
    pass


def _resolve_ffmpeg_path() -> str:
    """Locate ffmpeg binary: PATH first, then imageio-ffmpeg bundle."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, Exception):
        pass
    raise FFmpegNotFoundError(
        "ffmpeg 未找到。请安装 ffmpeg 或 pip install imageio-ffmpeg"
    )


def _parse_ffmpeg_progress(line: str) -> float | None:
    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
    if not match:
        return None
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


async def check_ffmpeg_available() -> str:
    return _resolve_ffmpeg_path()


async def start_image_sequence_hls(
    playlist_path: str,
    hls_dir: str,
    fps: int = 25,
    input_fps: int = 25,
    audio_path: str | None = None,
) -> asyncio.subprocess.Process:
    """Encode a frame sequence (PNG concat playlist) to HLS segments.

    The playlist file is a ffmpeg concat format text file listing PNG frames.
    ffmpeg loops the playlist so the stream is continuous.

    If audio_path is provided, it is looped alongside the video.
    """
    ffmpeg_path = _resolve_ffmpeg_path()

    hls_path = os.path.join(hls_dir, "stream.m3u8")
    segment_pattern = os.path.join(hls_dir, "seg_%03d.ts")

    cmd = [
        ffmpeg_path,
        "-f", "concat",
        "-safe", "0",
        "-stream_loop", "-1",
        "-framerate", str(input_fps),
        "-i", playlist_path,
    ]
    if audio_path and os.path.isfile(audio_path):
        cmd += [
            "-stream_loop", "-1",
            "-i", str(audio_path),
        ]
    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
    ]
    if audio_path and os.path.isfile(audio_path):
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += [
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "3",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", segment_pattern,
        hls_path,
    ]

    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )


async def extract_thumbnail(video_path: str, output_path: str, *, seek: float = 0.5) -> bool:
    ffmpeg_path = _resolve_ffmpeg_path()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-y",
        "-i", str(video_path),
        "-ss", str(seek),
        "-vframes", "1",
        "-q:v", "2",
        str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    return proc.returncode == 0 and output.exists()


async def get_media_duration(path: str) -> float:
    ffmpeg_path = _resolve_ffmpeg_path()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_path,
        "-i", str(path),
        "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return 0.0
    output = stderr.decode(errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return 0.0
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


async def _run_ffmpeg_command(
    cmd: list[str],
    total_duration: float,
    *,
    on_progress: Callable[[float, float], None] | None = None,
) -> FFmpegResult:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )

    last_progress = 0.0
    stderr_lines: list[str] = []
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        decoded = line.decode(errors="replace")
        stderr_lines.append(decoded.rstrip())
        progress = _parse_ffmpeg_progress(decoded)
        if progress is not None:
            last_progress = progress
            if on_progress and total_duration > 0:
                on_progress(progress, total_duration)

    await proc.wait()

    if proc.returncode not in (0, 255):
        tail = "\n".join(stderr_lines[-5:])
        return FFmpegResult(False, f"ffmpeg 退出码 {proc.returncode}\n{tail}")

    return FFmpegResult(True, "完成", duration_sec=last_progress)


async def composite_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    *,
    on_progress: Callable[[float, float], None] | None = None,
) -> FFmpegResult:
    ffmpeg_path = _resolve_ffmpeg_path()

    audio_duration = await get_media_duration(audio_path)
    if audio_duration <= 0:
        return FFmpegResult(False, "无法确定音频时长")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-shortest",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        str(output_path),
    ]

    result = await _run_ffmpeg_command(cmd, audio_duration, on_progress=on_progress)
    if result.ok and not output.exists():
        return FFmpegResult(False, "合成文件未生成")
    return result


async def start_hls_push(
    video_path: str,
    audio_path: str | None,
    hls_dir: str,
) -> asyncio.subprocess.Process:
    """Start ffmpeg writing HLS segments to a local directory.

    audio_path=None 时走 HeyGem 路径 — video 是音视频合一的 mp4，直接 -map 0:a。
    audio_path=path 时走原绿幕路径 — video 是无声循环视频，audio 单独从 WAV 接入。
    """
    ffmpeg_path = _resolve_ffmpeg_path()

    hls_path = os.path.join(hls_dir, "stream.m3u8")
    segment_pattern = os.path.join(hls_dir, "seg_%03d.ts")

    use_external_audio = bool(audio_path) and os.path.isfile(audio_path)

    cmd = [
        ffmpeg_path,
        "-stream_loop", "-1",
        "-re",
        "-i", str(video_path),
    ]
    if use_external_audio:
        cmd += [
            "-stream_loop", "-1",
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
        ]
    else:
        cmd += [
            "-map", "0:v:0",
            "-map", "0:a:0?",  # ?: 容忍 mp4 没音轨（不该出现，但保险）
        ]
    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "3",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", segment_pattern,
        hls_path,
    ]

    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )
