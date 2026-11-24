from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.logger import get_logger


log = get_logger(__name__)

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
LIBRARY_STYLE = "library"
LEGACY_STYLES = ("energetic", "chill", "cinematic", "hiphop")
BGM_CATEGORIES = ("未分类", "朋克", "欢快", "紧张", "史诗", "电子", "轻松", "暗黑", "复古", "射击", "RPG")
METADATA_FILENAME = ".metadata.json"


def bgm_root() -> Path:
    root = Path(settings.BGM_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_STYLE).mkdir(parents=True, exist_ok=True)
    for style in LEGACY_STYLES:
        (root / style).mkdir(parents=True, exist_ok=True)
    return root


def metadata_path() -> Path:
    return bgm_root() / METADATA_FILENAME


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "bgm"
    return name[:120]


def normalize_category(category: str | None) -> str:
    value = re.sub(r"\s+", "", (category or "").strip())
    return value if value in BGM_CATEGORIES else "未分类"


def style_dir(style: str) -> Path:
    style = (style or LIBRARY_STYLE).strip() or LIBRARY_STYLE
    if style == "default":
        style = LIBRARY_STYLE
    if style not in (LIBRARY_STYLE, *LEGACY_STYLES):
        raise ValueError(f"unknown style: {style}")
    d = (bgm_root() / style).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def track_key(style: str, filename: str) -> str:
    return f"{style}/{filename}"


def resolve_track(style: str, filename: str) -> Path | None:
    try:
        base = style_dir(style)
    except ValueError:
        return None
    fp = (base / filename).resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        return None
    if fp.is_file() and fp.suffix.lower() in AUDIO_EXT:
        return fp
    return None


def resolve_track_value(value: str | None) -> tuple[Path | None, str]:
    raw = (value or "").strip().replace("\\", "/")
    if not raw or "/" not in raw:
        return None, ""
    style, filename = raw.split("/", 1)
    path = resolve_track(style, filename)
    return path, track_key(style, filename) if path else ""


def load_metadata() -> dict[str, dict[str, Any]]:
    path = metadata_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        log.warning("[bgm] 读取元数据失败: %s", e)
    return {}


def save_metadata(data: dict[str, dict[str, Any]]) -> None:
    path = metadata_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_audio_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for style in (LIBRARY_STYLE, *LEGACY_STYLES):
        d = bgm_root() / style
        if not d.exists():
            continue
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                files.append((style, p))
    return files


def _probe_duration(path: Path) -> float:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        value = json.loads(res.stdout).get("format", {}).get("duration")
        return max(0.0, float(value or 0.0))
    except Exception:
        return 0.0


def _decode_mono(path: Path, *, seconds: float = 180.0, sample_rate: int = 11025) -> np.ndarray:
    res = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-i", str(path),
            "-t", f"{seconds:.1f}",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=max(45, seconds * 1.5),
    )
    audio = np.frombuffer(res.stdout, dtype=np.float32).astype(np.float32, copy=True)
    if audio.size == 0:
        return audio
    return np.nan_to_num(audio, copy=False)


def _smooth(values: np.ndarray, win: int) -> np.ndarray:
    if values.size == 0 or win <= 1:
        return values
    kernel = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(values, kernel, mode="same")


def _mood_tags(bpm: float | None, energy: float, filename: str) -> list[str]:
    tags: list[str] = []
    name = filename.lower()
    if bpm:
        if bpm >= 150:
            tags.append("快节奏")
        elif bpm >= 118:
            tags.append("中快")
        else:
            tags.append("慢节奏")
    if energy > 0.16:
        tags.append("强鼓点")
    if any(x in name for x in ("punk", "rock", "metal")):
        tags.append("朋克")
    if any(x in name for x in ("happy", "fun", "upbeat")):
        tags.append("欢快")
    if any(x in name for x in ("epic", "cinematic", "trailer")):
        tags.append("史诗")
    if any(x in name for x in ("dark", "horror")):
        tags.append("暗黑")
    return list(dict.fromkeys(tags))[:6]


def analyze_track(path: Path, *, category: str | None = None) -> dict[str, Any]:
    duration = _probe_duration(path)
    meta: dict[str, Any] = {
        "category": normalize_category(category),
        "duration": duration,
        "analysis_status": "done",
        "bpm": None,
        "beat_times": [],
        "energy_curve": [],
        "best_start_times": [0.0],
        "mood_tags": [],
    }
    try:
        sr = 11025
        hop = 512
        audio = _decode_mono(path, sample_rate=sr)
        if audio.size < sr:
            meta["analysis_status"] = "empty"
            return meta
        frame_count = audio.size // hop
        audio = audio[:frame_count * hop]
        frames = audio.reshape(frame_count, hop)
        energy = np.sqrt(np.mean(frames * frames, axis=1))
        energy = _smooth(energy, 5)
        if energy.size < 8:
            return meta
        norm = energy / max(float(np.max(energy)), 1e-6)
        threshold = max(0.36, float(np.mean(norm) + np.std(norm) * 0.75))
        min_gap_frames = max(1, int(0.24 * sr / hop))
        peaks: list[int] = []
        last = -9999
        for i in range(1, len(norm) - 1):
            if i - last < min_gap_frames:
                continue
            if norm[i] >= threshold and norm[i] >= norm[i - 1] and norm[i] >= norm[i + 1]:
                peaks.append(i)
                last = i
        beat_times = [round(i * hop / sr, 3) for i in peaks]
        intervals = [b - a for a, b in zip(beat_times, beat_times[1:]) if 0.28 <= b - a <= 1.2]
        bpm = None
        if intervals:
            median = float(np.median(np.array(intervals, dtype=np.float32)))
            if median > 0:
                bpm = int(round(60.0 / median))
                while bpm < 80:
                    bpm *= 2
                while bpm > 190:
                    bpm = int(round(bpm / 2))
        curve_step = max(1, int(len(norm) / 48))
        energy_curve = [round(float(np.mean(norm[i:i + curve_step])), 3) for i in range(0, len(norm), curve_step)]
        best = [t for t in beat_times if 2.0 <= t <= max(2.0, duration - 8.0)]
        if not best:
            best = [0.0]
        else:
            best = best[:12]
        meta.update(
            {
                "bpm": bpm,
                "beat_times": beat_times[:500],
                "energy_curve": energy_curve[:60],
                "best_start_times": best,
                "mood_tags": _mood_tags(float(bpm) if bpm else None, float(np.mean(norm)), path.name),
            }
        )
    except Exception as e:
        log.warning("[bgm] 分析失败 %s: %s", path.name, e)
        meta["analysis_status"] = "failed"
        meta["analysis_error"] = str(e)[:300]
    return meta


def ensure_metadata_for_files() -> dict[str, dict[str, Any]]:
    data = load_metadata()
    changed = False
    existing_keys = set()
    for style, path in list_audio_files():
        key = track_key(style, path.name)
        existing_keys.add(key)
        if key not in data:
            data[key] = {
                "category": "未分类",
                "duration": _probe_duration(path),
                "analysis_status": "pending",
                "bpm": None,
                "beat_times": [],
                "energy_curve": [],
                "best_start_times": [0.0],
                "mood_tags": [],
            }
            changed = True
    stale = [k for k in data if k not in existing_keys]
    for key in stale:
        data.pop(key, None)
        changed = True
    if changed:
        save_metadata(data)
    return data


def upsert_track_metadata(style: str, path: Path, *, category: str | None = None, analyze: bool = True) -> dict[str, Any]:
    data = load_metadata()
    key = track_key(style, path.name)
    if analyze:
        meta = analyze_track(path, category=category)
    else:
        meta = data.get(key, {})
        meta["category"] = normalize_category(category)
    meta["style"] = style
    meta["filename"] = path.name
    data[key] = meta
    save_metadata(data)
    return meta


def update_track_category(style: str, filename: str, category: str) -> dict[str, Any] | None:
    path = resolve_track(style, filename)
    if not path:
        return None
    data = ensure_metadata_for_files()
    key = track_key(style, filename)
    meta = data.get(key, {})
    meta["category"] = normalize_category(category)
    meta.setdefault("analysis_status", "pending")
    meta["style"] = style
    meta["filename"] = filename
    data[key] = meta
    save_metadata(data)
    return meta


def delete_track_metadata(style: str, filename: str) -> None:
    data = load_metadata()
    key = track_key(style, filename)
    if key in data:
        data.pop(key, None)
        save_metadata(data)


def select_best_start(meta: dict[str, Any] | None, target_duration: float) -> float:
    if not meta:
        return 0.0
    duration = float(meta.get("duration") or 0.0)
    starts = [float(x) for x in (meta.get("best_start_times") or []) if isinstance(x, (int, float))]
    max_start = max(0.0, duration - max(5.0, target_duration))
    starts = [s for s in starts if 0.0 <= s <= max_start]
    if starts:
        return round(starts[0], 3)
    return 0.0


def nearest_future_beat(meta: dict[str, Any] | None, bgm_start: float, video_time: float, *, window: float = 0.24) -> float | None:
    if not meta:
        return None
    target = bgm_start + video_time
    beats = meta.get("beat_times") or []
    for raw in beats:
        try:
            beat = float(raw)
        except Exception:
            continue
        delta = beat - target
        if 0.02 <= delta <= window:
            return delta
        if beat > target + window:
            break
    return None
