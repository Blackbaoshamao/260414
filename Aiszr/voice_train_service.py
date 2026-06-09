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


LOW_DATA_VERSION_CANDIDATES = ("v2ProPlus", "v2Pro", "v2")
DEFAULT_LOW_DATA_SOVITS_EPOCHS = 8
DEFAULT_TRAIN_GPT = False


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
        non_wav_count = 0
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
                    non_wav_count += 1
            except Exception as e:
                return False, f"读取失败 {p.name}: {e}", 0.0
        if total < 3.0 and non_wav_count == 0:
            return False, "音频总时长不足 3 秒，建议至少 10 秒以上", total
        parts = []
        if total > 0:
            parts.append(f"WAV 总时长 {total:.1f}s")
        if non_wav_count > 0:
            parts.append(f"{non_wav_count} 个音频文件")
        label = "，".join(parts)
        if total < 10.0 and non_wav_count == 0:
            return True, f"{label}，建议上传更长的音频以获得更好效果", total
        return True, f"音频总时长约 {total:.0f}s", total

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
                cwd=str(self.root),
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
            "-s", "medium",
            "-p", "float16",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root),
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

        # 清除旧的特征提取输出，防止残留数据污染
        for old in [
            exp_dir / "2-name2text.txt",
            exp_dir / "2-name2text-0.txt",
            exp_dir / "6-name2semantic.tsv",
            exp_dir / "6-name2semantic-0.tsv",
        ]:
            if old.is_file():
                old.unlink()
        for old_dir in [exp_dir / "3-bert", exp_dir / "4-cnhubert", exp_dir / "5-wav32k", exp_dir / "7-sv_cn"]:
            if old_dir.is_dir():
                shutil.rmtree(old_dir)

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
        ]
        if "Pro" in version:
            steps.append(("特征提取-SV", "GPT_SoVITS/prepare_datasets/2-get-sv.py", {
                "inp_text": str(asr_list_path),
                "inp_wav_dir": str(wav_dir),
                "exp_name": exp_dir.name,
                "i_part": "0",
                "all_parts": "1",
                "opt_dir": str(exp_dir),
                "sv_path": str(pretrained / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt"),
                "is_half": "True",
                "_CUDA_VISIBLE_DEVICES": "0",
            }))
        steps.append(
            ("特征提取-语义", "GPT_SoVITS/prepare_datasets/3-get-semantic.py", {
                "inp_text": str(asr_list_path),
                "exp_name": exp_dir.name,
                "i_part": "0",
                "all_parts": "1",
                "opt_dir": str(exp_dir),
                "pretrained_s2G": self._pretrained_s2g_path(version),
                "s2config_path": str(self._s2_config_path(version)),
                "is_half": "True",
            })
        )
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
                cwd=str(self.root),
                env=env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")
                raise RuntimeError(f"{label} 失败：{err}")
            self._emit("extract", int((idx + 1) / total * 100), f"{label} 完成")

        # 合并分片输出（all_parts=1 时只需合并 part 0）
        self._merge_part_file(exp_dir, "2-name2text", ".txt")
        self._merge_part_file(exp_dir, "6-name2semantic", ".tsv", header="item_name\tsemantic_audio")

        # 数据一致性：移除 semantic 中不在 phoneme 里的条目
        valid_count = self._ensure_data_consistency(exp_dir)
        if valid_count == 0:
            raise RuntimeError("特征提取后无有效训练数据：semantic 和 phoneme 数据完全无匹配")
        self._emit("extract", 100, f"特征提取完成，{valid_count} 条有效数据")

    async def train_model(
        self,
        exp_dir: str | Path,
        version: str = "auto",
        gpt_epochs: int = 0,
        sovits_epochs: int | None = None,
        train_gpt: bool = DEFAULT_TRAIN_GPT,
    ) -> dict[str, str]:
        """运行 SoVITS 训练；低数据默认跳过 GPT 训练，避免语义模型崩坏。"""
        import json as json_mod

        exp_dir = Path(exp_dir)
        version = self.resolve_training_version(version)
        if sovits_epochs is None:
            sovits_epochs = self.default_sovits_epochs(version)
        results: dict[str, str] = {}
        results["version"] = version

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
        env["CUDA_VISIBLE_DEVICES"] = "0"
        env["MASTER_ADDR"] = "127.0.0.1"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root),
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"SoVITS 训练失败：{err}")
        self._emit("train_sovits", 50, "SoVITS 训练完成")

        if train_gpt and gpt_epochs > 0:
            self._emit("train_gpt", 50, "正在训练 GPT 模型...")
            import yaml as yaml_mod
            s1_config = self._build_s1_config(exp_dir, version, gpt_epochs)
            s1_config_path = exp_dir / "tmp_s1.yaml"
            s1_config_path.write_text(yaml_mod.dump(s1_config, allow_unicode=True, default_flow_style=False))
            cmd = [
                self.python,
                str(self.root / "GPT_SoVITS" / "s1_train.py"),
                "-c", str(s1_config_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
                env=env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")
                raise RuntimeError(f"GPT 训练失败：{err}")
            self._emit("train_gpt", 100, "GPT 训练完成")
        else:
            self._emit("train_gpt", 100, "低数据质量优先：跳过 GPT 训练，使用预训练语义模型")

        # 查找最新产出的模型权重
        # SoVITS: 优先找 savee 保存的带 config 的权重，其次找 G_*.pth 并转换
        sovits_ckpt = self._find_savee_weights(exp_dir, "s2") or self._find_trained_weights(exp_dir, "logs_s2", ".pth", prefix="G_")
        if sovits_ckpt:
            self._inject_sovits_config(sovits_ckpt, s2_config)
            results["sovits_ckpt"] = str(sovits_ckpt)
        if train_gpt and gpt_epochs > 0:
            # GPT: 优先找 savee 保存的带 config 的权重（exp_dir/{name}-e*.ckpt），其次找 PL checkpoint 并转换
            gpt_ckpt = self._find_savee_weights(exp_dir, "s1") or self._find_trained_weights(exp_dir, "logs_s1", ".ckpt")
            if gpt_ckpt:
                self._inject_gpt_config(gpt_ckpt, s1_config)
                results["gpt_ckpt"] = str(gpt_ckpt)
        else:
            results["gpt_ckpt"] = self._pretrained_s1_path(version)
        return results

    def _pretrained_s2g_path(self, version: str) -> str:
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"
        mapping = {
            "v1": "s2G488k.pth",
            "v2": "gsv-v2final-pretrained/s2G2333k.pth",
            "v2Pro": "v2Pro/s2Gv2Pro.pth",
            "v2ProPlus": "v2Pro/s2Gv2ProPlus.pth",
            "v3": "s2Gv3.pth",
        }
        name = mapping.get(version, "s2G488k.pth")
        return str(pretrained / name)

    def _pretrained_s2d_path(self, version: str) -> str:
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"
        mapping = {
            "v1": "s2D488k.pth",
            "v2": "gsv-v2final-pretrained/s2D2333k.pth",
            "v2Pro": "v2Pro/s2Dv2Pro.pth",
            "v2ProPlus": "v2Pro/s2Dv2ProPlus.pth",
            "v3": "s2Dv3.pth",
        }
        name = mapping.get(version, "s2D488k.pth")
        return str(pretrained / name)

    def _pretrained_s1_path(self, version: str) -> str:
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"
        mapping = {
            "v1": "s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
            "v2": "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
            "v2Pro": "s1v3.ckpt",
            "v2ProPlus": "s1v3.ckpt",
            "v3": "s1v3.ckpt",
            "v4": "s1v3.ckpt",
        }
        name = mapping.get(version, mapping["v2"])
        return str(pretrained / name)

    def resolve_training_version(self, requested: str = "auto") -> str:
        requested = str(requested or "auto").strip()
        if requested and requested.lower() != "auto":
            return requested
        for candidate in LOW_DATA_VERSION_CANDIDATES:
            if Path(self._pretrained_s2g_path(candidate)).is_file() and Path(self._pretrained_s2d_path(candidate)).is_file():
                return candidate
        return "v2"

    @staticmethod
    def default_sovits_epochs(version: str) -> int:
        return 2 if version in {"v3", "v4"} else DEFAULT_LOW_DATA_SOVITS_EPOCHS

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
        cfg["train"]["gpu_numbers"] = "0"
        cfg["train"]["batch_size"] = 4
        cfg["train"]["pretrained_s2G"] = str(self._pretrained_s2g_path(version))
        cfg["train"]["pretrained_s2D"] = str(self._pretrained_s2d_path(version))
        cfg["train"]["save_every_epoch"] = 1
        cfg["train"]["if_save_latest"] = 0
        cfg["train"]["if_save_every_weights"] = 1
        cfg["save_weight_dir"] = str(exp_dir)
        cfg["s2_ckpt_dir"] = str(exp_dir)
        cfg["name"] = exp_dir.name
        cfg["version"] = version
        cfg.setdefault("data", {})
        cfg["data"]["exp_dir"] = str(exp_dir)
        cfg["data"]["training_files"] = str(exp_dir / "2-name2text.txt")
        cfg["data"]["validation_files"] = str(exp_dir / "2-name2text.txt")
        cfg.setdefault("model", {})
        cfg["model"]["version"] = version
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
        cfg.setdefault("optimizer", {})
        cfg["train"]["epochs"] = epochs
        cfg["train"]["if_save_latest"] = 0
        cfg["train"]["if_save_every_weights"] = 1
        cfg["train"]["half_weights_save_dir"] = str(exp_dir)
        cfg["train"]["exp_name"] = exp_dir.name
        cfg["pretrained_s1"] = self._pretrained_s1_path(version)
        cfg["train_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
        cfg["train_phoneme_path"] = str(exp_dir / "2-name2text.txt")
        cfg["output_dir"] = str(exp_dir / f"logs_s1_{version}")
        for key in ("lr", "lr_init", "lr_end", "warmup_steps", "decay_steps"):
            if key in cfg["optimizer"]:
                cfg["optimizer"][key] = float(cfg["optimizer"][key])
        return cfg

    @staticmethod
    def _ensure_data_consistency(exp_dir: Path) -> int:
        """移除 6-name2semantic.tsv 中不在 2-name2text.txt 里的条目，返回有效条目数。"""
        phoneme_path = exp_dir / "2-name2text.txt"
        semantic_path = exp_dir / "6-name2semantic.tsv"
        if not phoneme_path.is_file() or not semantic_path.is_file():
            return 0
        keys: set[str] = set()
        with open(phoneme_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split("\t")
                if parts:
                    keys.add(parts[0].strip())
        with open(semantic_path, "r", encoding="utf-8") as f:
            header = f.readline()
            lines = f.readlines()
        kept = [l for l in lines if l.split("\t")[0].strip() in keys]
        with open(semantic_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.writelines(kept)
        return len(kept)

    @staticmethod
    def _pick_ref_from_sliced(sliced_files: list[Path], asr_list: Path | None = None) -> Path:
        """从切片中选一个稳定的 3~10 秒片段作为推理参考音频。"""
        import wave
        candidates: list[tuple[float, Path]] = []
        for f in sliced_files:
            try:
                with wave.open(str(f), "r") as wf:
                    duration = wf.getnframes() / wf.getframerate()
            except Exception:
                continue
            if not 3 <= duration <= 10:
                continue
            text = ""
            if asr_list is not None:
                text, _ = VoiceTrainService._read_asr_text(asr_list, f.name)
            score = VoiceTrainService._score_ref_candidate(duration, text)
            candidates.append((score, f))
        if candidates:
            return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
        sorted_files = sorted(sliced_files, key=lambda p: p.stat().st_size, reverse=True)
        return sorted_files[len(sorted_files) // 2] if sorted_files else sorted_files[0]

    @staticmethod
    def _score_ref_candidate(duration: float, text: str) -> float:
        score = 100.0 - abs(duration - 5.5) * 8.0
        if 4.0 <= duration <= 8.0:
            score += 12.0
        stripped = (text or "").strip()
        if stripped:
            text_len = len(stripped)
            score -= abs(text_len - 28) * 0.6
            score -= sum(ch.isdigit() for ch in stripped) * 2.0
            score -= sum(stripped.count(ch) for ch in "!?？！") * 1.5
            if text_len < 10 or text_len > 60:
                score -= 12.0
        return score

    @staticmethod
    def _read_asr_text(asr_list: Path, wav_name: str) -> tuple[str, str]:
        """从 ASR 标注文件中查找指定音频的文本和语言。"""
        if not asr_list.is_file():
            return "", ""
        stem = Path(wav_name).stem
        with open(asr_list, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 4 and Path(parts[0]).stem == stem:
                    # ASR .list 格式: filepath|speaker|language|text
                    return parts[3], parts[2].lower()
        return "", ""

    @staticmethod
    def _inject_sovits_config(ckpt_path: Path, config: dict) -> None:
        """将 SoVITS checkpoint 转换为 API 加载所需的格式。
        save_checkpoint 保存 {"model": state_dict, ...}，
        API 期望 {"config": ..., "weight": state_dict}。
        """
        if VoiceTrainService._has_gpt_sovits_version_header(ckpt_path):
            return
        import torch
        data = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
        if "weight" in data and "config" in data:
            return
        converted: dict = {}
        converted["weight"] = data.get("model") or data.get("weight")
        converted["config"] = config
        converted["info"] = data.get("info", f"epoch_{data.get('iteration', '?')}")
        torch.save(converted, str(ckpt_path))

    @staticmethod
    def _has_gpt_sovits_version_header(ckpt_path: Path) -> bool:
        """GPT-SoVITS Pro/LoRA weights store the model version in the first 2 bytes."""
        try:
            with open(ckpt_path, "rb") as f:
                return f.read(2) in {b"03", b"04", b"05", b"06"}
        except Exception:
            return False

    @staticmethod
    def _inject_gpt_config(ckpt_path: Path, config: dict) -> None:
        """将 GPT checkpoint 转换为 API 加载所需的格式。
        pytorch_lightning 保存的 checkpoint 格式不同，
        API 期望 {"config": ..., "weight": state_dict}。
        """
        import torch
        data = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
        if "weight" in data and "config" in data:
            return
        state_dict = data.get("state_dict") or data.get("weight") or {}
        converted: dict = {}
        converted["weight"] = {k: v.half() if hasattr(v, "half") else v for k, v in state_dict.items()}
        converted["config"] = config
        converted["info"] = data.get("info", "GPT fine-tuned")
        torch.save(converted, str(ckpt_path))

    @staticmethod
    def _find_savee_weights(exp_dir: Path, stage: str) -> Path | None:
        """找 savee 保存的带 config 的权重文件（格式正确，API 可直接加载）。"""
        name = exp_dir.name
        if stage == "s1":
            # savee 保存到 half_weights_save_dir = exp_dir，文件名 {name}-e{epoch}.ckpt
            files = sorted(exp_dir.glob(f"{name}-e*.ckpt"), key=lambda f: f.stat().st_mtime, reverse=True)
        else:
            # s2: savee 保存到 save_weight_dir，文件名 {name}_e{epoch}_s{step}.pth
            files = sorted(exp_dir.glob(f"{name}_e*_s*.pth"), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None

    @staticmethod
    def _find_trained_weights(exp_dir: Path, log_subdir: str, suffix: str, prefix: str = "") -> Path | None:
        weight_dir = exp_dir / log_subdir
        if not weight_dir.is_dir():
            for d in exp_dir.iterdir():
                if d.is_dir() and d.name.startswith(log_subdir):
                    weight_dir = d
                    break
        if not weight_dir.is_dir():
            return None
        files = sorted(
            (f for f in weight_dir.rglob(f"*{suffix}") if not prefix or f.name.startswith(prefix)),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        return files[0] if files else None

    @staticmethod
    def _merge_part_file(exp_dir: Path, prefix: str, ext: str, header: str | None = None) -> None:
        """合并分片输出文件（如 2-name2text-0.txt → 2-name2text.txt）。"""
        part_path = exp_dir / f"{prefix}-0{ext}"
        merged_path = exp_dir / f"{prefix}{ext}"
        if not part_path.is_file():
            return
        with open(part_path, "r", encoding="utf-8") as f:
            lines = f.read().strip("\n").split("\n")
        parts = [lines]
        part_path.unlink()
        opt = []
        for chunk in parts:
            opt.extend(chunk)
        with open(merged_path, "w", encoding="utf-8") as f:
            if header:
                f.write(header + "\n")
            f.write("\n".join(opt) + "\n")

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
        version: str = "auto",
        gpt_epochs: int = 0,
        sovits_epochs: int | None = None,
        train_gpt: bool = DEFAULT_TRAIN_GPT,
        output_base_dir: str | Path | None = None,
    ) -> dict[str, str]:
        """一键完整训练管线。返回 {gpt_ckpt, sovits_ckpt, model_dir}。"""
        version = self.resolve_training_version(version)
        if sovits_epochs is None:
            sovits_epochs = self.default_sovits_epochs(version)
        if output_base_dir is None:
            output_base_dir = self.root / "trained_models"
        output_base_dir = Path(output_base_dir)
        exp_dir = output_base_dir / voice_name
        if exp_dir.exists():
            shutil.rmtree(exp_dir)
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
        results = await self.train_model(
            exp_dir,
            version,
            gpt_epochs,
            sovits_epochs,
            train_gpt=train_gpt,
        )
        results["model_dir"] = str(exp_dir)
        results["version"] = version
        # 参考音频：选一个时长在 3~10 秒的切片，同时查找对应文本
        if sliced_files:
            ref = self._pick_ref_from_sliced(sliced_files, asr_list)
            results["ref_audio"] = str(ref)
            ref_text, ref_lang = self._read_asr_text(asr_list, ref.name)
            if ref_text:
                results["prompt_text"] = ref_text
                results["prompt_lang"] = ref_lang
        return results
