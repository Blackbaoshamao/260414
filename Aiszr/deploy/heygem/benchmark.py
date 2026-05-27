"""HeyGem batch benchmark — exercise :8383 /easy/submit + /easy/query on 5060.

为什么这个脚本存在：
  Docker 镜像跑起来后第一件事就是要量真实延迟 — submit 一个已知长度的 WAV，
  看 HeyGem 多久吐 mp4。这决定了 Aiszr 直播闭环的"段长 vs 端到端延迟"调参。

  对比 server.py / smoke_test.py（WS 流式 stub）：那是 [HEYGEM_HANDOFF.md] 原 plan
  的 Protocol mock；HeyGem 官方 API 是 batch submit-and-wait，本脚本走的是真实路径。

典型用法：
    # 准备：把 anchor.mp4 拷到 D:/duix_avatar_data/face2face/temp/
    .venv/Scripts/python.exe deploy/heygem/benchmark.py --gen-wav --avatar anchor.mp4

    # 输出示例：
    #   alive OK
    #   wav 3.00s -> mp4 in 4.82s | ratio 1:1.6 (超实时) | mp4 size 412 KiB
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import math
import struct
import sys
import time
import wave
from pathlib import Path

# Windows 控制台默认 GBK，stdout 上有非 ASCII 字符会 UnicodeEncodeError
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

# 让脚本既能 `python benchmark.py` 直接跑，也能 `python -m` 跑
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from heygem_realtime.batch_client import (  # noqa: E402
    HeyGemBatchClient,
    HeyGemBatchError,
    HeyGemServiceNotReady,
)

DATA_ROOT = Path("D:/duix_avatar_data/face2face")
DEFAULT_WAV_REL = "temp/bench.wav"
DEFAULT_AVATAR_REL = "temp/anchor.mp4"
SAMPLE_RATE = 24000  # HeyGem 期望 24k mono s16le
DEFAULT_DURATION_SEC = 3.0
DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "bench_results.csv"
CSV_HEADER = ("timestamp", "anchor", "wav_sec", "chaofen", "elapsed_sec", "ratio", "mp4_path", "note")


def gen_sine_wav(out_path: Path, duration_sec: float, freq_hz: float = 220.0) -> None:
    """生成 24kHz mono s16le sine — 仅供 benchmark 用，不是真实音色。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(SAMPLE_RATE * duration_sec)
    amp = 12000  # 不要打满 32767，留点 headroom
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for i in range(n_samples):
            s = int(amp * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE))
            w.writeframes(struct.pack("<h", s))


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def append_csv(csv_path: Path, row: dict) -> None:
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if new_file:
            w.writeheader()
        w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--wav",
        default=DEFAULT_WAV_REL,
        help=f"WAV 相对 data_root 的路径，默认 {DEFAULT_WAV_REL}",
    )
    ap.add_argument(
        "--avatar",
        default=DEFAULT_AVATAR_REL,
        help=f"avatar mp4 相对 data_root 的路径，默认 {DEFAULT_AVATAR_REL}",
    )
    ap.add_argument(
        "--gen-wav",
        action="store_true",
        help=f"用 sine 波生成一个 {DEFAULT_DURATION_SEC}s 的 WAV 到 --wav 指定路径",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_SEC,
        help="--gen-wav 时的时长（秒），默认 3.0",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="单次合成最长等待秒数，默认 600",
    )
    ap.add_argument(
        "--chaofen",
        type=int,
        default=0,
        choices=(0, 1),
        help="HeyGem 超分（GFPGAN），0 关 / 1 开，默认 0",
    )
    ap.add_argument(
        "--note",
        default="",
        help="本次 benchmark 备注（写入 CSV），比如 'before_tf32' / 'after_tf32'",
    )
    ap.add_argument(
        "--csv",
        default=str(DEFAULT_CSV_PATH),
        help=f"benchmark 结果 CSV 路径（append），默认 {DEFAULT_CSV_PATH}",
    )
    args = ap.parse_args()

    wav_path = (DATA_ROOT / args.wav).resolve()
    avatar_path = (DATA_ROOT / args.avatar).resolve()

    if args.gen_wav:
        print(f"[gen] {wav_path} ({args.duration}s @ {SAMPLE_RATE}Hz)")
        gen_sine_wav(wav_path, args.duration)

    if not wav_path.is_file():
        print(f"[err] WAV 不存在: {wav_path}（用 --gen-wav 或先把文件放进去）")
        return 2
    if not avatar_path.is_file():
        print(f"[err] avatar 不存在: {avatar_path}（先把 mp4 拷到 data_root 下）")
        return 2

    client = HeyGemBatchClient(data_root=DATA_ROOT, timeout_sec=args.timeout)

    if not client.is_alive():
        print(f"[err] {client.base_url} 不可达 — docker compose 起来了吗？")
        return 3
    print("alive OK")

    # 拿 WAV 时长用于算 ratio
    with wave.open(str(wav_path), "rb") as w:
        wav_sec = w.getnframes() / float(w.getframerate())

    t0 = time.monotonic()
    try:
        result = client.synthesize(
            wav_path=wav_path,
            avatar_video_path=avatar_path,
            chaofen=args.chaofen,
        )
    except HeyGemServiceNotReady as exc:
        print(f"[err] 服务不可达: {exc}")
        return 3
    except HeyGemBatchError as exc:
        print(f"[err] 合成失败: {exc}")
        return 4
    elapsed = time.monotonic() - t0

    if not result.mp4_abs_path.is_file():
        print(f"[err] HeyGem 报成功，但 mp4 不存在: {result.mp4_abs_path}")
        print(f"      progress_log: {result.progress_log[-5:]}")
        return 5

    size = result.mp4_abs_path.stat().st_size
    ratio = elapsed / max(wav_sec, 1e-9)
    realtime_tag = "超实时" if ratio < 1.0 else "亚实时"

    print(
        f"wav {wav_sec:.2f}s -> mp4 in {elapsed:.2f}s | "
        f"ratio 1:{ratio:.2f} ({realtime_tag}) | "
        f"mp4 size {fmt_bytes(size)}"
    )
    print(f"     code={result.code}")
    print(f"     mp4 ={result.mp4_abs_path}")
    if result.progress_log:
        print(f"     last_progress={result.progress_log[-1]}")

    csv_path = Path(args.csv)
    append_csv(csv_path, {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "anchor": args.avatar,
        "wav_sec": f"{wav_sec:.2f}",
        "chaofen": args.chaofen,
        "elapsed_sec": f"{elapsed:.2f}",
        "ratio": f"{ratio:.3f}",
        "mp4_path": str(result.mp4_abs_path),
        "note": args.note,
    })
    print(f"     csv  ={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
