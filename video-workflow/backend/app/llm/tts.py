"""TTS 抽象，生产默认使用 ElevenLabs。

OpenAI 分支保留给显式配置使用；默认不会再走 OpenAI。
"""
from __future__ import annotations

from pathlib import Path
import os

import httpx
from openai import OpenAI

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

TTS_CONNECT_TIMEOUT_SECONDS = 10.0
TTS_READ_TIMEOUT_SECONDS = 120.0


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return default


def synthesize(text: str, output_path: Path, voice: str | None = None) -> Path:
    """合成语音到 output_path（mp3）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provider = (_env("TTS_PROVIDER", settings.TTS_PROVIDER) or "none").lower()

    if provider == "openai":
        return _openai_tts(text, output_path, voice)
    if provider == "elevenlabs":
        return _elevenlabs_tts(text, output_path, voice)
    if provider == "none":
        log.warning("TTS_PROVIDER=none，生成 1 秒静音占位文件")
        _silent_placeholder(output_path)
        return output_path

    raise ValueError(f"Unknown TTS_PROVIDER: {provider}")


def _openai_tts(text: str, output_path: Path, voice: str | None) -> Path:
    api_key = (_env("LLM_API_KEY", settings.LLM_API_KEY) or "").strip()
    base_url = (_env("LLM_BASE_URL", settings.LLM_BASE_URL) or "").strip().rstrip("/")
    model = (_env("OPENAI_TTS_MODEL", settings.OPENAI_TTS_MODEL) or "").strip()
    if not api_key or not base_url:
        raise RuntimeError("TTS_PROVIDER=openai 但缺少 LLM_API_KEY / LLM_BASE_URL")
    cli = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=httpx.Timeout(
            timeout=TTS_READ_TIMEOUT_SECONDS,
            connect=TTS_CONNECT_TIMEOUT_SECONDS,
            read=TTS_READ_TIMEOUT_SECONDS,
            write=TTS_CONNECT_TIMEOUT_SECONDS,
            pool=TTS_CONNECT_TIMEOUT_SECONDS,
        ),
    )
    voice = voice or (_env("OPENAI_TTS_VOICE", settings.OPENAI_TTS_VOICE) or "alloy")
    try:
        resp_ctx = cli.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
        )
        with resp_ctx as resp:
            resp.stream_to_file(str(output_path))
    except Exception as e:
        raise RuntimeError(f"OpenAI-compatible TTS 请求失败 model={model} base={base_url}: {e}") from e
    return output_path


def _elevenlabs_tts(text: str, output_path: Path, voice: str | None) -> Path:
    api_key = (_env("ELEVENLABS_API_KEY", settings.ELEVENLABS_API_KEY) or "").strip()
    if not api_key:
        raise RuntimeError("TTS_PROVIDER=elevenlabs 但缺少 ELEVENLABS_API_KEY")
    voice_id = voice or (_env("ELEVENLABS_VOICE_ID", settings.ELEVENLABS_VOICE_ID) or "").strip()
    model = (_env("ELEVENLABS_MODEL", settings.ELEVENLABS_MODEL) or "").strip()
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model,
        # TikTok ad 风格: 低 stability = 更有表达力 / 强语气波动
        # high similarity_boost = 紧贴 voice clone
        # style 0.6 = 加大情感戏剧化
        # use_speaker_boost = 增强清晰度
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.6,
            "use_speaker_boost": True,
        },
    }
    timeout = httpx.Timeout(
        timeout=TTS_READ_TIMEOUT_SECONDS,
        connect=TTS_CONNECT_TIMEOUT_SECONDS,
        read=TTS_READ_TIMEOUT_SECONDS,
        write=TTS_CONNECT_TIMEOUT_SECONDS,
        pool=TTS_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        with httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    except httpx.TimeoutException as e:
        raise RuntimeError(f"ElevenLabs TTS 请求超时 ({TTS_READ_TIMEOUT_SECONDS:.0f}s) voice={voice_id} model={model}") from e
    except httpx.HTTPStatusError as e:
        text_tail = (e.response.text or "")[:800]
        raise RuntimeError(f"ElevenLabs TTS HTTP {e.response.status_code} voice={voice_id} model={model}: {text_tail}") from e
    except Exception as e:
        raise RuntimeError(f"ElevenLabs TTS 请求失败 voice={voice_id} model={model}: {e}") from e
    return output_path


def _silent_placeholder(output_path: Path) -> None:
    """生成 1 秒静音 mp3 占位（用 ffmpeg）。"""
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "1", "-q:a", "9", "-acodec", "libmp3lame", str(output_path),
        ],
        check=True,
        capture_output=True,
    )
