"""去水印 + 去音轨。

策略选择:
1) crop  — 简单粗暴裁掉视频上下边的水印区域，最稳。抖音/TikTok 默认用这个。
2) delogo — ffmpeg 滤镜，给定位置做模糊填充，适合 logo 不在边缘的情况。

如果你想上 ProPainter / STTN 之类的 AI 擦除，后续在这里加一个 'inpaint' 模式即可。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


def probe_dimensions(file_path: Path) -> tuple[int, int]:
    """用 ffprobe 探测视频宽高。"""
    res = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", str(file_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(res.stdout)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


def remove_watermark_and_audio(
    input_path: Path,
    output_path: Path,
    crop_top: int | None = None,
    crop_bottom: int | None = None,
    remove_audio: bool = True,
) -> Path:
    """裁掉上/下边的水印区域，并可选移除音轨。

    输出保持竖屏（9:16）友好，裁剪后用 scale+pad 补回原画幅。
    """
    crop_top = settings.WATERMARK_CROP_TOP if crop_top is None else crop_top
    crop_bottom = settings.WATERMARK_CROP_BOTTOM if crop_bottom is None else crop_bottom

    width, height = probe_dimensions(input_path)
    new_h = height - crop_top - crop_bottom
    if new_h <= 0:
        log.warning("裁剪参数过大, 跳过裁剪 (height=%d, top=%d, bottom=%d)", height, crop_top, crop_bottom)
        crop_filter = f"crop={width}:{height}:0:0"
    else:
        crop_filter = f"crop={width}:{new_h}:0:{crop_top}"

    vf = f"{crop_filter},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vf", vf]
    if remove_audio:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        str(output_path),
    ]

    log.info("[watermark] %s -> %s (crop top=%d bottom=%d)", input_path.name, output_path.name, crop_top, crop_bottom)
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def make_thumbnail(input_path: Path, output_path: Path, time_sec: float = 1.0) -> Path:
    """从视频中抽一张缩略图。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", str(time_sec), "-i", str(input_path),
            "-frames:v", "1", "-q:v", "3", str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path
