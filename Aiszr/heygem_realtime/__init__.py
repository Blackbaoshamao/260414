"""HeyGem 实时口型驱动子系统。

UI 入口在 `ui_pages.heygem_preview_dialog.HeyGemPreviewDialog`；本包只提供
worker / SDK 抽象。worker 严格遵循 Aiszr 的 QObject + moveToThread 约定。
"""
from __future__ import annotations

from heygem_realtime.client import (
    HeyGemNotInstalledError,
    HeyGemRealtimeClient,
    LipFrame,
    build_default_client,
)
from heygem_realtime.audio_worker import AudioPlaybackWorker
from heygem_realtime.video_worker import HeyGemWorker

__all__ = [
    "HeyGemNotInstalledError",
    "HeyGemRealtimeClient",
    "LipFrame",
    "build_default_client",
    "AudioPlaybackWorker",
    "HeyGemWorker",
]
