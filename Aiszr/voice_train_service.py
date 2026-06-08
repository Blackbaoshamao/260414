"""voice_train_service.py — GPT-SoVITS one-click training pipeline."""
from __future__ import annotations

import asyncio
import os
import shutil
import wave
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from loguru import logger


@dataclass
class TrainProgress:
    step: str = ""
    percent: int = 0
    message: str = ""


class VoiceTrainService:
    """One-click GPT-SoVITS voice training pipeline."""

    def __init__(
        self,
        gpt_sovits_root: str | Path,
        python_exe: str | Path,
        progress_callback: Callable[[TrainProgress], None] | None = None,
    ):
        self.root = Path(gpt_sovits_root)
        self.python = str(python_exe)
        self._on_progress = progress_callback or (lambda p: None)

    def _emit(self, step: str, percent: int, message: str) -> None:
        self._on_progress(TrainProgress(step, percent, message))

    @staticmethod
    def validate_audio_files(paths: list[str | Path]) -> tuple[bool, str, float]:
        """校验音频文件列表，返回 (ok, message, total_seconds)。"""
        total = 0.0
        for p in paths:
            p = Path(p)
            if not p.is_file():
                return False, f"文件不存在：{p}", 0.0
            if p.suffix.lower() not in (".wav", ".mp3", ".flac"):
                return False, f"不支持的格式：{p.suffix}", 0.0
            try:
                if p.suffix.lower() == ".wav":
                    with wave.open(str(p), "rb") as wf:
                        total += wf.getnframes() / max(wf.getframerate(), 1)
                else:
                    total += 0.0
            except Exception as e:
                return False, f"读取失败 {p.name}: {e}", 0.0
        if total < 3.0:
            return False, "音频总时长不足 3 秒，建议至少 10 秒以上", total
        if total < 10.0:
            return True, f"音频总时长 {total:.1f}s，建议上传更长的音频以获得更好效果", total
        return True, f"音频总时长 {total:.1f}s", total

    async def slice_audio(
        self,
        input_files: list[str | Path],
        output_dir: str | Path,
    ) -> list[Path]:
        """将输入音频文件切片为短段。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._emit("slice", 0, "正在切片音频...")
        all_sliced: list[Path] = []
        total = len(input_files)
        for idx, fp in enumerate(input_files):
            fp = Path(fp)
            # slice_audio.py takes 11 positional args:
            # inp, opt_root, threshold, min_length, min_interval, hop_size, max_sil_kept, _max, alpha, i_part, all_part
            cmd = [
                self.python,
                str(self.root / "tools" / "slice_audio.py"),
                str(fp),                     # inp
                str(output_dir),             # opt_root
                "-34",                       # threshold
                "4000",                      # min_length
                "300",                       # min_interval
                "10",                        # hop_size
                "500",                       # max_sil_kept
                "0.9",                       # _max
                "0.25",                      # alpha
                "0",                         # i_part
                "1",                         # all_part
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("slice_audio stderr: {}", stderr.decode(errors="replace"))
            for f in sorted(output_dir.glob("*.wav")):
                if f not in all_sliced:
                    all_sliced.append(f)
            self._emit("slice", int((idx + 1) / total * 100), f"切片 {idx+1}/{total}")
        if not all_sliced:
            for fp in input_files:
                dest = output_dir / Path(fp).name
                shutil.copy2(Path(fp), dest)
                all_sliced.append(dest)
            self._emit("slice", 100, "使用原始音频（无需切片）")
        else:
            self._emit("slice", 100, f"切片完成，产出 {len(all_sliced)} 段")
        return all_sliced

    async def run_asr(
        self,
        wav_dir: str | Path,
        output_dir: str | Path,
        language: str = "zh",
    ) -> Path:
        """对切片后的音频运行 ASR 语音识别，返回 .list 标注文件路径。"""
        wav_dir = Path(wav_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._emit("asr", 0, "正在进行语音识别...")
        asr_script = self.root / "tools" / "asr" / "fasterwhisper_asr.py"
        if not asr_script.is_file():
            asr_script = self.root / "tools" / "asr" / "funasr_asr.py"
        if not asr_script.is_file():
            raise FileNotFoundError("找不到 ASR 脚本")
        cmd = [
            self.python,
            str(asr_script),
            "-i", str(wav_dir),
            "-o", str(output_dir),
            "-l", language,
            "-s", "large-v3",
            "-p", "float16",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"ASR 失败：{err}")
        # ASR outputs to {output_dir}/{basename(wav_dir)}.list
        expected_list = output_dir / f"{wav_dir.name}.list"
        if not expected_list.is_file():
            # fallback: find any .list file in output_dir
            lists = list(output_dir.glob("*.list"))
            if not lists:
                raise RuntimeError(f"ASR 未产出标注文件，预期：{expected_list}")
            expected_list = lists[0]
        self._emit("asr", 100, "语音识别完成")
        return expected_list
