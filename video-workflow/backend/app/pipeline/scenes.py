"""镜头切分 + 抽帧 + 切片落地。

用 PySceneDetect 的内容检测器（按相邻帧 HSV 差异）找到镜头切换点。
然后用 ffmpeg 把每个镜头切成独立 mp4 文件，并抽一帧做缩略图/视觉打标用。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from scenedetect import detect, ContentDetector

from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class Scene:
    start: float
    end: float
    duration: float


def detect_scenes(file_path: Path, threshold: float = 27.0, min_scene_len_sec: float = 1.0) -> list[Scene]:
    """对短视频要把 threshold 调高些（默认 27），避免切得太碎。"""
    log.info("[scenes] detect: %s (threshold=%.1f)", file_path.name, threshold)
    scene_list = detect(
        str(file_path),
        ContentDetector(threshold=threshold, min_scene_len=int(min_scene_len_sec * 24)),
    )
    out: list[Scene] = []
    for s, e in scene_list:
        start = s.get_seconds()
        end = e.get_seconds()
        if end - start >= min_scene_len_sec:
            out.append(Scene(start=start, end=end, duration=end - start))

    if not out:
        log.info("[scenes] 未检测到切镜，作为单镜头处理")
        import cv2
        cap = cv2.VideoCapture(str(file_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        dur = frames / fps if fps else 0
        if dur > 0:
            out.append(Scene(start=0.0, end=dur, duration=dur))

    log.info("[scenes] 找到 %d 个镜头", len(out))
    return out


def extract_clip(
    input_path: Path,
    output_path: Path,
    start: float,
    end: float,
    re_encode: bool = True,
) -> Path:
    """切出 [start, end] 的片段。

    re_encode=True 用 libx264 重编码，时间戳更准确；
    re_encode=False 用 -c copy 拷流，最快但可能切不准。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(end - start, 0.1)

    if re_encode:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start}", "-i", str(input_path),
            "-t", f"{duration}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-an",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start}", "-i", str(input_path),
            "-t", f"{duration}", "-c", "copy", "-an", str(output_path),
        ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def extract_frames(
    input_path: Path,
    output_dir: Path,
    interval: float = 2.0,
    max_frames: int = 6,
) -> list[Path]:
    """从一个片段里抽帧给多模态模型看，最多 max_frames 张。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%03d.jpg")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", f"fps=1/{interval},scale=512:-2",
            "-frames:v", str(max_frames),
            "-q:v", "3",
            pattern,
        ],
        check=True,
        capture_output=True,
    )
    return sorted(output_dir.glob("frame_*.jpg"))
