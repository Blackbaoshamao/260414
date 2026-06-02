"""Sample a video with LiveTalking's face detector and report face stability."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np


def _sample_indices(frame_count: int, sample_count: int) -> list[int]:
    if frame_count <= 0:
        return []
    sample_count = max(1, min(sample_count, frame_count))
    return sorted({int(round(x)) for x in np.linspace(0, frame_count - 1, sample_count)})


def check_video(video_path: str, sample_count: int = 12, min_face_ratio: float = 0.01) -> dict:
    import cv2
    import torch
    from avatars.wav2lip import face_detection

    device = os.environ.get("LIVETALKING_AVATAR_DEVICE", "").strip()
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    cap = cv2.VideoCapture(video_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frame_count <= 0:
        return {"ok": False, "message": "无法读取视频帧", "ratio": 0.0, "checked": 0}

    frames = []
    for index in _sample_indices(frame_count, sample_count):
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append(frame)
    cap.release()
    if not frames:
        return {"ok": False, "message": "无法抽样视频帧", "ratio": 0.0, "checked": 0}

    detector = face_detection.FaceAlignment(
        face_detection.LandmarksType._2D,
        flip_input=False,
        device=device,
    )
    predictions = detector.get_detections_for_batch(np.array(frames))
    detected = 0
    for rect, frame in zip(predictions, frames):
        if rect is None:
            continue
        x1, y1, x2, y2 = rect
        face_area = max(0, x2 - x1) * max(0, y2 - y1)
        frame_area = max(1, frame.shape[0] * frame.shape[1])
        if face_area / frame_area >= min_face_ratio:
            detected += 1

    ratio = detected / max(1, len(frames))
    if ratio >= 0.9:
        message = "人脸稳定"
        ok = True
    elif ratio >= 0.8:
        message = "人脸基本稳定"
        ok = True
    else:
        message = "人脸不稳定，请更换正脸清晰、无遮挡的视频"
        ok = False
    return {
        "ok": ok,
        "message": message,
        "ratio": ratio,
        "checked": len(frames),
        "device": device,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path")
    parser.add_argument("--sample-count", type=int, default=12)
    parser.add_argument("--min-face-ratio", type=float, default=0.01)
    args = parser.parse_args()
    result = check_video(args.video_path, args.sample_count, args.min_face_ratio)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
