"""视频合成 + 烧字幕 + 配音对齐。

策略:
1. 按脚本顺序，把每个 clip 用 ffmpeg trim 出来，**严格等比缩放** (禁止非等比 scale 导致拉伸)
2. 前景在 1080×N 安全框内 **fit 居中**，不裁左右；上下空白由同帧模糊背景填充
3. cropdetect 仅在不把 16:9 误判成超宽时才应用 (防 UI 暗边误当黑边)
4. 把所有 clip 用 concat filter 拼起来
5. 为每段 voiceover 生成 TTS mp3，按场景顺序拼成一条完整音轨
6. 拿 Whisper 跑 word-level 对齐, 生成 ASS 卡拉 OK 字幕 (libass 烧入)
7. 输出最终 mp4
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.core import bgm_library
from app.core import font_registry
from app.core.config import settings
from app.core.logger import get_logger
from app.llm import tts

log = get_logger(__name__)


TARGET_W, TARGET_H = 1080, 1920
WORD_ALIGNMENT_ENABLED = (os.getenv("WORD_ALIGNMENT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Word:
    """单词级时间戳, 时间是相对最终拼接视频的绝对时间。"""
    text: str
    start: float
    end: float


@dataclass
class SceneRender:
    clip_path: Path
    voice_path: Path                # 已经 pad 到 scene_duration 长度的音频
    voice_duration: float           # TTS 实际发声长度
    scene_duration: float           # 该场景视频/音频的整体长度 (两者严格相等)
    caption: str
    voiceover: str
    words: list[Word] = field(default_factory=list)
    scene_start_in_final: float = 0.0   # 该 scene 在最终视频里的起始时间
    fx_hint: str = ""                    # LLM 给的视觉特效提示: cold_open / zoom_punch / whip / speed_ramp
    callout: str = ""                    # 数字/价格大字 callout (例: "80% OFF" / "$5 KEY")


def ffprobe_duration(file: Path) -> float:
    res = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(file),
        ],
        check=True, capture_output=True, text=True, timeout=20,
    )
    return float(json.loads(res.stdout)["format"]["duration"])


_ffprobe_duration = ffprobe_duration  # backward-compat private alias


def _move_cross_device(src: Path, dst: Path) -> None:
    """跨文件系统安全 move: 容器场景下 /tmp 与 /data 不在同一设备,
    os.rename / Path.replace 会抛 EXDEV. 这里用 shutil.move,
    它在同设备时直接 rename, 跨设备时自动 copy + unlink, 行为更稳。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def _run_ffmpeg(cmd: list[str], *, what: str = "ffmpeg", timeout: float | None = None) -> subprocess.CompletedProcess:
    """统一封装 subprocess.run: 失败时把 stderr 尾巴塞进异常消息。

    标准 subprocess.run(check=True, capture_output=True) 失败时只抛
    'returned non-zero exit status 1', 拿不到 ffmpeg 的真实报错, 排障极其痛苦。
    这里失败时手动抛 RuntimeError, 末尾附 stderr 最后 600 字符。
    """
    timeout = timeout or 300
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{what} 超时 ({timeout:.0f}s)") from e
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", errors="ignore")[-600:].strip()
        raise RuntimeError(f"{what} 失败 (exit={result.returncode}): {tail or '(stderr 为空)'}")
    return result


def render_video(
    script: dict,
    clip_lookup: dict[int, Path],
    voice: str | None,
    output_path: Path,
    workdir: Path | None = None,
    bgm_style: str | None = None,
    bgm_file: str | None = None,
    bgm_category: str | None = None,
    bgm_start_mode: str | None = "smart",
    subtitle_font: str | None = None,
    subtitle_highlight_font: str | None = None,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict:
    """根据脚本渲染出最终 mp4，返回 {output_path, subtitle_path, voiceover_path, duration, bgm_used}。

    bgm_file: 指定 BGM 文件 (如 library/foo.mp3) 时优先使用;
    bgm_category: 未指定文件时按分类随机;
    bgm_style: 未指定文件时, 在 storage/bgm/{style}/ 下随机挑一首作为背景音乐,
               用 sidechaincompress 让 BGM 在配音时主动让位. 该 style 没文件时自动 fallback 到默认 style;
               所有 BGM 目录都空时跳过 BGM (纯人声输出)。
    """
    # 渲染中间产物默认放到 storage/tmp/ 下而不是系统 /tmp:
    #   1) 容器里 /tmp 是 overlayfs, 单次渲染产生几 GB 的中间帧/wav 容易把容器层撑爆;
    #   2) /tmp 和 /data/workflow/output 是不同设备, 最终 move 会触发 EXDEV.
    # 显式传入 workdir 时尊重调用者的选择 (单元测试可能想用临时目录),
    # 这种情况下也不负责清理。
    cleanup_workdir = False
    if workdir is None:
        tmp_root = settings.storage_path / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="render_", dir=str(tmp_root)))
        cleanup_workdir = True
    workdir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(progress: int, message: str) -> None:
        log.info("[compose] %s", message)
        if progress_cb:
            progress_cb(progress, message)

    scenes_data: list[SceneRender] = []
    scene_offset = 0.0  # 当前场景在最终视频里的起始时间戳
    planned_duration = sum(float(s.get("duration") or 0.0) for s in script.get("scenes", []))
    bgm_path, bgm_meta, bgm_start = _pick_bgm(
        bgm_style,
        bgm_file,
        bgm_category=bgm_category,
        target_duration=planned_duration,
        start_mode=bgm_start_mode,
    )

    total_scenes = max(len(script["scenes"]), 1)
    emit(12, f"准备渲染 {total_scenes} 个场景")
    for idx, s in enumerate(script["scenes"]):
        clip_id = s["clip_id"]
        clip_src = clip_lookup.get(clip_id)
        if not clip_src or not Path(clip_src).exists():
            log.warning("[compose] 跳过缺失片段 clip_id=%s", clip_id)
            continue

        voice_text = s.get("voiceover") or ""
        fx_hint = (s.get("fx_hint") or "").strip().lower()
        callout = (s.get("callout") or "").strip()

        raw_voice = workdir / f"voice_raw_{idx:02d}.mp3"
        sped_voice = workdir / f"voice_sped_{idx:02d}.mp3"
        base_progress = 12 + int(48 * idx / total_scenes)
        emit(base_progress, f"场景 {idx + 1}/{total_scenes}: 生成配音")
        if voice_text.strip():
            try:
                tts.synthesize(voice_text, raw_voice, voice=voice)
                # 倍速到 settings.TTS_SPEED 让配音更 TikTok-y (默认 1.12x)
                speed = float(getattr(settings, "TTS_SPEED", 1.12) or 1.12)
                if abs(speed - 1.0) > 0.01:
                    _speed_audio(raw_voice, sped_voice, speed)
                    raw_voice = sped_voice
            except Exception as e:
                log.warning("[compose] TTS 失败, 用静音兜底: %s", e)
                _silence(raw_voice, duration=max(s["duration"], 1.0))
        else:
            _silence(raw_voice, duration=max(s["duration"], 1.0))

        v_dur = _ffprobe_duration(raw_voice)
        # 画面长度 = 配音长度 + 一点呼吸感, 同时不低于 LLM 安排的 duration
        scene_dur = max(v_dur + 0.15, float(s.get("duration") or 0.0), 1.0)
        if bgm_meta and idx < total_scenes - 1:
            delta = bgm_library.nearest_future_beat(bgm_meta, bgm_start, scene_offset + scene_dur)
            if delta is not None:
                scene_dur += min(delta, 0.24)

        # 关键: 音频 pad 到完整 scene_dur, 视频/音频/字幕三时钟严格对齐
        voice_padded = workdir / f"voice_{idx:02d}.mp3"
        _pad_audio_to(raw_voice, voice_padded, scene_dur)

        clip_out = workdir / f"scene_{idx:02d}.mp4"
        emit(base_progress + 3, f"场景 {idx + 1}/{total_scenes}: 处理画面")
        _prepare_clip(clip_src, clip_out, scene_dur, fx_hint=fx_hint, is_first=(idx == 0))

        # word-level 对齐 (拿 TTS 原始未 pad 的版本跑 whisper)
        emit(base_progress + 5, f"场景 {idx + 1}/{total_scenes}: 生成字幕时间轴")
        words = _align_voice_to_words(
            raw_voice,
            scene_offset_in_final=scene_offset,
            fallback_text=voice_text,
            voice_duration=v_dur,
        )

        scenes_data.append(SceneRender(
            clip_path=clip_out,
            voice_path=voice_padded,
            voice_duration=v_dur,
            scene_duration=scene_dur,
            caption=s.get("caption") or "",
            voiceover=voice_text,
            words=words,
            scene_start_in_final=scene_offset,
            fx_hint=fx_hint,
            callout=callout,
        ))
        scene_offset += scene_dur

    if not scenes_data:
        raise RuntimeError("没有可渲染的场景")

    emit(62, "拼接视频片段")
    concat_video = workdir / "concat_video.mp4"
    _concat_videos([sd.clip_path for sd in scenes_data], concat_video)

    emit(68, "拼接配音音轨")
    concat_audio = workdir / "concat_audio.mp3"
    _concat_audios([sd.voice_path for sd in scenes_data], concat_audio)

    emit(72, "生成字幕文件")
    ass_path = workdir / "captions.ass"
    _build_ass(
        scenes_data,
        ass_path,
        subtitle_font=subtitle_font,
        subtitle_highlight_font=subtitle_highlight_font,
    )

    srt_path = workdir / "captions.srt"
    _build_srt_from_words(scenes_data, srt_path)

    # 混入 BGM (没 BGM 文件时直接 copy concat_audio)
    emit(78, "混合背景音乐")
    final_audio = workdir / "final_audio.mp3"
    total_audio_dur = _ffprobe_duration(concat_audio)
    _mix_voice_with_bgm(concat_audio, bgm_path, final_audio, total_audio_dur, bgm_start=bgm_start)
    if bgm_path:
        log.info("[compose] 使用 BGM: style=%s, file=%s, start=%.2fs", bgm_style or "default", bgm_path.name, bgm_start)
    else:
        log.info("[compose] 未使用 BGM (BGM 目录为空, 纯人声输出)")

    voiceover_out = output_path.parent / f"{output_path.stem}.voiceover.mp3"
    subtitle_out = output_path.parent / f"{output_path.stem}.srt"
    # 不用 Path.replace / os.rename: 容器里 /tmp (overlayfs) 和 /data/workflow (bind mount)
    # 是不同设备, rename 会 EXDEV [Errno 18]. shutil.move 跨设备时会自动 copy+unlink.
    _move_cross_device(final_audio, voiceover_out)
    _move_cross_device(srt_path, subtitle_out)

    emit(84, "合成最终视频")
    _burn_subtitles_and_mux(concat_video, voiceover_out, ass_path, output_path)

    result = {
        "output_path": str(output_path),
        "subtitle_path": str(subtitle_out),
        "voiceover_path": str(voiceover_out),
        "duration": _ffprobe_duration(output_path),
        "bgm_used": str(bgm_path) if bgm_path else "",
        "bgm_start": bgm_start,
    }

    if cleanup_workdir:
        # 失败路径里也想清理, 但当前函数失败直接抛出, 由外层 worker 决定保留
        # workdir 看现场。成功路径下清掉, 避免 storage/tmp 越积越大。
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception as e:
            log.warning("[compose] 清理 workdir 失败 (忽略): %s", e)

    return result


# ---------------------------------------------------------------------------
# 视频片段预处理 (横屏自适应 + 模糊背景)
# ---------------------------------------------------------------------------

def _detect_letterbox(src: Path, sample_seconds: float = 4.0) -> str | None:
    """探测素材自身的电影级黑边, 返回 'crop=W:H:X:Y' 或 None。"""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-ss", "0", "-t", f"{sample_seconds:.1f}",
                "-i", str(src), "-vf", "cropdetect=24:16:0",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", result.stderr or "")
    if not matches:
        return None
    counts: dict[tuple, int] = {}
    for m in matches:
        counts[m] = counts.get(m, 0) + 1
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    w, h, x, y = (int(v) for v in best)

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(src)],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)["streams"][0]
        if w >= info["width"] - 4 and h >= info["height"] - 4:
            return None
    except Exception:
        pass

    if w < 200 or h < 200:
        return None

    return f"crop={w}:{h}:{x}:{y}"


def _probe_video_wh(src: Path) -> tuple[int, int]:
    """首路视频流的像素尺寸。"""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(src)],
            capture_output=True, text=True, check=True, timeout=10,
        )
        s = json.loads(probe.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:
        return 1920, 1080


def _letterbox_crop_safe(src: Path, raw_crop: str | None) -> str | None:
    """只裁确定的内置黑边；不做颜色抠图，不裁主体。

    以前为了防 HUD 暗条误判，16:9 裁成 2.35:1 时直接丢弃 crop，导致真正电影黑边保留下来。
    现在允许典型电影宽银幕黑边裁切，但拒绝过窄/过激的误检。
    """
    if not raw_crop:
        return None
    rw, rh = _probe_video_wh(src)
    if rh <= 0:
        return raw_crop
    raw_ar = rw / rh
    m = re.match(r"crop=(\d+):(\d+):\d+:\d+", raw_crop)
    if not m:
        return raw_crop
    cw, ch = int(m.group(1)), int(m.group(2))
    if ch <= 0:
        return raw_crop
    crop_ar = cw / ch
    removed_h_ratio = max(0.0, 1.0 - (ch / max(1, rh)))
    removed_w_ratio = max(0.0, 1.0 - (cw / max(1, rw)))

    # 宽度被裁很多通常不是上下黑边，而是 cropdetect 被暗角/HUD 带偏。
    if removed_w_ratio > 0.04:
        log.info(
            "[compose] %s 跳过 cropdetect: 横向裁切 %.1f%% 过多, 疑似误检",
            src.name, removed_w_ratio * 100,
        )
        return None

    # 16:9 素材裁成 2.2-2.55 是常见电影黑边；超过 2.65 多半裁到主体了。
    if raw_ar < 1.95 and crop_ar > 2.65:
        log.info(
            "[compose] %s 跳过 cropdetect: 原始 %.2f → 裁切后 %.2f (过激, 疑似误检)",
            src.name, raw_ar, crop_ar,
        )
        return None
    if raw_ar < 1.95 and crop_ar > 2.05 and removed_h_ratio < 0.08:
        log.info(
            "[compose] %s 跳过 cropdetect: 仅裁高 %.1f%% 但宽高比跳到 %.2f, 疑似 HUD 暗条",
            src.name, removed_h_ratio * 100, crop_ar,
        )
        return None
    return raw_crop


def _fit_even(sw: float, sh: float, box_w: int, box_h: int) -> tuple[int, int]:
    """等比缩放到完全放入 box_w×box_h, 返回偶数宽高 (无拉伸)。"""
    if sw <= 0 or sh <= 0:
        return min(box_w, 1080), min(box_h, 1920)
    scale = min(box_w / sw, box_h / sh)
    ow = max(2, int(sw * scale) // 2 * 2)
    oh = max(2, int(sh * scale) // 2 * 2)
    return ow, oh


def _max_fg_box(raw_ar: float) -> tuple[int, int]:
    """前景允许的最大包围盒 (宽×高), 横屏略留边给模糊背景。"""
    if raw_ar <= 0.72:
        return TARGET_W, TARGET_H
    if raw_ar <= 1.25:
        return TARGET_W, int(TARGET_H * 0.92)
    return TARGET_W, int(TARGET_H * 0.90)


def _prepare_clip(
    src: Path,
    dst: Path,
    duration: float,
    fx_hint: str = "",
    is_first: bool = False,
) -> None:
    """渲染成 1080x1920: 前景 **等比 fit** 进安全框, 不裁切左右; 全帧模糊背景。

    曾用 scale=WxH 无等比约束会把画面拉扁/拉长; 现改为先算等比偶数尺寸再 scale。
    fx_hint: zoom_punch / cold_open 在前景上加 zoompan; 其它见代码分支。
    """
    src_dur = _ffprobe_duration(src)
    raw_crop = _detect_letterbox(src)
    auto_crop = _letterbox_crop_safe(src, raw_crop)
    pre_filter = f",{auto_crop}" if auto_crop else ""
    if auto_crop:
        log.info("[compose] %s 检测到内置黑边, 自动去除: %s", src.name, auto_crop)

    rw, rh = _probe_video_wh(src)
    if auto_crop:
        m = re.match(r"crop=(\d+):(\d+):\d+:\d+", auto_crop)
        if m:
            eff_w, eff_h = float(m.group(1)), float(m.group(2))
        else:
            eff_w, eff_h = float(rw), float(rh)
    else:
        eff_w, eff_h = float(rw), float(rh)
    if eff_h <= 0:
        eff_w, eff_h = 1920.0, 1080.0
    raw_ar = eff_w / eff_h

    box_w, box_h = _max_fg_box(raw_ar)
    fg_w, fg_h = _fit_even(eff_w, eff_h, box_w, box_h)
    # 等比 scale 到精确 fg_w×fg_h (不再使用 force_original_aspect_ratio, 因尺寸已算准)
    fg_scale = f"scale={fg_w}:{fg_h}"
    log.info("[compose] %s eff=%.0fx%.0f ar=%.2f box=%dx%d → fg=%dx%d",
             src.name, eff_w, eff_h, raw_ar, box_w, box_h, fg_w, fg_h)

    # 前景特效: zoom_punch 或 cold_open 都给前景做"快推近 -> 回正"
    want_zoom = fx_hint in ("zoom_punch", "punch") or (is_first and fx_hint == "cold_open")
    fg_extra = ""
    if want_zoom:
        # 30fps, 前 8 帧 (~0.27s) 推到 1.10, 接下来 15 帧缓慢回到 1.0
        # zoompan 输出尺寸必须跟 fg 一致, 否则 overlay 会错位
        fg_extra = (
            ",zoompan=z='if(lt(on,8),1+0.0125*on,if(lt(on,23),1.10-0.0067*(on-8),1.0))'"
            f":d=1:s={fg_w}x{fg_h}:fps=30"
        )
    base_chain = (
        "[v0]split=2[bg_in][fg_in];"
        f"[bg_in]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},boxblur=30:2,"
        "eq=saturation=1.35:brightness=-0.04[bg];"
        f"[fg_in]{fg_scale},setsar=1{fg_extra}[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        "setsar=1,fps=30,format=yuv420p[v]"
    )

    if duration <= src_dur:
        filter_complex = f"[0:v]copy{pre_filter}[v0];" + base_chain
        cmd = [
            "ffmpeg", "-y", "-ss", "0", "-i", str(src),
            "-t", f"{duration}",
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(dst),
        ]
    else:
        slowdown = duration / src_dur
        filter_complex = f"[0:v]setpts={slowdown:.4f}*PTS{pre_filter}[v0];" + base_chain
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-t", f"{duration}",
            str(dst),
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=max(90, duration * 35))
    except subprocess.TimeoutExpired:
        log.warning("[compose] %s fx_hint=%s 渲染超时, 降级备用方案", src.name, fx_hint or "(none)")
        _prepare_clip_letterbox(src, dst, duration)
        return
    if result.returncode != 0:
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="ignore")[-400:]
        log.warning("[compose] %s fx_hint=%s 渲染失败, 降级纯黑 letterbox: %s",
                    src.name, fx_hint or "(none)", stderr_tail)
        _prepare_clip_letterbox(src, dst, duration)


def _prepare_clip_letterbox(src: Path, dst: Path, duration: float) -> None:
    """降级方案: 模糊背景 + 等比前景；避免回退成黑底 letterbox。"""
    src_dur = _ffprobe_duration(src)
    vf = (
        f"split=2[bg_in][fg_in];"
        f"[bg_in]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},boxblur=30:2,eq=saturation=1.25:brightness=-0.04[bg];"
        f"[fg_in]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p"
    )
    if duration <= src_dur:
        cmd = [
            "ffmpeg", "-y", "-ss", "0", "-i", str(src),
            "-t", f"{duration}", "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(dst),
        ]
    else:
        slowdown = duration / src_dur
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", f"{vf},setpts={slowdown:.4f}*PTS",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-t", f"{duration}", str(dst),
        ]
    _run_ffmpeg(cmd, what=f"备用片段渲染 {src.name}")


# ---------------------------------------------------------------------------
# 拼接 / 静音 / 音频 padding
# ---------------------------------------------------------------------------

def _concat_videos(parts: list[Path], dst: Path) -> None:
    list_file = dst.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in parts))
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(dst),
        ],
        what="拼接视频",
    )


def _concat_audios(parts: list[Path], dst: Path) -> None:
    list_file = dst.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in parts))
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c:a", "libmp3lame", "-q:a", "2",
            str(dst),
        ],
        what="拼接配音",
    )


def _silence(dst: Path, duration: float) -> None:
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono",
            "-t", f"{duration}", "-q:a", "9",
            "-acodec", "libmp3lame", str(dst),
        ],
        what="生成静音音轨",
    )


def _speed_audio(src: Path, dst: Path, speed: float) -> None:
    """用 ffmpeg atempo 给音频倍速 (1.0=原速, 1.15=快 15%)。

    atempo 单次最大 2.0, 这里只支持 0.5-2.0 的合法区间。
    保持原始 pitch (不变调), 让配音更有 TikTok 节奏。
    """
    speed = max(0.5, min(2.0, float(speed)))
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-af", f"atempo={speed:.3f}",
            "-ac", "1", "-ar", "44100",
            "-acodec", "libmp3lame", "-q:a", "2",
            str(dst),
        ],
        what="配音倍速",
    )


def _resolve_bgm_file(bgm_file: str | None) -> Path | None:
    """Resolve a user-selected BGM under BGM_DIR, rejecting path traversal."""
    path, _ = bgm_library.resolve_track_value(bgm_file)
    return path


def _pick_bgm(
    style: str | None,
    bgm_file: str | None = None,
    *,
    bgm_category: str | None = None,
    target_duration: float = 0.0,
    start_mode: str | None = "smart",
) -> tuple[Path | None, dict | None, float]:
    """从统一 BGM 库随机挑一首；兼容旧 style 目录。"""
    meta_map = bgm_library.ensure_metadata_for_files()
    selected, selected_key = bgm_library.resolve_track_value(bgm_file)
    if selected:
        meta = meta_map.get(selected_key)
        start = bgm_library.select_best_start(meta, target_duration) if (start_mode or "smart") == "smart" else 0.0
        return selected, meta, start
    if (style or "").strip().lower() == "none":
        return None, None, 0.0
    category = bgm_library.normalize_category(bgm_category) if bgm_category else ""
    tracks: list[tuple[Path, str, dict | None]] = []
    for sub, path in bgm_library.list_audio_files():
        key = bgm_library.track_key(sub, path.name)
        meta = meta_map.get(key)
        if category and (meta or {}).get("category") != category:
            continue
        tracks.append((path, key, meta))
    if not tracks:
        return None, None, 0.0
    path, _, meta = random.choice(tracks)
    start = bgm_library.select_best_start(meta, target_duration) if (start_mode or "smart") == "smart" else 0.0
    return path, meta, start


def _mix_voice_with_bgm(voice: Path, bgm: Path | None, dst: Path, target_duration: float, *, bgm_start: float = 0.0) -> None:
    """把配音和 BGM 混合, BGM sidechain ducking 让出人声。

    没有 BGM 时, 直接 copy voice 到 dst (兜底).
    """
    if bgm is None or not bgm.exists():
        # 没 BGM, voice 就是最终音轨
        _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", str(voice),
                "-c:a", "libmp3lame", "-q:a", "2",
                "-ac", "2", "-ar", "44100",
                str(dst),
            ],
            what="输出纯人声音轨",
        )
        return

    bgm_db = float(getattr(settings, "BGM_VOLUME_DB", -14.0) or -14.0)
    duck_thr = float(getattr(settings, "BGM_DUCK_THRESHOLD", 0.05) or 0.05)
    duck_ratio = float(getattr(settings, "BGM_DUCK_RATIO", 8.0) or 8.0)

    # filter_complex:
    # [bgm_in] aloop infinite -> atrim to target -> volume -> [bgm_norm]
    # [bgm_norm][voice] sidechaincompress -> [bgm_ducked]
    # [bgm_ducked][voice] amix -> [out]
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{target_duration:.2f},"
        f"volume={bgm_db:.1f}dB,asetpts=N/SR/TB[bgm_norm];"
        f"[bgm_norm][0:a]sidechaincompress="
        f"threshold={duck_thr}:ratio={duck_ratio:.1f}:attack=20:release=350"
        f":makeup=1[bgm_ducked];"
        f"[bgm_ducked][0:a]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(voice),
    ]
    if bgm_start > 0.01:
        cmd.extend(["-ss", f"{bgm_start:.3f}"])
    cmd.extend([
        "-i", str(bgm),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-q:a", "2",
        "-ac", "2", "-ar", "44100",
        str(dst),
    ])
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=max(90, target_duration * 10))
    except subprocess.TimeoutExpired:
        log.warning("[compose] BGM 混音超时, 降级纯人声")
        result = None
    if result is None or result.returncode != 0:
        stderr_tail = "" if result is None else (result.stderr or b"").decode("utf-8", errors="ignore")[-400:]
        log.warning("[compose] BGM 混音失败, 降级纯人声: %s", stderr_tail)
        # 兜底: 直接用人声
        _run_ffmpeg(
            ["ffmpeg", "-y", "-i", str(voice),
             "-c:a", "libmp3lame", "-q:a", "2", "-ac", "2", "-ar", "44100",
             str(dst)],
            what="混音失败降级纯人声",
        )


def _pad_audio_to(src: Path, dst: Path, target_duration: float) -> None:
    """把音频 pad 静音 / 截断到指定时长, 保证音频和视频严格同长。"""
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-af", f"apad,atrim=0:{target_duration:.3f},asetpts=N/SR/TB",
            "-ac", "1", "-ar", "44100",
            "-acodec", "libmp3lame", "-q:a", "2",
            str(dst),
        ],
        what="对齐配音时长",
    )


# ---------------------------------------------------------------------------
# Word-level 对齐 (Whisper)
# ---------------------------------------------------------------------------

def _align_voice_to_words(
    voice_path: Path,
    scene_offset_in_final: float,
    fallback_text: str = "",
    voice_duration: float = 0.0,
) -> list[Word]:
    """跑 faster-whisper 拿 TTS 配音的 word-level 时间戳。

    返回 Word 列表, 时间戳已经平移到最终拼接视频的绝对时间。
    失败时按字符均分 fallback_text 作为兜底。
    """
    if not WORD_ALIGNMENT_ENABLED:
        return _even_split_words(
            fallback_text,
            scene_offset_in_final=scene_offset_in_final,
            voice_duration=voice_duration,
        )

    try:
        from app.pipeline.asr import _get_model
        model = _get_model()
        segments_iter, _info = model.transcribe(
            str(voice_path),
            language="en",
            word_timestamps=True,
            beam_size=1,
            vad_filter=False,
        )
        out: list[Word] = []
        for seg in segments_iter:
            for w in (seg.words or []):
                txt = (w.word or "").strip()
                if not txt:
                    continue
                w_start = float(w.start)
                w_end = float(w.end)
                if w_end - w_start < 0.02:
                    w_end = w_start + 0.15  # whisper 偶尔返回零长度词, 强制给一点显示时间
                out.append(Word(
                    text=txt,
                    start=scene_offset_in_final + w_start,
                    end=scene_offset_in_final + w_end,
                ))
        if out:
            return out
        log.warning("[compose] whisper 没识别出 word, 走兜底均分")
    except Exception as e:
        log.warning("[compose] word alignment failed (%s), fallback to even split", e)

    return _even_split_words(
        fallback_text,
        scene_offset_in_final=scene_offset_in_final,
        voice_duration=voice_duration,
    )


def _even_split_words(
    fallback_text: str,
    *,
    scene_offset_in_final: float,
    voice_duration: float,
) -> list[Word]:
    """不用 Whisper 时按词均分，速度快且字幕时间轴足够可用。"""
    text = (fallback_text or "").strip()
    if not text or voice_duration <= 0:
        return []
    tokens = text.split()
    if not tokens:
        return []
    per_word = voice_duration / len(tokens)
    out: list[Word] = []
    for i, tok in enumerate(tokens):
        s = scene_offset_in_final + i * per_word
        e = scene_offset_in_final + (i + 1) * per_word
        out.append(Word(text=tok, start=s, end=e))
    return out


# ---------------------------------------------------------------------------
# ASS / SRT 字幕生成
# ---------------------------------------------------------------------------

# 关键词识别 (数字/价格/折扣/CTA 词) 标黄色突出
_HIGHLIGHT_PATTERN = re.compile(
    r"(\$\d[\d,\.]*|\d+%|\d+x|\b\d+\b|"
    r"free|now|today|tonight|new|hot|sale|deal|off|"
    r"limited|instant|only|just|huge|exclusive|guarantee|verified|"
    r"wait|stop|secret|crazy|insane|wtf|epic)",
    re.IGNORECASE,
)

# 紧密粘连规则: 这些词后跟特定 tail 时, 不允许断块, 保证短语整体出现
_STICKY_HEAD_NUMBER_RE = re.compile(r"^\d+(%|x)?$", re.IGNORECASE)
_STICKY_TAILS_AFTER_NUMBER = {"off", "discount", "less", "cheaper", "saved", "savings", "save"}
_STICKY_TAILS_AFTER_DOLLAR = {"deal", "key", "off", "savings", "instant", "or"}

# Mac 上 Arial Black 一般在 Supplemental 文件夹, libass 通过 fontconfig 自动找
_ASS_STYLE_NAME = "TikTok"
_ASS_FONT = "Arial Black"
_ASS_FONTSIZE = 82
_ASS_CALLOUT_FONTSIZE = 150
_SUBTITLE_SAFE_W = TARGET_W - 180
_SUBTITLE_SAFE_TOP = int(TARGET_H * 0.56)
_SUBTITLE_SAFE_BOTTOM = int(TARGET_H * 0.78)

def _ass_header(subtitle_font: str | None = None, subtitle_highlight_font: str | None = None) -> str:
    main_font = font_registry.resolve_fontname(subtitle_font)
    highlight_font = font_registry.resolve_fontname(subtitle_highlight_font)
    return f"""[Script Info]
Title: TikTok Caption
ScriptType: v4.00+
PlayResX: {TARGET_W}
PlayResY: {TARGET_H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {_ASS_STYLE_NAME},{main_font},{_ASS_FONTSIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,6,3,5,80,80,0,1
Style: Highlight,{highlight_font},{_ASS_FONTSIZE},&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,6,3,5,80,80,0,1
Style: Callout,{highlight_font},{_ASS_CALLOUT_FONTSIZE},&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,4,0,1,12,8,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _merge_adjacent_tokens(words: list[Word]) -> list[Word]:
    """Whisper 会把 "40%" 拆成 "40" + "%"; "$5" 拆成 "$" + "5"。把这些紧粘 token 合并回去。"""
    if not words:
        return []
    out: list[Word] = []
    i = 0
    while i < len(words):
        cur = words[i]
        cur_t = cur.text.strip()
        if i + 1 < len(words):
            nxt = words[i + 1]
            nxt_t_raw = nxt.text.strip()
            nxt_t = nxt_t_raw.lstrip(".,!?:;")
            cur_t_core = cur_t.rstrip(".,!?:;")
            # 数字 + (%, x, X) -> "80%" / "2x"
            if cur_t_core.isdigit() and nxt_t in ("%", "x", "X"):
                # 保留 next 的尾部标点 (如果有)
                trail = nxt_t_raw[len(nxt_t):]
                out.append(Word(text=cur_t + nxt_t + trail,
                                start=cur.start, end=nxt.end))
                i += 2
                continue
            # "$" + 数字 -> "$5"
            if cur_t == "$" and nxt_t_raw and nxt_t_raw[0].isdigit():
                out.append(Word(text=cur_t + nxt_t_raw,
                                start=cur.start, end=nxt.end))
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def _must_keep_with_next(cur: Word, nxt: Word | None) -> bool:
    """关键短语必粘: 比如 80% + off, $5 + key, 不允许在中间断块。"""
    if not nxt:
        return False
    cur_t = cur.text.strip().lower().rstrip(".,!?:;")
    nxt_t = nxt.text.strip().lower().rstrip(".,!?:;")
    if not cur_t or not nxt_t:
        return False
    # "80" / "80%" / "80x" + (off/discount/...)
    if _STICKY_HEAD_NUMBER_RE.match(cur_t) and nxt_t in _STICKY_TAILS_AFTER_NUMBER:
        return True
    # "$5" + (deal/key/instant/...)
    if cur_t.startswith("$") and len(cur_t) >= 2 and cur_t[1].isdigit():
        if nxt_t in _STICKY_TAILS_AFTER_DOLLAR:
            return True
    return False


def _group_words(words: list[Word], max_chunk: int = 3) -> list[list[Word]]:
    """把 word 列表分块, 默认每块 1-3 词。

    断块规则:
    - 句号/感叹号/问号 强制断
    - 满 max_chunk 默认断
    - 但遇到"必粘对" (例如 80% + off, $5 + key) 允许扩到 max_chunk+2, 不在中间切
    """
    if not words:
        return []
    words = _merge_adjacent_tokens(words)
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        text = w.text.strip()
        last_char = text[-1] if text else ""
        is_sentence_end = last_char in ".!?"
        nxt = words[i + 1] if i + 1 < len(words) else None

        # 必粘对: 当前词跟下一个词必须放一起, 即使 chunk 已满也不在这里断
        if _must_keep_with_next(w, nxt) and len(cur) < max_chunk + 2:
            continue

        if is_sentence_end:
            chunks.append(cur)
            cur = []
            continue
        if len(cur) >= max_chunk:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _group_words_safe(words: list[Word]) -> list[list[Word]]:
    """按字幕安全宽度分块，确保主字幕不会因为长词/三词组合冲出画面。"""
    if not words:
        return []
    words = _merge_adjacent_tokens(words)
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        txt = _clean_word_for_display(w.text)
        candidate = cur + [w]
        candidate_text = " ".join(_clean_word_for_display(x.text) for x in candidate if x.text)
        too_wide = _estimate_ass_text_width(candidate_text, _ASS_FONTSIZE) > _SUBTITLE_SAFE_W
        if cur and too_wide:
            chunks.append(cur)
            cur = [w]
        else:
            cur = candidate

        text = w.text.strip()
        last_char = text[-1] if text else ""
        nxt = words[i + 1] if i + 1 < len(words) else None
        if _must_keep_with_next(w, nxt) and len(cur) < 4:
            continue
        if last_char in ".!?" or len(cur) >= 3:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _clean_word_for_display(w: str) -> str:
    """去掉首尾标点/引号, 保留 $ % x 等卖点符号, 转大写。"""
    return w.strip().strip("\"'“”‘’.,!?:;").upper()


def _is_highlight(text: str) -> bool:
    if not text:
        return False
    return bool(_HIGHLIGHT_PATTERN.search(text))


def _ass_escape_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


def _estimate_ass_text_width(text: str, font_size: int) -> float:
    """粗估 Arial Black / Montserrat Black 这类粗体大字宽度，用来避免 callout 出画。"""
    width = 0.0
    for ch in text:
        if ch == "\\":
            continue
        if ch.isspace():
            width += font_size * 0.34
        elif ch in ".,:;!|":
            width += font_size * 0.22
        elif ch in "MW@#%":
            width += font_size * 0.86
        elif ch.isdigit():
            width += font_size * 0.62
        else:
            width += font_size * 0.68
    return width


def _wrap_callout_text(text: str) -> list[str]:
    """把超长 callout 控制在安全框内；优先 1-2 行，避免 NEW EXPANSION 这种单行冲出画面。"""
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return []
    safe_w = TARGET_W - 140
    if _estimate_ass_text_width(" ".join(words), _ASS_CALLOUT_FONTSIZE) <= safe_w:
        return [" ".join(words)]

    best: list[str] | None = None
    best_score = 10**9
    for i in range(1, len(words)):
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        widths = [_estimate_ass_text_width(line, _ASS_CALLOUT_FONTSIZE) for line in lines]
        score = max(widths) + abs(widths[0] - widths[1]) * 0.25
        if score < best_score:
            best = lines
            best_score = score
    if best:
        return best

    # 没空格的超长词兜底：硬切成两行。
    word = words[0]
    mid = max(1, len(word) // 2)
    return [word[:mid], word[mid:]]


def _callout_scale(lines: list[str]) -> int:
    if not lines:
        return 100
    safe_w = TARGET_W - 120
    max_w = max(_estimate_ass_text_width(line, _ASS_CALLOUT_FONTSIZE) for line in lines)
    scale = int(min(100, max(58, safe_w / max(1.0, max_w) * 100)))
    return scale


def _subtitle_scale(chunk: list[Word]) -> int:
    text = " ".join(_clean_word_for_display(w.text) for w in chunk if w.text)
    max_w = _estimate_ass_text_width(text, _ASS_FONTSIZE)
    # 动画会临时放大到 106%，这里预留额外安全边距。
    return int(min(100, max(62, (_SUBTITLE_SAFE_W / 1.08) / max(1.0, max_w) * 100)))


def _subtitle_y(chunk_index: int) -> int:
    # 字幕固定在同一条视觉基线，避免一条上一条下的跳动感。
    y = int(TARGET_H * 0.71)
    return max(_SUBTITLE_SAFE_TOP, min(_SUBTITLE_SAFE_BOTTOM, y))


def _build_ass(
    scenes: list[SceneRender],
    dst: Path,
    *,
    subtitle_font: str | None = None,
    subtitle_highlight_font: str | None = None,
) -> None:
    """生成 TikTok 风格 ASS 字幕。

    Layer 0 (主字幕): chunk 整体显示, 词被说时 yellow flash + slight scale punch,
                      关键词 (数字/价格/CTA) 永久黄色 + 入场 punch
    Layer 1 (callout): scene 有 callout 字段时, 画面中央叠加超大字弹出动画
    """
    all_words: list[Word] = []
    for s in scenes:
        all_words.extend(s.words)

    chunks = _group_words_safe(all_words)

    events: list[str] = []
    pos_x = TARGET_W // 2
    # 主字幕放在画面下方安全区, 避开:
    #   - 顶部 0-25%: 游戏素材常烧死 logo / UI / 任务名
    #   - 底部 84-100%: TikTok 自身的用户名 / 描述 / icon 区

    for ci, chunk in enumerate(chunks):
        if not chunk:
            continue
        chunk_start = max(0.0, chunk[0].start - 0.12)
        chunk_end = chunk[-1].end + 0.28
        # 跟下一块衔接: 不重叠
        if ci + 1 < len(chunks):
            next_dialogue_start = max(0.0, chunks[ci + 1][0].start - 0.12)
            if chunk_end > next_dialogue_start:
                chunk_end = max(chunk_start + 0.20, next_dialogue_start - 0.01)

        # 默认不用逐词闪烁：关闭 Whisper 后词级时间是估算的，闪烁会显得不同步。
        # 这里按短语块整体出现，关键词保持黄色，观感更稳。
        segs: list[str] = []
        for w in chunk:
            txt = _clean_word_for_display(w.text)
            if not txt:
                continue
            escaped = _ass_escape_text(txt)
            if _is_highlight(txt):
                segs.append(f"{{\\rHighlight\\1c&H00FFFF&}}{escaped}{{\\r{_ASS_STYLE_NAME}}}")
            else:
                segs.append(f"{{\\1c&HFFFFFF&}}{escaped}")

        text_field = " ".join(segs)
        if not text_field:
            continue

        scale = _subtitle_scale(chunk)
        pos_y = _subtitle_y(ci)
        prefix = f"{{\\an5\\pos({pos_x},{pos_y})\\fscx{scale}\\fscy{scale}\\fad(60,80)}}"
        events.append(
            f"Dialogue: 0,{_ass_ts(chunk_start)},{_ass_ts(chunk_end)},"
            f"{_ASS_STYLE_NAME},,0,0,0,,{prefix}{text_field}"
        )

    # Layer 1: Number / Price Callout 大字 (画面中部偏上, 主字幕在其下方, 视觉层次: callout 主, 字幕辅)
    callout_x = TARGET_W // 2
    callout_y = int(TARGET_H * 0.42)
    for s in scenes:
        callout_text = (s.callout or "").strip().upper()
        if not callout_text:
            continue
        cs = s.scene_start_in_final + 0.15
        # callout 最多显示 1.5s 或 scene 剩余时长 - 0.2s 的较小值
        ce = min(s.scene_start_in_final + s.scene_duration - 0.2, cs + 1.5)
        if ce <= cs + 0.3:
            continue
        # 缩放弹入 + 轻微抖动 (frz -3 → 3 → 0)
        # 超长 callout 自动换行/缩放，避免烧字幕时左右出画。
        lines = _wrap_callout_text(callout_text)
        if not lines:
            continue
        scale = _callout_scale(lines)
        start_scale = max(24, int(scale * 0.4))
        safe = r"\N".join(_ass_escape_text(line) for line in lines)
        events.append(
            f"Dialogue: 1,{_ass_ts(cs)},{_ass_ts(ce)},Callout,,0,0,0,,"
            f"{{\\an5\\pos({callout_x},{callout_y})\\fad(80,150)"
            f"\\fscx{start_scale}\\fscy{start_scale}\\t(0,140,\\fscx{scale}\\fscy{scale})"
            f"\\frz-3\\t(140,300,\\frz3)\\t(300,440,\\frz-2)\\t(440,580,\\frz0)"
            f"}}{safe}"
        )

    dst.write_text(_ass_header(subtitle_font, subtitle_highlight_font) + "\n".join(events) + "\n", encoding="utf-8")


def _build_srt_from_words(scenes: list[SceneRender], dst: Path) -> None:
    """用户下载用的简单 SRT: 每块 3 词一条, 跟 ASS 时间对齐 (但没有卡拉 OK 效果)。"""
    all_words: list[Word] = []
    for s in scenes:
        all_words.extend(s.words)
    chunks = _group_words(all_words, max_chunk=3)

    lines: list[str] = []
    idx = 1
    for ci, chunk in enumerate(chunks):
        if not chunk:
            continue
        start_t = max(0.0, chunk[0].start - 0.05)
        end_t = chunk[-1].end + 0.20
        if ci + 1 < len(chunks):
            next_start = max(0.0, chunks[ci + 1][0].start - 0.05)
            if end_t > next_start:
                end_t = next_start - 0.01
        if end_t <= start_t:
            continue
        text = " ".join(_clean_word_for_display(w.text) for w in chunk if w.text)
        if not text:
            continue
        lines.append(str(idx))
        lines.append(f"{_srt_ts(start_t)} --> {_srt_ts(end_t)}")
        lines.append(text)
        lines.append("")
        idx += 1
    dst.write_text("\n".join(lines), encoding="utf-8")


def _ass_ts(sec: float) -> str:
    """ASS 时间格式: H:MM:SS.CC (CC=centiseconds, 0-99)."""
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _srt_ts(sec: float) -> str:
    """SRT 时间格式: HH:MM:SS,mmm."""
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


# ---------------------------------------------------------------------------
# 最终 ffmpeg: 烧 ASS 字幕 + mux 配音
# ---------------------------------------------------------------------------

def _burn_subtitles_and_mux(video: Path, audio: Path, ass_path: Path, dst: Path) -> None:
    """烧 ASS 字幕 (libass) + mux 配音。"""
    # ass 滤镜要 escape 路径里的 : (Windows 盘符), macOS 一般不需要但加上保险
    ass_arg = str(ass_path.resolve()).replace("\\", "/").replace(":", r"\\:")
    fonts_dir = str(font_registry.FONT_DIR.resolve()).replace("\\", "/").replace(":", r"\\:")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-vf", f"ass={ass_arg}:fontsdir={fonts_dir}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        log.warning("[compose] libass 烧字幕超时, 降级无字幕 mux")
        _mux_without_subs(video, audio, dst)
        return
    if result.returncode != 0:
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="ignore")[-800:]
        log.warning("[compose] libass 烧字幕失败, 降级无字幕 mux: %s", stderr_tail)
        _mux_without_subs(video, audio, dst)


def _mux_without_subs(video: Path, audio: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video), "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("无字幕 mux 超时 (300s)") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"无字幕 mux 也失败 (exit={result.returncode}): "
            f"{(result.stderr or b'').decode('utf-8', errors='ignore')[-500:]}"
        )
