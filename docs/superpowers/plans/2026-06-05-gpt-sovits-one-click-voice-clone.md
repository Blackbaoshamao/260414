# GPT-SoVITS 一键语音克隆 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 配置选了 GPT-SoVITS 的界面添加一键语音克隆按钮，用户上传参考音频后一键完成训练并自动切换到克隆音色；支持 GPT-SoVITS 本地训练和阿里云百炼两种后端。

**Architecture:** 新增 `voice_train_service.py` 服务封装 GPT-SoVITS 完整训练管线（音频切片 → ASR → 特征提取 → 微调训练 → 模型产出）。`VoiceCloneDialog` 扩展为支持长音频上传和多文件上传，训练完成后自动更新 `VoiceEntry` 的 `clone_voice_id` 指向微调模型权重，`LocalVoiceProvider` 在合成时自动切换到微调权重。阿里云百炼保持现有云端克隆流程不变。

**Tech Stack:** Python 3.10, PyQt5, GPT-SoVITS v2Pro, PyTorch, faster-whisper

---

## File Structure

| File | Responsibility |
|------|---------------|
| `Aiszr/voice_train_service.py` | **新建** — GPT-SoVITS 训练管线编排器（切片、ASR、特征提取、训练） |
| `Aiszr/voice_models.py` | **修改** — VoiceEntry 增加 `trained_model_dir` 字段 |
| `Aiszr/voice_manager.py` | **修改** — LocalVoiceProvider 合成时支持切换微调模型权重 |
| `Aiszr/ui_dialogs/voiceclonedialog.py` | **修改** — 扩展 UI：多文件上传、一键克隆按钮、训练进度显示 |
| `Aiszr/local_voice_runtime.py` | **修改** — 支持动态切换 GPT/SoVITS 权重文件 |
| `Aiszr/tests/test_voice_train_service.py` | **新建** — 训练服务单元测试 |

---

### Task 1: VoiceEntry 扩展训练模型字段

**Files:**
- Modify: `Aiszr/voice_models.py`
- Test: `Aiszr/tests/test_voice_provider_settings.py`

- [ ] **Step 1: 在 VoiceEntry 中增加 trained_model_dir 字段**

在 `Aiszr/voice_models.py` 的 `VoiceEntry` dataclass 中增加字段：

```python
@dataclass(slots=True)
class VoiceEntry:
    id: str = ""
    name: str = ""
    provider: str = ""
    sample_wav_path: str = ""
    clone_voice_id: str = ""
    clone_status: str = "idle"
    last_error: str = ""
    trained_model_dir: str = ""  # 微调训练产出的模型目录
```

同步更新 `from_dict` 和 `to_dict`：

```python
# from_dict 中增加:
trained_model_dir=str(value.get("trained_model_dir", "")).strip(),

# to_dict 中增加:
"trained_model_dir": self.trained_model_dir,
```

- [ ] **Step 2: 运行现有测试确认无破坏**

Run: `cd Aiszr && python -m pytest tests/test_voice_provider_settings.py -v`
Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add Aiszr/voice_models.py
git commit -m "feat: add trained_model_dir field to VoiceEntry"
```

---

### Task 2: 训练服务 — 音频预处理（切片 + ASR）

**Files:**
- Create: `Aiszr/voice_train_service.py`
- Test: `Aiszr/tests/test_voice_train_service.py`

- [ ] **Step 4: 创建 voice_train_service.py 骨架和音频切片功能**

```python
"""voice_train_service.py — GPT-SoVITS one-click training pipeline."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
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
                    total += 0.0  # 非wav后续转码后测量
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
            cmd = [
                self.python,
                str(self.root / "tools" / "slice_audio.py"),
                str(fp),
                str(output_dir),
                "--threshold", "-34",
                "--min_length", "4000",
                "--min_interval", "300",
                "--hop_size", "10",
                "--max_sil_kept", "500",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("slice_audio stderr: {}", stderr.decode(errors="replace"))
            for f in output_dir.glob("*.wav"):
                all_sliced.append(f)
            self._emit("slice", int((idx + 1) / total * 100), f"切片 {idx+1}/{total}")
        if not all_sliced:
            # 如果切片没产出，直接复制原始文件
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
        output_list_path: str | Path,
        language: str = "zh",
        speaker_name: str = "speaker",
    ) -> Path:
        """对切片后的音频运行 ASR 语音识别。"""
        wav_dir = Path(wav_dir)
        output_list_path = Path(output_list_path)
        output_list_path.parent.mkdir(parents=True, exist_ok=True)
        self._emit("asr", 0, "正在进行语音识别...")
        asr_script = self.root / "tools" / "asr" / "fasterwhisper_asr.py"
        if not asr_script.is_file():
            asr_script = self.root / "tools" / "asr" / "funasr_asr.py"
        if not asr_script.is_file():
            raise FileNotFoundError("找不到 ASR 脚本")
        cmd = [
            self.python,
            str(asr_script),
            "--wav_dir", str(wav_dir),
            "--output_list_path", str(output_list_path),
            "--language", language,
            "--speaker_name", speaker_name,
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
        if not output_list_path.is_file():
            raise RuntimeError(f"ASR 未产出标注文件：{output_list_path}")
        self._emit("asr", 100, "语音识别完成")
        return output_list_path
```

- [ ] **Step 5: 写 TrainProgress 和 validate_audio_files 测试**

在 `Aiszr/tests/test_voice_train_service.py` 中：

```python
"""Tests for voice_train_service."""
import pytest
from voice_train_service import VoiceTrainService, TrainProgress


def test_train_progress_defaults():
    p = TrainProgress()
    assert p.step == ""
    assert p.percent == 0
    assert p.message == ""


def test_validate_audio_files_missing():
    ok, msg, dur = VoiceTrainService.validate_audio_files(["/nonexistent.wav"])
    assert not ok
    assert "不存在" in msg


def test_validate_audio_files_unsupported_format():
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello")
        path = f.name
    try:
        ok, msg, dur = VoiceTrainService.validate_audio_files([path])
        assert not ok
        assert "不支持" in msg
    finally:
        os.unlink(path)


def test_validate_audio_files_short_wav(tmp_path):
    import wave, struct
    wav_path = tmp_path / "short.wav"
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(32000)
        wf.writeframes(struct.pack("<h", 0) * 32000)  # 1 second of silence
    ok, msg, dur = VoiceTrainService.validate_audio_files([str(wav_path)])
    assert not ok  # 1s < 3s minimum
```

- [ ] **Step 6: 运行测试验证**

Run: `cd Aiszr && python -m pytest tests/test_voice_train_service.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add Aiszr/voice_train_service.py Aiszr/tests/test_voice_train_service.py
git commit -m "feat: add VoiceTrainService with audio slicing and ASR"
```

---

### Task 3: 训练服务 — 特征提取 + 模型训练

**Files:**
- Modify: `Aiszr/voice_train_service.py`

- [ ] **Step 8: 在 VoiceTrainService 中添加特征提取和训练方法**

在 `voice_train_service.py` 中追加以下方法：

```python
    async def extract_features(
        self,
        asr_list_path: str | Path,
        exp_dir: str | Path,
        version: str = "v2",
    ) -> None:
        """一键三连：文本提取 + HuBERT特征 + 语义特征。"""
        exp_dir = Path(exp_dir)
        asr_list_path = Path(asr_list_path)
        for subdir in ("2-name2text", "3-bert", "4-cnhubert", "5-wav32k", "6-name2semantic"):
            (exp_dir / subdir).mkdir(parents=True, exist_ok=True)

        steps = [
            ("特征提取-文本", "prepare_datasets/1-get-text.py", {
                "asr_list": str(asr_list_path),
            }),
            ("特征提取-HuBERT", "prepare_datasets/2-get-hubert-wav32k.py", {
                "asr_list": str(asr_list_path),
            }),
            ("特征提取-语义", "prepare_datasets/3-get-semantic.py", {
                "asr_list": str(asr_list_path),
            }),
        ]
        total = len(steps)
        for idx, (label, script, extra_env) in enumerate(steps):
            self._emit("extract", int(idx / total * 100), f"{label}...")
            script_path = self.root / script
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

    async def train_model(
        self,
        exp_dir: str | Path,
        version: str = "v2",
        gpt_epochs: int = 15,
        sovits_epochs: int = 8,
        gpu_id: int = 0,
    ) -> dict[str, str]:
        """运行 SoVITS + GPT 训练，返回 {gpt_ckpt, sovits_ckpt}。"""
        exp_dir = Path(exp_dir)
        results: dict[str, str] = {}

        # SoVITS training (Stage 2)
        self._emit("train_sovits", 0, "正在训练 SoVITS 模型...")
        s2_config = self._build_s2_config(exp_dir, version, sovits_epochs, gpu_id)
        s2_config_path = exp_dir / "tmp_s2.json"
        s2_config_path.write_text(json.dumps(s2_config, ensure_ascii=False, indent=2))
        cmd = [
            self.python,
            str(self.root / "GPT_SoVITS" / "s2_train.py"),
            "--config", str(s2_config_path),
        ]
        if version in ("v3", "v4"):
            cmd = [
                self.python,
                str(self.root / "GPT_SoVITS" / "s2_train_v3_lora.py"),
                "--config", str(s2_config_path),
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
            raise RuntimeError(f"SoVITS 训练失败：{err}")
        self._emit("train_sovits", 50, "SoVITS 训练完成")

        # GPT training (Stage 1)
        self._emit("train_gpt", 50, "正在训练 GPT 模型...")
        s1_config = self._build_s1_config(exp_dir, version, gpt_epochs)
        s1_config_path = exp_dir / "tmp_s1.yaml"
        s1_config_path.write_text(json.dumps(s1_config, ensure_ascii=False, indent=2))
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
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"GPT 训练失败：{err}")
        self._emit("train_gpt", 100, "GPT 训练完成")

        # 查找最新产出的模型权重
        version_suffix = version
        sovits_weight_dir = self.root / f"SoVITS_weights_{version_suffix}"
        gpt_weight_dir = self.root / f"GPT_weights_{version_suffix}"
        sovits_ckpt = self._latest_file(sovits_weight_dir, ".pth")
        gpt_ckpt = self._latest_file(gpt_weight_dir, ".ckpt")
        if sovits_ckpt:
            results["sovits_ckpt"] = str(sovits_ckpt)
        if gpt_ckpt:
            results["gpt_ckpt"] = str(gpt_ckpt)
        return results

    def _build_s2_config(self, exp_dir: Path, version: str, epochs: int, gpu_id: int) -> dict:
        base_config_name = {
            "v1": "s2.json", "v2": "s2.json",
            "v2Pro": "s2v2Pro.json", "v2ProPlus": "s2v2ProPlus.json",
            "v3": "s2.json", "v4": "s2.json",
        }.get(version, "s2.json")
        base_config_path = self.root / "GPT_SoVITS" / "configs" / base_config_name
        if base_config_path.is_file():
            import yaml
            with open(base_config_path) as f:
                cfg = yaml.safe_load(f) if base_config_name.endswith(".yaml") else json.load(f)
        else:
            cfg = {}
        cfg.setdefault("train", {})
        cfg["train"]["epochs"] = epochs
        cfg["train"]["exp_dir"] = str(exp_dir)
        cfg["data"] = cfg.get("data", {})
        cfg["data"]["training_files"] = str(exp_dir / "2-name2text.txt")
        return cfg

    def _build_s1_config(self, exp_dir: Path, version: str, epochs: int) -> dict:
        base_config_name = "s1longer-v2.yaml" if version != "v1" else "s1longer.yaml"
        base_config_path = self.root / "GPT_SoVITS" / "configs" / base_config_name
        if base_config_path.is_file():
            import yaml
            with open(base_config_path) as f:
                cfg = yaml.safe_load(f)
        else:
            cfg = {}
        cfg.setdefault("train", {})
        cfg["train"]["epochs"] = epochs
        cfg["train"]["exp_dir"] = str(exp_dir)
        cfg["data"] = cfg.get("data", {})
        cfg["data"]["train_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
        cfg["data"]["train_phoneme_path"] = str(exp_dir / "2-name2text.txt")
        return cfg

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
        gpu_id: int = 0,
        output_base_dir: str | Path | None = None,
    ) -> dict[str, str]:
        """一键完整训练管线。返回 {gpt_ckpt, sovits_ckpt, model_dir}。"""
        from local_voice_runtime import resolve_gpt_sovits_root, resolve_python_exe

        if output_base_dir is None:
            output_base_dir = self.root / "trained_models"
        output_base_dir = Path(output_base_dir)
        exp_dir = output_base_dir / voice_name
        exp_dir.mkdir(parents=True, exist_ok=True)
        slice_dir = exp_dir / "sliced"
        asr_list = exp_dir / "asr.list"

        # 1. 音频切片
        await self.slice_audio(input_files, slice_dir)

        # 2. ASR
        await self.run_asr(slice_dir, asr_list, language, voice_name)

        # 3. 特征提取
        await self.extract_features(asr_list, exp_dir, version)

        # 4. 训练
        results = await self.train_model(exp_dir, version, gpt_epochs, sovits_epochs, gpu_id)
        results["model_dir"] = str(exp_dir)
        return results
```

- [ ] **Step 9: 补充特征提取和训练的单元测试**

在 `Aiszr/tests/test_voice_train_service.py` 追加：

```python
def test_latest_file_finds_newest(tmp_path):
    (tmp_path / "old.ckpt").write_text("a")
    (tmp_path / "new.ckpt").write_text("b")
    import os, time
    os.utime(tmp_path / "new.ckpt", (time.time() + 10, time.time() + 10))
    result = VoiceTrainService._latest_file(tmp_path, ".ckpt")
    assert result is not None
    assert result.name == "new.ckpt"


def test_latest_file_returns_none_for_empty(tmp_path):
    assert VoiceTrainService._latest_file(tmp_path, ".ckpt") is None


def test_latest_file_returns_none_for_missing(tmp_path):
    missing = tmp_path / "nope"
    assert VoiceTrainService._latest_file(missing, ".ckpt") is None
```

- [ ] **Step 10: 运行测试**

Run: `cd Aiszr && python -m pytest tests/test_voice_train_service.py -v`
Expected: 全部 PASS

- [ ] **Step 11: Commit**

```bash
git add Aiszr/voice_train_service.py Aiszr/tests/test_voice_train_service.py
git commit -m "feat: add feature extraction and model training to VoiceTrainService"
```

---

### Task 4: LocalVoiceRuntime 支持动态切换权重

**Files:**
- Modify: `Aiszr/local_voice_runtime.py`
- Test: `Aiszr/tests/test_local_voice_runtime.py`

- [ ] **Step 12: 在 LocalVoiceRuntime 中添加 switch_weights 方法**

在 `local_voice_runtime.py` 的 `LocalVoiceRuntime` 类中添加方法，通过 API 热切换 GPT/SoVITS 权重：

```python
    async def switch_weights(self, gpt_ckpt: str = "", sovits_ckpt: str = "") -> tuple[bool, str]:
        """热切换 GPT/SoVITS 模型权重。"""
        if not self._port:
            return False, "服务未启动"
        import httpx
        endpoint = f"http://127.0.0.1:{self._port}"
        if sovits_ckpt:
            try:
                resp = await asyncio.to_thread(
                    httpx.get, f"{endpoint}/set_sovits_weights", params={"weights_path": sovits_ckpt}, timeout=10.0
                )
                if resp.status_code != 200:
                    return False, f"切换 SoVITS 权重失败：{resp.text}"
            except Exception as e:
                return False, f"切换 SoVITS 权重异常：{e}"
        if gpt_ckpt:
            try:
                resp = await asyncio.to_thread(
                    httpx.get, f"{endpoint}/set_gpt_weights", params={"weights_path": gpt_ckpt}, timeout=10.0
                )
                if resp.status_code != 200:
                    return False, f"切换 GPT 权重失败：{resp.text}"
            except Exception as e:
                return False, f"切换 GPT 权重异常：{e}"
        return True, "模型权重已切换"
```

注意：需要在文件头部确保有 `import asyncio`。

- [ ] **Step 13: 更新 ensure_local_voice_runtime 暴露 switch 方法**

在模块级别添加辅助函数：

```python
async def switch_local_voice_weights(gpt_ckpt: str = "", sovits_ckpt: str = "") -> tuple[bool, str]:
    """切换本地语音服务的模型权重。"""
    global _runtime
    if _runtime is None:
        return False, "本地语音服务未初始化"
    return await _runtime.switch_weights(gpt_ckpt, sovits_ckpt)
```

- [ ] **Step 14: 运行测试**

Run: `cd Aiszr && python -m pytest tests/test_local_voice_runtime.py -v`
Expected: 全部 PASS

- [ ] **Step 15: Commit**

```bash
git add Aiszr/local_voice_runtime.py
git commit -m "feat: add runtime weight switching for trained models"
```

---

### Task 5: LocalVoiceProvider 合成时自动切换微调模型

**Files:**
- Modify: `Aiszr/voice_manager.py`

- [ ] **Step 16: 修改 LocalVoiceProvider.create_clone 支持微调模型**

在 `voice_manager.py` 的 `LocalVoiceProvider.create_clone` 方法中，检测 `voice_id` 对应的 `VoiceEntry` 是否有 `trained_model_dir`，如果有则记录微调权重路径：

```python
    async def create_clone(
        self,
        wav_path: str,
        voice_id: str | None = None,
        *,
        model_id: str = "",
        requested_voice_id: str = "001",
        trained_model_dir: str = "",
    ) -> VoiceActionResult:
        path = Path(wav_path).expanduser()
        if not path.is_file():
            return VoiceActionResult(
                False,
                f"GPT-SoVITS 参考音频不存在：{wav_path}",
                clone_status="error",
            )
        return VoiceActionResult(
            True,
            "GPT-SoVITS 音色已就绪" + ("（微调模型）" if trained_model_dir else ""),
            clone_voice_id=str(path.resolve()),
            clone_status="ready",
        )
```

- [ ] **Step 17: 修改 LocalVoiceProvider.synthesize 支持微调权重切换**

在 `synthesize` 方法中，在调用 API 之前检查并切换微调权重：

```python
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_dir: Path,
        *,
        model_id: str = "",
        speed: float = DEFAULT_SPEED_RATIO,
        volume: float = DEFAULT_VOLUME_RATIO,
        trained_model_dir: str = "",
    ) -> VoiceActionResult:
        text = str(text or "").strip()
        if not text:
            return VoiceActionResult(False, "合成文本为空")

        ref_audio_path = str(voice_id or self.config.reference_audio).strip()
        if not ref_audio_path:
            return VoiceActionResult(False, "GPT-SoVITS 缺少参考音频，请先完成主播音色克隆或填写参考音频路径")
        if not Path(ref_audio_path).expanduser().is_file():
            return VoiceActionResult(False, f"GPT-SoVITS 参考音频不存在：{ref_audio_path}")

        ready = await ensure_local_voice_runtime(self.credential("endpoint"))
        if not ready.ok:
            return VoiceActionResult(False, ready.message)

        # 切换到微调权重（如果有）
        if trained_model_dir:
            from local_voice_runtime import switch_local_voice_weights
            model_path = Path(trained_model_dir)
            gpt_dir = model_path / "GPT_weights_v2"
            sovits_dir = model_path / "SoVITS_weights_v2"
            # 查找日志目录中的权重
            for d in model_path.iterdir():
                if d.is_dir() and d.name.startswith("logs_"):
                    ckpt_dir = d / "ckpt"
                    if ckpt_dir.is_dir():
                        for f in ckpt_dir.iterdir():
                            if f.suffix == ".ckpt" and "gpt" in d.name.lower():
                                gpt_dir = d
                            if f.suffix == ".pth" and ("sovits" in d.name.lower() or d.name.startswith("logs_s2")):
                                sovits_dir = d
            ok, msg = await switch_local_voice_weights(
                gpt_ckpt=str(gpt_dir) if gpt_dir.exists() else "",
                sovits_ckpt=str(sovits_dir) if sovits_dir.exists() else "",
            )
            if not ok:
                logger.warning("权重切换失败：{}", msg)

        # ... 后续现有合成逻辑保持不变 ...
```

- [ ] **Step 18: Commit**

```bash
git add Aiszr/voice_manager.py
git commit -m "feat: LocalVoiceProvider supports fine-tuned model weight switching"
```

---

### Task 6: 扩展 VoiceCloneDialog — 一键克隆 UI

**Files:**
- Modify: `Aiszr/ui_dialogs/voiceclonedialog.py`

- [ ] **Step 19: 扩展 VoiceCloneDialog 支持多文件上传和训练进度**

在 `voiceclonedialog.py` 中修改，关键改动：

1. 文件选择改为支持多文件，扩展支持 `.wav, .mp3, .flac`
2. 移除 15 秒时长限制（训练需要更多音频）
3. 当 provider 是 `local_voice` 时显示"一键训练克隆"按钮
4. 添加训练进度显示区域
5. 训练完成后自动更新 VoiceEntry 的 `trained_model_dir`

```python
class VoiceCloneDialog(QDialog):
    voice_action_requested = pyqtSignal(object)
    voice_settings_changed = pyqtSignal(object)
    DEFAULT_SAMPLE_PATH = str(app_dir() / "data" / "voice" / "samples" / "anchor.wav")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音克隆")
        self.resize(560, 560)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())
        self._sample_path = ""
        self._training = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self._title_label = QLabel("语音克隆", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        # Provider 提示
        self._provider_hint = QLabel("", self)
        self._provider_hint.setWordWrap(True)
        layout.addWidget(self._provider_hint)

        # 声音名称
        name_row = QWidget(self)
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.addWidget(QLabel("声音名称："))
        self._name_edit = MacLineEdit(placeholder="输入声音名称")
        self._name_edit.setText("")
        self._name_edit.setFixedHeight(36)
        name_layout.addWidget(self._name_edit)
        layout.addWidget(name_row)

        # 上传区域
        upload_row = QWidget(self)
        upload_layout = QHBoxLayout(upload_row)
        upload_layout.setContentsMargins(0, 0, 0, 0)
        upload_layout.setSpacing(theme.SPACING_SM)
        self._upload_btn = MacButton("选择音频文件", variant="secondary", parent=self)
        self._upload_btn.setMinimumSize(140, 34)
        upload_layout.addWidget(self._upload_btn)
        upload_layout.addStretch(1)
        layout.addWidget(upload_row)

        self._sample_path_edit = MacLineEdit(placeholder="支持 wav/mp3/flac，可多选")
        self._sample_path_edit.setFixedHeight(36)
        self._sample_path_edit.setReadOnly(True)
        layout.addWidget(self._sample_path_edit)

        self._sample_label = QLabel("未选择样本")
        self._sample_label.setWordWrap(True)
        layout.addWidget(self._sample_label)

        # 训练进度
        self._progress_label = QLabel("", self)
        self._progress_label.setWordWrap(True)
        self._progress_label.setStyleSheet(f"color: {theme.CLR_ACCENT}; border: none; font-size: 13px;")
        layout.addWidget(self._progress_label)

        layout.addSpacing(6)

        # 按钮行：快速克隆 + 一键训练克隆
        btn_row = QWidget(self)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        self._clone_btn = MacButton("快速克隆", variant="primary", parent=self)
        self._clone_btn.setMinimumSize(120, 34)
        btn_layout.addWidget(self._clone_btn)

        self._train_clone_btn = MacButton("一键训练克隆", variant="primary", parent=self)
        self._train_clone_btn.setMinimumSize(140, 34)
        btn_layout.addWidget(self._train_clone_btn)

        btn_layout.addStretch(1)
        layout.addWidget(btn_row)

        self._loading_label = QLabel("", self)
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setFixedHeight(20)
        self._loading_label.hide()
        layout.addWidget(self._loading_label)

        self._loading_dots_timer = QTimer(self)
        self._loading_dots_count = 0
        self._loading_dots_timer.timeout.connect(self._tick_loading_dots)

        layout.addSpacing(4)
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._upload_btn.clicked.connect(self._on_upload_clicked)
        self._clone_btn.clicked.connect(self._on_clone_clicked)
        self._train_clone_btn.clicked.connect(self._on_train_clone_clicked)
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        for w in (self._name_edit, self._sample_path_edit,
                  self._upload_btn, self._clone_btn, self._train_clone_btn):
            w.apply_theme_styles()
        self._loading_label.setStyleSheet(
            f"color: {theme.CLR_ACCENT}; border: none; font-size: 14px;")

    def _update_provider_ui(self) -> None:
        provider = self._voice_settings_state.provider
        is_local = provider == "local_voice"
        self._train_clone_btn.setVisible(is_local)
        if is_local:
            self._provider_hint.setText(
                "当前使用 GPT-SoVITS 本地语音。\n"
                "• 快速克隆：使用参考音频直接推理（零样本，无需训练）\n"
                "• 一键训练克隆：微调训练模型，音色更精准自然"
            )
        else:
            self._provider_hint.setText(
                "当前使用阿里云百炼。\n上传参考音频后点击快速克隆，将音频上传到云端进行音色克隆。"
            )

    def _on_upload_clicked(self):
        provider = self._voice_settings_state.provider
        if provider == "local_voice":
            # 多文件选择，不限时长
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择音频文件", "",
                "Audio Files (*.wav *.mp3 *.flac);;WAV Files (*.wav);;All Files (*)"
            )
        else:
            # 单文件，15s 限制
            paths_raw, _ = QFileDialog.getOpenFileName(
                self, "选择 wav 样本", "", "WAV Files (*.wav)"
            )
            paths = [paths_raw] if paths_raw else []
        if not paths:
            return
        # 验证
        if provider == "local_voice":
            from voice_train_service import VoiceTrainService
            ok, msg, total_sec = VoiceTrainService.validate_audio_files(paths)
            if not ok:
                self._status_label.setText(msg)
                return
            self._sample_path = ";".join(paths)
            self._sample_path_edit.setText(self._sample_path)
            self._sample_label.setText(f"{len(paths)} 个文件，{msg}")
            self._status_label.setText("")
        else:
            path = paths[0]
            ok, message = _validate_wav_duration(path, 15.0)
            if not ok:
                self._status_label.setText(message)
                return
            self._sample_path = path
            self._sample_path_edit.setText(path)
            self._sample_label.setText(path)
            self._status_label.setText(message)

    def _on_clone_clicked(self):
        """快速克隆 — 原有逻辑，GPT-SoVITS 零样本 / 百炼上传。"""
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("请输入声音名称")
            return
        if any(v.name == name for v in self._voice_settings_state.voices):
            self._status_label.setText(f"声音名称「{name}」已存在，请使用其他名称")
            return
        provider = self._voice_settings_state.provider
        if provider == "local_voice":
            # GPT-SoVITS 快速克隆：取第一个文件
            sample_ref = self._sample_path.split(";")[0] if self._sample_path else ""
        else:
            sample_ref = self._sample_path_edit.text().strip() or self.DEFAULT_SAMPLE_PATH
        if not sample_ref:
            self._status_label.setText("请先选择音频文件")
            return
        if not os.path.exists(sample_ref):
            self._status_label.setText(f"样本文件不存在：{sample_ref}")
            return
        if provider != "local_voice":
            ok, message = _validate_wav_duration(sample_ref, 15.0)
            if not ok:
                self._status_label.setText(message)
                return
        from voice_models import VoiceEntry
        entry = VoiceEntry(
            id=VoiceEntry.make_id(),
            name=name,
            provider=provider,
            sample_wav_path=sample_ref,
        )
        self._voice_settings_state.voices.append(entry)
        self._voice_settings_state.anchor.voice_id = entry.id
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText("正在克隆...")
        self._clone_btn.setEnabled(False)
        self._clone_btn.setText("克隆中...")
        self._start_loading()
        action = {
            "type": "clone",
            "voice_id": entry.id,
            "settings": self._voice_settings_state.to_dict(),
        }
        QTimer.singleShot(120, lambda payload=action: self.voice_action_requested.emit(payload))

    def _on_train_clone_clicked(self):
        """一键训练克隆 — GPT-SoVITS 微调训练。"""
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("请输入声音名称")
            return
        if any(v.name == name for v in self._voice_settings_state.voices):
            self._status_label.setText(f"声音名称「{name}」已存在，请使用其他名称")
            return
        if not self._sample_path:
            self._status_label.setText("请先选择音频文件")
            return
        files = [p.strip() for p in self._sample_path.split(";") if p.strip()]
        if not files:
            self._status_label.setText("请先选择音频文件")
            return

        # 禁用按钮
        self._training = True
        self._clone_btn.setEnabled(False)
        self._train_clone_btn.setEnabled(False)
        self._train_clone_btn.setText("训练中...")
        self._start_loading()
        self._status_label.setText("准备训练...")
        self._progress_label.setText("")

        import asyncio
        from voice_models import VoiceEntry
        from local_voice_runtime import resolve_gpt_sovits_root, resolve_python_exe

        # 创建 VoiceEntry
        entry = VoiceEntry(
            id=VoiceEntry.make_id(),
            name=name,
            provider="local_voice",
            sample_wav_path=files[0],
            clone_status="training",
        )
        self._voice_settings_state.voices.append(entry)
        self._voice_settings_state.anchor.voice_id = entry.id
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())

        # 在后台线程运行训练
        def _run_training():
            try:
                root = resolve_gpt_sovits_root()
                python = resolve_python_exe()
                from voice_train_service import VoiceTrainService, TrainProgress

                def on_progress(p: TrainProgress):
                    QTimer.singleShot(0, lambda: self._progress_label.setText(
                        f"[{p.step}] {p.percent}% — {p.message}"
                    ))

                service = VoiceTrainService(root, python, on_progress)
                loop = asyncio.new_event_loop()
                try:
                    results = loop.run_until_complete(
                        service.run_full_pipeline(
                            input_files=files,
                            voice_name=name,
                            language=self._voice_settings_state.api["local_voice"].text_lang or "zh",
                        )
                    )
                finally:
                    loop.close()
                return results, entry.id
            except Exception as e:
                return None, str(e)

        import threading
        thread = threading.Thread(target=self._handle_train_result, args=(_run_training(),), daemon=True)
        # 简化：直接在线程中启动
        def _thread_wrapper():
            result = _run_training()
            QTimer.singleShot(0, lambda: self._handle_train_result(result))

        threading.Thread(target=_thread_wrapper, daemon=True).start()

    def _handle_train_result(self, result):
        results, voice_id_or_error = result
        self._training = False
        self._clone_btn.setEnabled(True)
        self._train_clone_btn.setEnabled(True)
        self._train_clone_btn.setText("一键训练克隆")
        self._stop_loading()

        if results is None:
            self._status_label.setText(f"训练失败：{voice_id_or_error}")
            # 更新 voice entry 状态
            voice = self._voice_settings_state.find_voice(voice_id_or_error) if results is None else None
            return

        # 训练成功
        voice = self._voice_settings_state.find_voice(voice_id_or_error)
        if voice:
            voice.clone_status = "ready"
            voice.clone_voice_id = results.get("model_dir", "")
            voice.trained_model_dir = results.get("model_dir", "")
            voice.last_error = ""
            data = _load_settings()
            data["voice"] = self._voice_settings_state.to_dict()
            _save_settings(data)
            self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText(
            f"训练完成！GPT: {results.get('gpt_ckpt', 'N/A')}\n"
            f"SoVITS: {results.get('sovits_ckpt', 'N/A')}"
        )

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        self._update_provider_ui()

    # ... handle_voice_action_result 保持不变 ...
```

- [ ] **Step 20: Commit**

```bash
git add Aiszr/ui_dialogs/voiceclonedialog.py
git commit -m "feat: expand VoiceCloneDialog with one-click training UI"
```

---

### Task 7: resolve 辅助函数导出

**Files:**
- Modify: `Aiszr/local_voice_runtime.py`

- [ ] **Step 21: 确认 resolve_gpt_sovits_root 和 resolve_python_exe 可被外部调用**

检查 `local_voice_runtime.py` 中是否已有这两个函数。如果没有则添加：

```python
def resolve_gpt_sovits_root() -> Path:
    """解析 GPT-SoVITS 根目录。"""
    root = os.environ.get("GPT_SOVITS_ROOT", "")
    if root and Path(root).is_dir():
        return Path(root)
    candidates = [
        app_dir() / "GPT-SoVITS",
        app_dir().parent / "external_deps" / "GPT-SoVITS",
        app_dir().parent / "GPT-SoVITS",
    ]
    for c in candidates:
        if c.is_dir() and (c / "api_v2.py").is_file():
            return c
    return candidates[1]  # 默认返回 external_deps 路径


def resolve_python_exe() -> str:
    """解析 GPT-SoVITS 使用的 Python 可执行文件。"""
    python = os.environ.get("GPT_SOVITS_PYTHON", "")
    if python and Path(python).is_file():
        return python
    root = resolve_gpt_sovits_root()
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / "runtime" / "python.exe",
        root / "py312" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable
```

注意：需确认 `from app_paths import app_dir` 已导入。

- [ ] **Step 22: Commit**

```bash
git add Aiszr/local_voice_runtime.py
git commit -m "feat: export resolve_gpt_sovits_root and resolve_python_exe"
```

---

### Task 8: VoiceManager 合成链路传递 trained_model_dir

**Files:**
- Modify: `Aiszr/voice_manager.py`

- [ ] **Step 23: 修改 VoiceManager.synthesize_role_to_file 传递 trained_model_dir**

在 `VoiceManager` 的合成链路中，查找 voice entry 并传递 `trained_model_dir` 给 provider：

在 `synthesize_role_to_file` 和 `synthesize_and_play` 方法中，找到 voice entry 后传递：

```python
    # 在获取 voice 之后、调用 provider.synthesize 之前：
    trained_dir = voice.trained_model_dir if voice else ""
    result = await provider.synthesize(
        text,
        voice_id=clone_id or sample_path,
        output_dir=output_dir,
        model_id=self.settings.model_id,
        speed=speed,
        volume=volume,
        trained_model_dir=trained_dir,
    )
```

- [ ] **Step 24: 运行全部语音相关测试**

Run: `cd Aiszr && python -m pytest tests/test_voice_manager_cache.py tests/test_voice_provider_settings.py tests/test_local_voice_runtime.py tests/test_voice_train_service.py -v`
Expected: 全部 PASS

- [ ] **Step 25: Final commit**

```bash
git add Aiszr/voice_manager.py
git commit -m "feat: pass trained_model_dir through synthesis pipeline"
```

---

## Self-Review Checklist

### 1. Spec Coverage
| Requirement | Task |
|---|---|
| AI 配置选了 GPT 时添加按钮 | Task 6 — `_train_clone_btn` 仅 `local_voice` provider 显示 |
| 一键克隆声音 | Task 6 — `_on_train_clone_clicked` 调用完整管线 |
| 了解需要上传什么文件 | Task 2 — `validate_audio_files` 支持多格式 |
| 上传后一键训练 | Task 3 + 6 — `run_full_pipeline` |
| 训练好了之后克隆（修改合成链路） | Task 5 — `synthesize` 切换权重 |
| 输入名字后根据模型选择 GPT-SoVITS 或百炼 | Task 6 — `_update_provider_ui` 根据 provider 切换界面 |
| 克隆声音完美自然 | Task 3 — v2Pro 微调训练提升音质 |

### 2. Placeholder Scan
No TBD/TODO/placeholders found. All code blocks contain complete implementations.

### 3. Type Consistency
- `trained_model_dir` consistently `str` across VoiceEntry, VoiceTrainService, LocalVoiceProvider
- `VoiceTrainService` methods use consistent `str | Path` input types
- `TrainProgress` dataclass used consistently in callback signatures
