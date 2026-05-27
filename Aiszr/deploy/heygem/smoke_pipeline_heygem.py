"""HeyGem pipeline 集成 smoke test — 绕开 UI 直接跑底层。

为什么这个脚本存在：
  voiceconfigpage.py 在禁改清单上，没法直接加 use_heygem checkbox 跑端到端。
  这个脚本绕开 UI，直接构造 PipelineConfig(use_heygem=True) 跑：
    TTS WAV → HeyGem mp4 → start_hls_push → HLS m3u8 文件
  验证 digital_human_pipeline.py 的 HeyGem 分支在生产环境跑得通。

典型用法：
    # 1. 起 HeyGem 容器：docker compose -f deploy/heygem/docker-compose-aiszr.yml up -d
    # 2. 确保 D:/duix_avatar_data/face2face/temp/{anchor.mp4, bench.wav} 都在
    # 3. 跑：.venv/Scripts/python.exe deploy/heygem/smoke_pipeline_heygem.py

输出：
  hls_dir/stream.m3u8 + seg_*.ts，可用 ffplay http://127.0.0.1:8780/stream.m3u8 验证
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from digital_human_pipeline import DigitalHumanPipeline, PipelineConfig, PipelineState  # noqa: E402
from ffmpeg_ops import start_hls_push  # noqa: E402

DATA_ROOT = Path("D:/duix_avatar_data/face2face")
ANCHOR_MP4 = DATA_ROOT / "temp" / "anchor.mp4"
WAV_PATH = DATA_ROOT / "temp" / "bench.wav"  # benchmark 生成的 sine wav，可直接复用
HLS_DIR = Path(__file__).resolve().parent / "smoke_hls"


async def main() -> int:
    # 输入 sanity check
    for p, label in ((ANCHOR_MP4, "anchor mp4"), (WAV_PATH, "wav")):
        if not p.is_file():
            print(f"[err] {label} 不存在: {p}")
            print("      先跑 benchmark.py --gen-wav 生成 wav，再拷 anchor.mp4 进 temp/")
            return 2

    # Phase 1: HeyGem 合成
    print(f"[phase1] HeyGem 合成 wav={WAV_PATH.name} anchor={ANCHOR_MP4.name}")
    pipeline = DigitalHumanPipeline(voice_manager=None, log_callback=lambda m: print(f"  [pipe] {m}"))
    cfg = PipelineConfig(
        use_heygem=True,
        heygem_avatar_video_path=str(ANCHOR_MP4),
        heygem_timeout_sec=600.0,
    )
    heygem_result = await pipeline._heygem_synthesize(cfg, str(WAV_PATH))
    if not heygem_result.get("ok"):
        print(f"[err] HeyGem 合成失败: {heygem_result.get('message')}")
        return 3
    mp4_path = heygem_result["mp4_path"]
    mp4_size = Path(mp4_path).stat().st_size
    print(f"[phase1] OK mp4={mp4_path} size={mp4_size/1024:.1f} KiB")

    # Phase 2: HLS push（mp4 含音频，audio_path=None）
    HLS_DIR.mkdir(parents=True, exist_ok=True)
    for f in HLS_DIR.glob("*"):
        f.unlink(missing_ok=True)
    print(f"[phase2] start_hls_push 推流 mp4 -> {HLS_DIR}")
    proc = await start_hls_push(
        video_path=mp4_path,
        audio_path=None,  # mp4 自带音频
        hls_dir=str(HLS_DIR),
    )

    # 等 m3u8 出现（最多 15s，每 100ms 探一次）
    m3u8 = HLS_DIR / "stream.m3u8"
    deadline = 150
    for _ in range(deadline):
        if proc.returncode is not None:
            stderr = await proc.stderr.read()
            print(f"[err] ffmpeg 提前退出 returncode={proc.returncode}")
            print(f"      stderr: {stderr.decode(errors='replace')[:1000]}")
            return 4
        if m3u8.exists() and m3u8.stat().st_size > 0:
            break
        await asyncio.sleep(0.1)
    else:
        print(f"[err] HLS m3u8 15s 内未生成: {m3u8}")
        proc.terminate()
        await proc.wait()
        return 5

    # 看下生成的 segments
    segs = sorted(HLS_DIR.glob("seg_*.ts"))
    print(f"[phase2] OK m3u8={m3u8} ({m3u8.stat().st_size}B), {len(segs)} segments")

    # 让它再推一会儿（5s）观察循环 — 看是否 stream_loop 生效
    await asyncio.sleep(5)
    segs_after = sorted(HLS_DIR.glob("seg_*.ts"))
    print(f"[phase2] 5s 后 {len(segs_after)} segments — 循环 {'OK' if len(segs_after) > len(segs) else '可疑(无新分片)'}")

    # 收
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        proc.kill()

    print(f"[done] 完整链路通了。手动验证：")
    print(f"       ffplay file:///{m3u8.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
