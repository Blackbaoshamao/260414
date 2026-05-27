"""HeyGem batch synthesis client — POST /easy/submit + GET /easy/query polling.

为什么是 batch 而不是 WS 流式：
  HeyGem 官方 API（docker-compose-5090.yml 暴露的 :8383）是 submit-and-wait，
  不存在流式 push-chunk/pull-frame 接口。对 Aiszr 直播助手用例，全 WAV → 全 mp4
  → ffmpeg HLS 推 OBS 才是真实可行的路径。

  与 [client.py](Aiszr/heygem_realtime/client.py) 的 `_RealHeyGemClient`（WS 流式
  Protocol）并存——后者保留是因为它是 [HEYGEM_HANDOFF.md](Aiszr/HEYGEM_HANDOFF.md)
  原 plan 的契约 stub，单元测试还依赖它；但实际接 HeyGem 走的是这个 batch 客户端。

容器路径假设：
  docker-compose-5090.yml 把 `D:/duix_avatar_data/face2face` 挂载到容器
  `/code/data`。HeyGem 内部（参考 deploy/heygem/HeyGem/src/main/config/config.js
  的 assetPath.model = `D:/duix_avatar_data/face2face/temp`）硬编码了 **`/code/data/temp/`**
  作为输入文件目录——submit 时发的是**裸文件名**（不带任何目录前缀），
  HeyGem 内部自动拼成 `/code/data/temp/<文件名>`。

  因此本客户端的契约：
    - 输入 wav / avatar 文件必须放在 `<data_root>/temp/` 下
    - submit body 的 audio_url / video_url 是裸文件名（如 `anchor.mp4`）
    - 返回的 result 是相对 `/code/data/` 的路径（实测 `temp/<code>-r.mp4`）

  踩过的坑（2026-05-25）：
    发 `temp/anchor.mp4` → HeyGem 拼成 `/code/data/temp/temp/anchor.mp4` → ffprobe 找不到
    → 抛 KeyError: 'streams' → "三次获取音频时长失败"。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from loguru import logger


DEFAULT_BASE_URL = "http://127.0.0.1:8383/easy"
DEFAULT_DATA_ROOT = Path("D:/duix_avatar_data/face2face")
DEFAULT_POLL_INTERVAL_SEC = 0.5
DEFAULT_TIMEOUT_SEC = 600

# 失败码（来自 src/main/service/video.js loopPending）
FAILURE_CODES = (9999, 10002, 10003)


class HeyGemBatchError(RuntimeError):
    """HeyGem 提交 / 轮询 / 合成失败的统一异常。"""


class HeyGemServiceNotReady(HeyGemBatchError):
    """:8383 不可达——Docker 没起来，或镜像没拉好。"""


@dataclass
class SynthesisResult:
    code: str
    mp4_abs_path: Path
    elapsed_sec: float
    progress_log: list[str] = field(default_factory=list)


class HeyGemBatchClient:
    """同步阻塞客户端：submit → poll → 返 mp4 绝对路径。

    线程模型：单纯的 HTTP 客户端，无内部线程；调用方自己决定从哪条线程发起
    （Aiszr 通常从 worker 线程调，避免阻塞 Qt event loop）。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        data_root: Path | str = DEFAULT_DATA_ROOT,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.data_root = Path(data_root).resolve()
        self.poll_interval_sec = poll_interval_sec
        self.timeout_sec = timeout_sec

    def is_alive(self) -> bool:
        """探活：调用 /query 用一个绝对不存在的 code，只要连接通就视为 alive。
        HeyGem 官方没有 /health 端点（参考 src/main/api/f2f.js）。"""
        try:
            resp = requests.get(
                f"{self.base_url}/query",
                params={"code": "AISZR_PROBE_NONEXISTENT"},
                timeout=3,
            )
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def synthesize(
        self,
        *,
        wav_path: Path | str,
        avatar_video_path: Path | str,
        code: Optional[str] = None,
        chaofen: int = 0,
        watermark_switch: int = 0,
    ) -> SynthesisResult:
        wav = Path(wav_path).resolve()
        avatar = Path(avatar_video_path).resolve()
        temp_dir = (self.data_root / "temp").resolve()

        for p, name in ((wav, "wav_path"), (avatar, "avatar_video_path")):
            if not p.is_file():
                raise HeyGemBatchError(f"{name} 不存在: {p}")
            try:
                p.relative_to(temp_dir)
            except ValueError as exc:
                raise HeyGemBatchError(
                    f"{name} 必须在 {temp_dir} 下（HeyGem 内部硬编码 /code/data/temp/ 前缀）: {p}"
                ) from exc

        wav_name = wav.name
        avatar_name = avatar.name

        task_code = code or uuid.uuid4().hex
        body = {
            "audio_url": wav_name,
            "video_url": avatar_name,
            "code": task_code,
            "chaofen": chaofen,
            "watermark_switch": watermark_switch,
            "pn": 1,
        }

        # ── submit ─────────────────────────────────────────────────────────
        try:
            resp = requests.post(f"{self.base_url}/submit", json=body, timeout=10)
            resp.raise_for_status()
            submit = resp.json()
        except requests.ConnectionError as exc:
            raise HeyGemServiceNotReady(
                f":8383 不可达 — docker-compose 起来了吗? {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise HeyGemBatchError(f"submit 失败: {exc}") from exc

        if submit.get("code") != 10000:
            raise HeyGemBatchError(
                f"submit 被拒: code={submit.get('code')} msg={submit.get('msg')}"
            )

        logger.info("HeyGem submit OK code={} body={}", task_code, body)

        # ── poll ───────────────────────────────────────────────────────────
        progress_log: list[str] = []
        started = time.monotonic()
        deadline = started + self.timeout_sec

        while time.monotonic() < deadline:
            time.sleep(self.poll_interval_sec)
            try:
                resp = requests.get(
                    f"{self.base_url}/query",
                    params={"code": task_code},
                    timeout=5,
                )
                resp.raise_for_status()
                q = resp.json()
            except requests.RequestException as exc:
                progress_log.append(f"poll err: {exc}")
                continue

            q_code = q.get("code")
            if q_code in FAILURE_CODES:
                raise HeyGemBatchError(
                    f"合成失败 code={q_code} msg={q.get('msg')} log={progress_log}"
                )
            if q_code != 10000:
                progress_log.append(f"unexpected outer code={q_code}")
                continue

            data = q.get("data") or {}
            status = data.get("status")
            if status == 1:  # processing
                progress_log.append(f"processing {data.get('progress', '?')}")
                continue
            if status == 2:  # done
                result_rel = data.get("result")
                if not result_rel:
                    raise HeyGemBatchError(f"status=2 但无 result 字段: {data}")
                # HeyGem 返回 "/AISZR_BARE_1-r.mp4"（带前导 /），实际文件在
                # /code/data/temp/AISZR_BARE_1-r.mp4，即相对 temp_dir 的裸文件名。
                result_name = result_rel.lstrip("/")
                mp4_abs = temp_dir / result_name
                return SynthesisResult(
                    code=task_code,
                    mp4_abs_path=mp4_abs,
                    elapsed_sec=time.monotonic() - started,
                    progress_log=progress_log,
                )
            if status == 3:  # failed
                raise HeyGemBatchError(
                    f"合成失败 status=3 msg={data.get('msg')} log={progress_log}"
                )
            progress_log.append(f"unknown status={status}")

        raise HeyGemBatchError(
            f"轮询超时 {self.timeout_sec}s code={task_code} log={progress_log}"
        )
