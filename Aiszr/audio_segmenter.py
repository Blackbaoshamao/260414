import math
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioSegmenterConfig:
    silence_threshold_db: float = -25.0
    min_segment_ms: int = 2000
    min_silence_ms: int = 50
    scan_step_ms: int = 10
    max_retained_silence_ms: int = 1000
    max_segments: int = 300


class AudioSegmenter:
    def __init__(self, config: AudioSegmenterConfig | None = None) -> None:
        self.config = config or AudioSegmenterConfig()

    def segment(self, wav_path: str | Path, output_dir: str | Path) -> list[Path]:
        source = Path(wav_path)
        output = Path(output_dir)
        params, frames = self._read_pcm16_wav(source)
        cut_frames = self._find_cut_frames(params, frames)
        if not cut_frames:
            return [source]

        output.mkdir(parents=True, exist_ok=True)
        bytes_per_frame = params.nchannels * params.sampwidth
        starts = [0, *cut_frames]
        ends = [*cut_frames, params.nframes]
        segments: list[Path] = []

        for index, (start_frame, end_frame) in enumerate(zip(starts, ends), start=1):
            segment_path = output / f"segment_{index:04d}.wav"
            start_byte = start_frame * bytes_per_frame
            end_byte = end_frame * bytes_per_frame
            with wave.open(str(segment_path), "wb") as wav_file:
                wav_file.setnchannels(params.nchannels)
                wav_file.setsampwidth(params.sampwidth)
                wav_file.setframerate(params.framerate)
                wav_file.setcomptype(params.comptype, params.compname)
                wav_file.writeframes(frames[start_byte:end_byte])
            segments.append(segment_path)

        return segments

    def _read_pcm16_wav(self, source: Path) -> tuple[wave._wave_params, bytes]:
        try:
            with wave.open(str(source), "rb") as wav_file:
                params = wav_file.getparams()
                if params.comptype != "NONE" or params.sampwidth != 2:
                    raise ValueError("Only PCM16 WAV files are supported")
                frames = wav_file.readframes(params.nframes)
        except wave.Error as exc:
            raise ValueError("Only PCM16 WAV files are supported") from exc
        return params, frames

    def _find_cut_frames(self, params: wave._wave_params, frames: bytes) -> list[int]:
        bytes_per_frame = params.nchannels * params.sampwidth
        step_frames = max(1, params.framerate * self.config.scan_step_ms // 1000)
        min_silence_frames = params.framerate * self.config.min_silence_ms // 1000
        min_segment_frames = params.framerate * self.config.min_segment_ms // 1000
        retained_silence_frames = params.framerate * self.config.max_retained_silence_ms // 1000

        cuts: list[int] = []
        segment_start = 0
        silence_start: int | None = None
        cursor = 0

        while cursor < params.nframes:
            window_end = min(params.nframes, cursor + step_frames)
            is_silent = (
                self._window_dbfs(frames, cursor, window_end, bytes_per_frame, params.nchannels)
                <= self.config.silence_threshold_db
            )

            if is_silent:
                if silence_start is None:
                    silence_start = cursor
            elif silence_start is not None:
                silence_frames = cursor - silence_start
                segment_frames = silence_start - segment_start
                if silence_frames >= min_silence_frames and segment_frames >= min_segment_frames:
                    cut_frame = silence_start + min(silence_frames, retained_silence_frames)
                    cuts.append(cut_frame)
                    segment_start = cut_frame
                    if len(cuts) >= self.config.max_segments - 1:
                        break
                silence_start = None

            cursor += step_frames

        return cuts

    def _window_dbfs(
        self,
        frames: bytes,
        start_frame: int,
        end_frame: int,
        bytes_per_frame: int,
        channel_count: int,
    ) -> float:
        if end_frame <= start_frame:
            return -math.inf

        square_sum = 0.0
        frame_count = 0
        for frame_index in range(start_frame, end_frame):
            byte_offset = frame_index * bytes_per_frame
            channel_sum = 0
            for channel_index in range(channel_count):
                sample_offset = byte_offset + channel_index * 2
                channel_sum += int.from_bytes(
                    frames[sample_offset : sample_offset + 2],
                    "little",
                    signed=True,
                )
            mono_sample = channel_sum / channel_count
            square_sum += mono_sample * mono_sample
            frame_count += 1

        if square_sum == 0:
            return -math.inf
        rms = math.sqrt(square_sum / frame_count)
        return 20 * math.log10(rms / 32768.0)
