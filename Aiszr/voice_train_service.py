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

    async def extract_features(
        self,
        asr_list_path: str | Path,
        wav_dir: str | Path,
        exp_dir: str | Path,
        version: str = "v2",
    ) -> None:
        """一键三连：文本提取 + HuBERT特征 + 语义特征。通过环境变量传参。"""
        exp_dir = Path(exp_dir)
        asr_list_path = Path(asr_list_path)
        wav_dir = Path(wav_dir)
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"

        steps = [
            ("特征提取-文本", "GPT_SoVITS/prepare_datasets/1-get-text.py", {
                "inp_text": str(asr_list_path),
                "inp_wav_dir": str(wav_dir),
                "exp_name": exp_dir.name,
                "i_part": "0",
                "all_parts": "1",
                "opt_dir": str(exp_dir),
                "bert_pretrained_dir": str(pretrained / "chinese-roberta-wwm-ext-large"),
                "is_half": "True",
                "version": version,
            }),
            ("特征提取-HuBERT", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py", {
                "inp_text": str(asr_list_path),
                "inp_wav_dir": str(wav_dir),
                "exp_name": exp_dir.name,
                "i_part": "0",
                "all_parts": "1",
                "opt_dir": str(exp_dir),
                "cnhubert_base_dir": str(pretrained / "chinese-hubert-base"),
                "is_half": "True",
            }),
            ("特征提取-语义", "GPT_SoVITS/prepare_datasets/3-get-semantic.py", {
                "inp_text": str(asr_list_path),
                "exp_name": exp_dir.name,
                "i_part": "0",
                "all_parts": "1",
                "opt_dir": str(exp_dir),
                "pretrained_s2G": self._pretrained_s2g_path(version),
                "s2config_path": str(self._s2_config_path(version)),
                "is_half": "True",
            }),
        ]
        total = len(steps)
        for idx, (label, script_rel, extra_env) in enumerate(steps):
            self._emit("extract", int(idx / total * 100), f"{label}...")
            script_path = self.root / script_rel
            if not script_path.is_file():
                raise FileNotFoundError(f"脚本不存在：{script_path}")
            env = os.environ.copy()
            env.update(extra_env)
            cmd = [self.python, str(script_path)]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root / "GPT_SoVITS"),
                env=env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")
                raise RuntimeError(f"{label} 失败：{err}")
            self._emit("extract", int((idx + 1) / total * 100), f"{label} 完成")

    async def train_model(
        self,
        exp_dir: str | Path,
        version: str = "v2",
        gpt_epochs: int = 15,
        sovits_epochs: int = 8,
    ) -> dict[str, str]:
        """运行 SoVITS + GPT 训练，返回 {gpt_ckpt, sovits_ckpt}。"""
        import json as json_mod

        exp_dir = Path(exp_dir)
        results: dict[str, str] = {}

        # SoVITS training (Stage 2)
        self._emit("train_sovits", 0, "正在训练 SoVITS 模型...")
        s2_config = self._build_s2_config(exp_dir, version, sovits_epochs)
        s2_config_path = exp_dir / "tmp_s2.json"
        s2_config_path.write_text(json_mod.dumps(s2_config, ensure_ascii=False, indent=2))
        cmd = [
            self.python,
            str(self.root / "GPT_SoVITS" / "s2_train.py"),
            "-c", str(s2_config_path),
        ]
        env = os.environ.copy()
        env["_CUDA_VISIBLE_DEVICES"] = "0"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root / "GPT_SoVITS"),
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"SoVITS 训练失败：{err}")
        self._emit("train_sovits", 50, "SoVITS 训练完成")

        # GPT training (Stage 1)
        self._emit("train_gpt", 50, "正在训练 GPT 模型...")
        s1_config = self._build_s1_config(exp_dir, version, gpt_epochs)
        s1_config_path = exp_dir / "tmp_s1.yaml"
        s1_config_path.write_text(json_mod.dumps(s1_config, ensure_ascii=False, indent=2))
        cmd = [
            self.python,
            str(self.root / "GPT_SoVITS" / "s1_train.py"),
            "-c", str(s1_config_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root / "GPT_SoVITS"),
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"GPT 训练失败：{err}")
        self._emit("train_gpt", 100, "GPT 训练完成")

        # 查找最新产出的模型权重
        sovits_ckpt = self._find_trained_weights(exp_dir, "logs_s2", ".pth")
        gpt_ckpt = self._find_trained_weights(exp_dir, "logs_s1", ".ckpt")
        if sovits_ckpt:
            results["sovits_ckpt"] = str(sovits_ckpt)
        if gpt_ckpt:
            results["gpt_ckpt"] = str(gpt_ckpt)
        return results

    def _pretrained_s2g_path(self, version: str) -> str:
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"
        mapping = {
            "v1": "s2G488k.pth",
            "v2": "s2G488k.pth",
            "v2Pro": "v2Pro/s2Gv2Pro.pth",
            "v2ProPlus": "v2Pro/s2Gv2ProPlus.pth",
            "v3": "s2Gv3.pth",
        }
        name = mapping.get(version, "s2G488k.pth")
        return str(pretrained / name)

    def _s2_config_path(self, version: str) -> Path:
        configs = self.root / "GPT_SoVITS" / "configs"
        mapping = {
            "v2Pro": "s2v2Pro.json",
            "v2ProPlus": "s2v2ProPlus.json",
        }
        name = mapping.get(version, "s2.json")
        return configs / name

    def _build_s2_config(self, exp_dir: Path, version: str, epochs: int) -> dict:
        import json as json_mod

        config_path = self._s2_config_path(version)
        if config_path.is_file():
            with open(config_path) as f:
                cfg = json_mod.load(f)
        else:
            cfg = {}
        cfg.setdefault("train", {})
        cfg["train"]["epochs"] = epochs
        cfg["s2_ckpt_dir"] = str(exp_dir / "logs_s2")
        cfg.setdefault("data", {})
        cfg["data"]["training_files"] = str(exp_dir / "2-name2text.txt")
        cfg["data"]["validation_files"] = str(exp_dir / "2-name2text.txt")
        return cfg

    def _build_s1_config(self, exp_dir: Path, version: str, epochs: int) -> dict:
        import yaml

        config_name = "s1longer-v2.yaml" if version != "v1" else "s1longer.yaml"
        config_path = self.root / "GPT_SoVITS" / "configs" / config_name
        if config_path.is_file():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}
        cfg.setdefault("train", {})
        cfg["train"]["epochs"] = epochs
        cfg.setdefault("data", {})
        cfg["data"]["train_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
        cfg["data"]["train_phoneme_path"] = str(exp_dir / "2-name2text.txt")
        cfg["data"]["val_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
        cfg["data"]["val_phoneme_path"] = str(exp_dir / "2-name2text.txt")
        cfg["data"]["exp_dir"] = str(exp_dir / "logs_s1")
        return cfg

    @staticmethod
    def _find_trained_weights(exp_dir: Path, log_subdir: str, suffix: str) -> Path | None:
        weight_dir = exp_dir / log_subdir
        if not weight_dir.is_dir():
            # also check for versioned directories like logs_s2/big2k1
            for d in exp_dir.iterdir():
                if d.is_dir() and d.name.startswith(log_subdir):
                    weight_dir = d
                    break
        if not weight_dir.is_dir():
            return None
        files = sorted(weight_dir.rglob(f"*{suffix}"), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None

    @staticmethod
    def _latest_file(directory: Path, suffix: str) -> Path | None:
        if not directory.is_dir():
            return None
        files = sorted(directory.glob(f"*{suffix}"), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None

    async def run_full_pipeline(
        self,
        input_files: list[str | Path],
        voice_name: str,
        language: str = "zh",
        version: str = "v2",
        gpt_epochs: int = 15,
        sovits_epochs: int = 8,
        output_base_dir: str | Path | None = None,
    ) -> dict[str, str]:
        """一键完整训练管线。返回 {gpt_ckpt, sovits_ckpt, model_dir}。"""
        if output_base_dir is None:
            output_base_dir = self.root / "trained_models"
        output_base_dir = Path(output_base_dir)
        exp_dir = output_base_dir / voice_name
        exp_dir.mkdir(parents=True, exist_ok=True)
        slice_dir = exp_dir / "sliced"
        asr_output_dir = exp_dir / "asr_output"

        # 1. 音频切片
        sliced_files = await self.slice_audio(input_files, slice_dir)

        # 2. ASR
        asr_list = await self.run_asr(slice_dir, asr_output_dir, language)

        # 3. 特征提取
        await self.extract_features(asr_list, slice_dir, exp_dir, version)

        # 4. 训练
        results = await self.train_model(exp_dir, version, gpt_epochs, sovits_epochs)
        results["model_dir"] = str(exp_dir)
        return results
