"""ASR：用 faster-whisper 把视频里的中文语音转写成带时间戳的文本。

模型在第一次使用时会从 HuggingFace 下载到本地缓存。
默认 CPU 跑 'base' 模型，速度大约是实时的 1-2 倍。
有 NVIDIA GPU 可以把 ASR_DEVICE 改成 'cuda' 并把模型换成 'large-v3'。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from faster_whisper import WhisperModel

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

_model_cache: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model_cache
    if _model_cache is None:
        compute_type = "int8" if settings.ASR_DEVICE == "cpu" else "float16"
        log.info("[asr] loading model=%s device=%s compute_type=%s",
                 settings.ASR_MODEL, settings.ASR_DEVICE, compute_type)
        _model_cache = WhisperModel(
            settings.ASR_MODEL,
            device=settings.ASR_DEVICE,
            compute_type=compute_type,
        )
    return _model_cache


@dataclass
class Segment:
    start: float
    end: float
    text: str


def transcribe(file_path: Path, language: str | None = None) -> tuple[str, list[Segment]]:
    """返回 (full_text, segments)。"""
    model = _get_model()
    lang = language or settings.ASR_LANGUAGE
    log.info("[asr] transcribe: %s (lang=%s)", file_path.name, lang)

    segments_iter, info = model.transcribe(
        str(file_path),
        language=lang,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments: list[Segment] = []
    for s in segments_iter:
        segments.append(Segment(start=float(s.start), end=float(s.end), text=s.text.strip()))

    full = " ".join(s.text for s in segments).strip()
    log.info("[asr] done. %d segments, %.1fs duration, lang=%s",
             len(segments), info.duration, info.language)
    return full, segments


def segments_to_jsonable(segments: Iterable[Segment]) -> list[dict]:
    return [asdict(s) for s in segments]


def transcript_in_range(segments: list[dict], start: float, end: float) -> str:
    """返回某个时间段内的转写文字。"""
    parts: list[str] = []
    for s in segments:
        if s["end"] < start:
            continue
        if s["start"] > end:
            break
        parts.append(s["text"])
    return " ".join(parts).strip()
