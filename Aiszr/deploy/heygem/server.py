"""HeyGem 实时口型驱动 — Aiszr 适配 server 模板。

骨架：HTTP/WS 协议**完整对齐** Aiszr 端 (Aiszr/heygem_realtime/client.py)，
但 `_heygem_inference` 是 mock 实现（按 PCM 音量画矩形开合嘴），先证明 wire 通了。

部署 HeyGem fork 时，按 `=== HeyGem 真实部署点 N ===` 注释替换三处占位。

────────────────────────────────────────────────────────────────────────────
有线协议（与 client.py 一字不差）
────────────────────────────────────────────────────────────────────────────
HTTP（JSON）
  GET  /v1/health                       → 200 {"ok": true, ...}
  POST /v1/sessions/start               → 200 {"session_id": "<hex>"}
       body: {avatar_video_path, sample_rate, target_fps, wav_duration_ms}
  POST /v1/sessions/{session_id}/stop   → 200 {"ok": true}   (best-effort)

WebSocket  ws://host/v1/sessions/{session_id}/stream
  上行 (client → server)，每条 binary：
    struct ">q" wrapped_pts_ms (int64 BE) ‖ pcm_s16le_mono_bytes
    wrapped_pts 已在客户端按 wav_duration_ms 取模（落在 [0, wav_duration_ms)）。

  下行 (server → client)，每条 binary：
    struct ">qiiiii" wrapped_pts, server_idx, crop_x, crop_y, crop_w, crop_h
                                                              ‖ rgb_bytes
    rgb_bytes 长度严格 = crop_w * crop_h * 3，C-order H×W×3 uint8。
    wrapped_pts 原样回写即可（客户端 _restore_monotonic_pts 负责还原单调）。

────────────────────────────────────────────────────────────────────────────
依赖 / 启动
────────────────────────────────────────────────────────────────────────────
  pip install "fastapi[standard]>=0.135" numpy loguru

方式 A（仓库根，namespace package）：
  cd Aiszr
  uvicorn deploy.heygem.server:app --host 0.0.0.0 --port 8770

方式 B（独立目录）：
  cd Aiszr/deploy/heygem
  uvicorn server:app --host 0.0.0.0 --port 8770

Aiszr 端：
  Windows : set AISZR_HEYGEM_URL=http://localhost:8770
  *nix    : export AISZR_HEYGEM_URL=http://localhost:8770

────────────────────────────────────────────────────────────────────────────
线程模型
────────────────────────────────────────────────────────────────────────────
- 每个 session 独占 `ThreadPoolExecutor(max_workers=1)`：HeyGem CUDA/TRT context
  必须绑死在创建它的线程，单线程序列化跑 inference 既安全又满足 back-pressure
  需求（WS reader await 在 run_in_executor 上自动止流）。
- HTTP `start` slot 阻塞至 backend warmup 完毕再返回，client.py 的 start()
  返回后立即拉 WS，确保第一条音频不会丢。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import struct
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field

# ── Docker 后端配置 ─────────────────────────────────────────────────────────
# Docker 容器从 Windows host 可达的地址（localhost:映射端口）
DOCKER_HEYGEM_URL = os.environ.get("DOCKER_HEYGEM_URL", "http://127.0.0.1:8383")
# Windows → Docker 路径映射（volume mount prefix）
# 例：d:/duix_avatar_data/face2face:/code/data
#   WIN_DATA_PREFIX = "d:/duix_avatar_data/face2face"
#   DOCKER_DATA_PREFIX = "/code/data"
WIN_DATA_PREFIX = os.environ.get("WIN_DATA_PREFIX", "d:/duix_avatar_data/face2face").replace("\\", "/")
DOCKER_DATA_PREFIX = os.environ.get("DOCKER_DATA_PREFIX", "/code/data")


# ── 协议常量（client.py 镜像）─────────────────────────────────────────────
HEAD_UP_FMT = ">q"
HEAD_UP_SIZE = struct.calcsize(HEAD_UP_FMT)
HEAD_DOWN_FMT = ">qiiiii"
HEAD_DOWN_SIZE = struct.calcsize(HEAD_DOWN_FMT)

# Mock 推理：固定 crop 位置 + 尺寸（实接 HeyGem 后由 backend 决定）
MOCK_CROP_W = 160
MOCK_CROP_H = 96
MOCK_CROP_X = 220
MOCK_CROP_Y = 540


# ── Schemas ────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    avatar_video_path: str
    sample_rate: int = 24000
    target_fps: int = 25
    wav_duration_ms: int = Field(..., gt=0)
    wav_path: str = ""


class StartResponse(BaseModel):
    session_id: str


# ── Docker backend ──────────────────────────────────────────────────────────

class _DockerBackend:
    """Per-session proxy to Docker container's streaming inference endpoint.

    Thread-safety: all methods called from session's ThreadPoolExecutor
    (single worker), so no locking needed internally.
    """

    def __init__(self, avatar_video_path: str, sample_rate: int, target_fps: int,
                 wav_path: str = "", wav_duration_ms: int = 0) -> None:
        import requests
        import shutil
        self._requests = requests
        self._base = DOCKER_HEYGEM_URL.rstrip("/")

        dest_dir = os.path.join(WIN_DATA_PREFIX, "temp")
        os.makedirs(dest_dir, exist_ok=True)

        logger.info("_DockerBackend wav_path={} exists={}", wav_path,
                    os.path.isfile(wav_path) if wav_path else "N/A")

        # Avatar: translate or copy to Docker mount
        avatar_win = avatar_video_path.replace("\\", "/")
        if WIN_DATA_PREFIX and avatar_win.lower().startswith(WIN_DATA_PREFIX.lower()):
            docker_avatar = DOCKER_DATA_PREFIX + avatar_win[len(WIN_DATA_PREFIX):]
        else:
            name = os.path.basename(avatar_video_path)
            shutil.copy2(avatar_video_path, os.path.join(dest_dir, name))
            docker_avatar = DOCKER_DATA_PREFIX + "/temp/" + name

        # WAV: translate or copy to Docker mount
        docker_wav_path = ""
        if wav_path and os.path.isfile(wav_path):
            wav_win = wav_path.replace("\\", "/")
            if WIN_DATA_PREFIX and wav_win.lower().startswith(WIN_DATA_PREFIX.lower()):
                docker_wav_path = DOCKER_DATA_PREFIX + wav_win[len(WIN_DATA_PREFIX):]
            else:
                name = os.path.basename(wav_path)
                shutil.copy2(wav_path, os.path.join(dest_dir, name))
                docker_wav_path = DOCKER_DATA_PREFIX + "/temp/" + name

        logger.info("_DockerBackend docker_avatar={} docker_wav_path={}",
                    docker_avatar, docker_wav_path)

        resp = requests.post(
            f"{self._base}/aiszr/stream/start",
            json={
                "avatar_video_path": docker_avatar,
                "sample_rate": sample_rate,
                "target_fps": target_fps,
                "wav_path": docker_wav_path,
            },
            timeout=60,
        )
        logger.info("Docker /aiszr/stream/start status={} body={}", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        self._docker_sid: str = data["session_id"]
        self.crop_x: int = data["crop_x"]
        self.crop_y: int = data["crop_y"]
        self.crop_w: int = data["crop_w"]
        self.crop_h: int = data["crop_h"]
        logger.info(
            "Docker backend started sid={} crop=({},{},{},{})",
            self._docker_sid, self.crop_x, self.crop_y, self.crop_w, self.crop_h,
        )

    def run_chunk(self, pcm: bytes, pts_ms: int) -> list[tuple[np.ndarray, int, int, int, int]]:
        """Send PCM to Docker, receive N mouth crop RGB frames back."""
        resp = self._requests.post(
            f"{self._base}/aiszr/stream/{self._docker_sid}/infer",
            data=pcm,
            headers={"Content-Type": "application/octet-stream"},
            timeout=30,
        )
        resp.raise_for_status()
        rgb_bytes = resp.content
        frame_size = self.crop_w * self.crop_h * 3
        if len(rgb_bytes) == 0 or len(rgb_bytes) % frame_size != 0:
            raise ValueError(
                f"Docker returned {len(rgb_bytes)} bytes, not a multiple of {frame_size}"
            )
        n_frames = len(rgb_bytes) // frame_size
        results = []
        for i in range(n_frames):
            start = i * frame_size
            mouth = np.frombuffer(
                rgb_bytes[start:start + frame_size], dtype=np.uint8
            ).reshape(self.crop_h, self.crop_w, 3).copy()
            results.append((mouth, self.crop_x, self.crop_y, self.crop_w, self.crop_h))
        return results

    def close(self) -> None:
        try:
            self._requests.post(
                f"{self._base}/aiszr/stream/{self._docker_sid}/stop",
                timeout=5,
            )
        except Exception:
            logger.exception("Docker stream stop failed for sid={}", self._docker_sid)


# ── Session state ──────────────────────────────────────────────────────────

@dataclass
class SessionState:
    avatar_video_path: str
    sample_rate: int
    target_fps: int
    wav_duration_ms: int
    frame_counter: int = 0
    backend: Optional[object] = None  # 实部署时塞 HeyGem runtime 句柄
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="heygem-inf"
        )
    )


SESSIONS: dict[str, SessionState] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("HeyGem adapter starting")
    yield
    for sid, state in list(SESSIONS.items()):
        _shutdown_session(state)
        SESSIONS.pop(sid, None)
    logger.info("HeyGem adapter shutdown clean")


app = FastAPI(title="HeyGem realtime adapter for Aiszr", lifespan=lifespan)


# ── HTTP control plane ─────────────────────────────────────────────────────

@app.get("/v1/health")
def health() -> dict:
    return {"ok": True, "active_sessions": len(SESSIONS)}


@app.post("/v1/sessions/start", response_model=StartResponse)
def start_session(req: StartRequest) -> StartResponse:
    if not Path(req.avatar_video_path).exists():
        raise HTTPException(
            status_code=400,
            detail=f"avatar_video_path 不存在: {req.avatar_video_path}",
        )

    session_id = uuid.uuid4().hex
    state = SessionState(
        avatar_video_path=req.avatar_video_path,
        sample_rate=req.sample_rate,
        target_fps=req.target_fps,
        wav_duration_ms=req.wav_duration_ms,
    )

    # === HeyGem 真实部署点 1：起 runtime + 加载 avatar + warmup ============
    future = state.executor.submit(
        _DockerBackend, req.avatar_video_path, req.sample_rate, req.target_fps,
        req.wav_path, req.wav_duration_ms,
    )
    try:
        state.backend = future.result(timeout=60)
    except Exception as exc:
        logger.exception("Docker backend 启动失败")
        raise HTTPException(status_code=502, detail=f"HeyGem backend 启动失败: {exc}")
    # ======================================================================

    SESSIONS[session_id] = state
    logger.info(
        "session start id={} avatar={} wav_ms={}",
        session_id[:8],
        Path(req.avatar_video_path).name,
        req.wav_duration_ms,
    )
    return StartResponse(session_id=session_id)


@app.post("/v1/sessions/{session_id}/stop")
def stop_session(session_id: str) -> dict:
    state = SESSIONS.pop(session_id, None)
    if state is None:
        return {"ok": True, "noop": True}
    _shutdown_session(state)
    logger.info("session stopped id={}", session_id[:8])
    return {"ok": True}


def _shutdown_session(state: SessionState) -> None:
    # === HeyGem 真实部署点 2：在 inference 线程内释放 CUDA / TRT context ==
    if state.backend is not None:
        try:
            state.executor.submit(state.backend.close).result(timeout=5)
        except Exception:
            logger.exception("backend close 异常")
    # ======================================================================
    try:
        state.executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        logger.exception("executor shutdown 异常")


# ── WebSocket data plane ───────────────────────────────────────────────────

@app.websocket("/v1/sessions/{session_id}/stream")
async def realtime_stream(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    state = SESSIONS.get(session_id)
    if state is None:
        await ws.close(code=4404, reason="session not found")
        logger.warning("WS rejected: unknown session id={}", session_id[:8])
        return

    logger.info("WS connected session={}", session_id[:8])
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(maxsize=1)
    stop_event = asyncio.Event()

    async def receiver() -> None:
        ws_recv_count = 0
        try:
            while not stop_event.is_set():
                msg = await ws.receive_bytes()
                if len(msg) < HEAD_UP_SIZE:
                    continue
                ws_recv_count += 1
                (wrapped_pts,) = struct.unpack(HEAD_UP_FMT, msg[:HEAD_UP_SIZE])
                pcm = msg[HEAD_UP_SIZE:]
                if not pcm:
                    continue

                if audio_queue.full():
                    with suppress(asyncio.QueueEmpty):
                        audio_queue.get_nowait()
                        audio_queue.task_done()
                with suppress(asyncio.QueueFull):
                    audio_queue.put_nowait((int(wrapped_pts), pcm))

                if ws_recv_count == 1:
                    logger.info("WS first chunk received session={}", session_id[:8])
        except WebSocketDisconnect:
            logger.info("WS disconnected session={}", session_id[:8])
        except Exception:
            logger.exception("WS receiver crashed session={}", session_id[:8])
        finally:
            stop_event.set()

    async def processor() -> None:
        frame_step_ms = max(1, int(1000 / max(state.target_fps, 1)))
        while not stop_event.is_set():
            try:
                wrapped_pts, pcm = await asyncio.wait_for(audio_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue

            try:
                try:
                    result = await loop.run_in_executor(
                        state.executor, _heygem_inference, state, pcm, wrapped_pts
                    )
                except RuntimeError as exc:
                    logger.info("executor closed mid-stream session={}: {}",
                                session_id[:8], exc)
                    stop_event.set()
                    break
                except Exception:
                    logger.exception("inference crashed pts={}", wrapped_pts)
                    continue

                if result is None:
                    logger.warning("inference returned None, pts={}", wrapped_pts)
                    continue

                for fi, (mouth_rgb, cx, cy, cw, ch) in enumerate(result):
                    if mouth_rgb.shape != (ch, cw, 3) or mouth_rgb.dtype != np.uint8:
                        logger.error(
                            "frame {} shape/dtype mismatch: {} {} expected ({},{},3) uint8",
                            fi, mouth_rgb.shape, mouth_rgb.dtype, ch, cw,
                        )
                        continue
                    if not mouth_rgb.flags["C_CONTIGUOUS"]:
                        mouth_rgb = np.ascontiguousarray(mouth_rgb)

                    state.frame_counter += 1
                    frame_pts = (wrapped_pts + fi * frame_step_ms) % state.wav_duration_ms
                    head = struct.pack(
                        HEAD_DOWN_FMT,
                        int(frame_pts),
                        state.frame_counter,
                        int(cx), int(cy), int(cw), int(ch),
                    )
                    await ws.send_bytes(head + mouth_rgb.tobytes())

                if state.frame_counter <= len(result) * 2:
                    logger.info("WS sent {} frames, pts {}..{}, session={}",
                                len(result), wrapped_pts,
                                (wrapped_pts + (len(result) - 1) * frame_step_ms) % state.wav_duration_ms,
                                session_id[:8])
            except WebSocketDisconnect:
                logger.info("WS disconnected while sending session={}", session_id[:8])
                stop_event.set()
                break
            finally:
                audio_queue.task_done()

    try:
        receiver_task = asyncio.create_task(receiver())
        processor_task = asyncio.create_task(processor())
        done, pending = await asyncio.wait(
            {receiver_task, processor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_event.set()
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            with suppress(asyncio.CancelledError):
                task.result()
    except WebSocketDisconnect:
        logger.info("WS disconnected session={}", session_id[:8])
    except Exception:
        logger.exception("WS handler crashed session={}", session_id[:8])


# ── Inference 占位 ─────────────────────────────────────────────────────────

def _heygem_inference(
    state: SessionState, pcm: bytes, wrapped_pts_ms: int
) -> Optional[list[tuple[np.ndarray, int, int, int, int]]]:
    """Batch inference: returns list of (mouth_rgb, crop_x, crop_y, crop_w, crop_h)."""
    if not pcm:
        return None
    if state.backend is None:
        logger.warning("_heygem_inference: backend is None!")
        return None
    try:
        result = state.backend.run_chunk(pcm, wrapped_pts_ms)
    except Exception as exc:
        logger.exception("_heygem_inference run_chunk FAILED: {}", exc)
        raise
    if state.frame_counter < 3:
        logger.info("_heygem_inference got {} frames", len(result))
    return result
