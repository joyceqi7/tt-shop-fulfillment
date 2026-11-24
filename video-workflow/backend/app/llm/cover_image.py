"""TikTok 竖屏封面 — AI 出图，输出 1080×1920 JPEG。

默认 **阿里云 DashScope 通义万相**（``wan2.6-t2i`` 同步 HTTP，与千问/百炼同一把 Key），
无需 OpenAI。

可选 ``IMAGE_PROVIDER=openai`` 走 ``images.generate``（DALL·E 等）。

说明：**DeepSeek 不提供文生图**，仅用 DeepSeek Key 无法出封面；需 DashScope Key（通义万相）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from app.core.config import settings
from app.core import models
from app.core.db import db_session
from app.core.logger import get_logger
from app.llm.client import chat_text

log = get_logger(__name__)

TARGET_COVER_W, TARGET_COVER_H = 1080, 1920
IMAGE_CONNECT_TIMEOUT_SECONDS = 10.0
IMAGE_READ_TIMEOUT_SECONDS = 180.0
FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
TITLE_FONT_PATH = FONT_DIR / "BebasNeue-Regular.ttf"
BODY_FONT_PATH = FONT_DIR / "Montserrat-Black.ttf"

DASHSCOPE_MULTIMODAL_URL = {
    "cn": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "intl": "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "us": "https://dashscope-us.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
}

STYLE_LOCK = """GLOBAL STYLE (LOCKED — apply every time, same art direction):
Vertical mobile-commerce poster, 9:16 full-bleed, ultra high resolution feel.
Premium PC game deal / digital download storefront aesthetic — NOT a screenshot, NOT UI mockup, NOT a product render.
Background: deep charcoal and near-black with soft electric cyan + magenta rim light and subtle
hexagonal tech grid fading into darkness. A few soft lens flares, no clutter.
Composition: CLEAR empty negative space in the UPPER 45% for later text overlay (keep dark and clean,
only soft gradients and faint particles — absolutely no readable text in that zone).
Lower 55%: abstract heroic gaming energy — storefront light beams, loot shimmer, neon data streams,
geometric shards, energy waves, subtle embers. Use atmosphere and shapes only; do not place a central object.
Lighting: cinematic high contrast, crisp edges, commercial TikTok Shop thumbnail quality.
FORBIDDEN: real human faces, photoreal celebrities, watermarks, QR codes, tiny unreadable text,
game HUD, stock photo hands, national flags, blood/gore, physical keys, keyholes, lock icons,
key silhouettes, giant central key objects, literal CD-shaped keys, access cards, code cards, padlocks,
floating product icons, readable letters or words."""

BRIEF_SYSTEM = """You write ONE English sentence (max 40 words) for an image-generation brief.
Describe ONLY abstract mood, lighting, palette, and energy level.
Never name specific video games, movie titles, or real brands. Use generic phrases like
"dark fantasy action", "sci-fi shooter", "cozy RPG" instead of franchise names.
Never include prices, percentages, URLs, calls to action, the word "key", "CD key", "CDKEY", or "code".
Output plain text — no quotes, no bullet points."""


def _env(name: str, fallback: str = "") -> str:
    return (os.getenv(name) or fallback or "").strip()


def _http_timeout(read_seconds: float = IMAGE_READ_TIMEOUT_SECONDS) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=read_seconds,
        connect=IMAGE_CONNECT_TIMEOUT_SECONDS,
        read=read_seconds,
        write=IMAGE_CONNECT_TIMEOUT_SECONDS,
        pool=IMAGE_CONNECT_TIMEOUT_SECONDS,
    )


def _clean_product_title(title: str) -> str:
    """封面主标题只保留产品名；CDKEY/Key/Code 是售卖形态，不该进主视觉或标题。"""
    s = re.sub(r"\s+", " ", (title or "").strip())
    s = re.sub(
        r"\b(?:cd\s*key|cdkey|steam\s*key|activation\s*(?:key|code)|digital\s*(?:key|code)|"
        r"game\s*key|license\s*(?:key|code)|redeem\s*code|download\s*code|key|code)\b",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b(?:steam|on|for|global|region\s*free|instant|delivery|official|account)\b", " ", s, flags=re.I)
    s = re.sub(r"[-_+/|:;,.()[\]{}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "PC GAME DEAL"


def _build_mood_line(
    product_name: str,
    product_info: str | None,
    game: str | None,
    style: str | None,
    script_json: dict[str, Any] | None,
) -> str:
    # 不把 product_name / product_info 直接喂给出图模型，避免 "CDKEY" 被画成实体钥匙。
    title = ""
    if script_json and isinstance(script_json, dict):
        title = _clean_product_title((script_json.get("title") or "").strip())[:120]
    user = (
        f"Internal abstract title hint, cleaned of storefront terms: {title or '(none)'}\n"
        f"Category hint (generic): {game or 'PC digital games'}\n"
        f"Energy tag from marketing style: {style or 'energetic'}\n"
        "Important: describe genre mood only; do not mention or depict keys, locks, cards, text, logos, or products.\n"
    )
    try:
        line = chat_text(BRIEF_SYSTEM, user, temperature=0.22).strip()
        line = re.sub(r"\s+", " ", line)
        return line[:400]
    except Exception as e:
        log.warning("[cover] mood LLM 失败, 用兜底: %s", e)
        return "High-energy dark fantasy digital storefront mood with cyan and magenta accents."


def _full_prompt(mood_line: str) -> str:
    return (
        f"{STYLE_LOCK}\n\n"
        f"EPISODE-SPECIFIC MOOD (this listing only): {mood_line}\n\n"
        "Technical: single coherent illustration, portrait 9:16, sharp focus, no borders, no split panels."
    )


def _font_from_paths(size: int, paths: list[Path | str]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in paths:
        try:
            return ImageFont.truetype(str(p), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _font_from_paths(
        size,
        [
            TITLE_FONT_PATH,
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ],
    )


def _body_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _font_from_paths(
        size,
        [
            BODY_FONT_PATH,
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Bold.ttf",
        ],
    )


def _font(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    return _font_from_paths(size, candidates)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return box[2] - box[0], box[3] - box[1]


def _wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    title = re.sub(r"\s+", " ", (title or "").strip())
    if not title:
        return ["PC GAME DEAL"]

    words = title.split(" ")
    if len(words) > 1:
        lines: list[str] = []
        cur = ""
        for word in words:
            nxt = word if not cur else f"{cur} {word}"
            if _text_size(draw, nxt.upper(), font)[0] <= max_w:
                cur = nxt
            else:
                if cur:
                    lines.append(cur.upper())
                cur = word
            if len(lines) >= 2:
                break
        if cur and len(lines) < 2:
            lines.append(cur.upper())
    else:
        lines = [title.upper()]

    out: list[str] = []
    for line in lines[:2]:
        while line and _text_size(draw, line, font)[0] > max_w:
            line = line[:-1].rstrip()
        out.append(line or "GAME DEAL")
    return out or ["PC GAME DEAL"]


def _wrap_text_by_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    max_w: int,
    max_lines: int,
    max_chars_per_line: int,
) -> list[str]:
    """封面文字必须完整落在安全区内；长词按宽度拆行，不直接截断。"""
    text = re.sub(r"\s+", " ", (text or "").strip().upper())
    if not text:
        return []

    def split_long_word(word: str) -> list[str]:
        chunks: list[str] = []
        cur = ""
        for ch in word:
            nxt = f"{cur}{ch}"
            if cur and (len(nxt) > max_chars_per_line or _text_size(draw, nxt, font)[0] > max_w):
                chunks.append(cur)
                cur = ch
            else:
                cur = nxt
        if cur:
            chunks.append(cur)
        return chunks

    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        pieces = split_long_word(word)
        for piece in pieces:
            nxt = piece if not cur else f"{cur} {piece}"
            if len(nxt) <= max_chars_per_line and _text_size(draw, nxt, font)[0] <= max_w:
                cur = nxt
                continue
            if cur:
                lines.append(cur)
                if len(lines) >= max_lines:
                    return lines
            cur = piece
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    fitted: list[str] = []
    for line in lines[:max_lines]:
        if line:
            fitted.append(line)
    return fitted


def _fit_cover_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font_factory,
    max_w: int,
    max_lines: int,
    max_chars_per_line: int,
    start_size: int = 132,
    min_size: int = 72,
) -> tuple[list[str], ImageFont.ImageFont]:
    """从大到小试字号，直到所有文字完整落入安全宽度。"""
    for size in range(start_size, min_size - 1, -8):
        font = font_factory(size)
        lines = _wrap_text_by_width(
            draw,
            text,
            font,
            max_w=max_w,
            max_lines=max_lines,
            max_chars_per_line=max_chars_per_line,
        )
        joined_words = re.sub(r"\s+", "", (text or "").upper())
        joined_lines = re.sub(r"\s+", "", "".join(lines))
        all_words_fit = not joined_words or joined_words == joined_lines
        if lines and all(_text_size(draw, line, font)[0] <= max_w for line in lines) and all_words_fit:
            return lines, font
    font = font_factory(min_size)
    return _wrap_text_by_width(
        draw,
        text,
        font,
        max_w=max_w,
        max_lines=max_lines,
        max_chars_per_line=max_chars_per_line,
    ), font


def _extract_offer(product_info: str | None, script_json: dict[str, Any] | None) -> tuple[str, str]:
    text_parts = [product_info or ""]
    if script_json and isinstance(script_json, dict):
        for scene in script_json.get("scenes") or []:
            if isinstance(scene, dict):
                text_parts.append(str(scene.get("callout") or ""))
    text = " ".join(text_parts)

    patterns = [
        r"\b(?:save\s+)?(?:up\s+to\s+)?\d{1,3}\s*%\s*(?:off|discount)?\b",
        r"\b\d{1,3}\s*%\s*(?:off|discount)\b",
        r"[$￥¥]\s*\d+(?:\.\d{1,2})?",
        r"\b\d+(?:\.\d+)?\s*(?:折|元|usd|dollars?)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            offer = re.sub(r"\s+", " ", m.group(0)).strip().upper()
            if "%" in offer and "OFF" not in offer and "DISCOUNT" not in offer:
                offer = f"{offer} OFF"
            return offer[:42], "LIMITED DEAL"
    return "", ""


def _cover_text_overrides(script_json: dict[str, Any] | None) -> dict[str, str | None] | None:
    if not isinstance(script_json, dict):
        return None
    raw = script_json.get("cover_text")
    if not isinstance(raw, dict):
        return None
    out: dict[str, str | None] = {}
    for key in ("title", "offer", "kicker", "line2", "line3"):
        val = raw.get(key)
        if val is None:
            out[key] = None
        else:
            out[key] = re.sub(r"\s+", " ", str(val)).strip()
    return out


def _edge_darkness_score(im: Image.Image) -> float:
    gray = im.convert("L")
    w, h = gray.size
    band_h = max(8, h // 16)
    top = ImageStat.Stat(gray.crop((0, 0, w, band_h))).mean[0]
    bottom = ImageStat.Stat(gray.crop((0, h - band_h, w, h))).mean[0]
    return max(0.0, 45.0 - top) + max(0.0, 45.0 - bottom)


def _thumb_fill_score(path: Path) -> float:
    try:
        with Image.open(path) as im:
            w, h = im.size
            if w <= 0 or h <= 0:
                return -999.0
            aspect = h / w
            target = TARGET_COVER_H / TARGET_COVER_W
            score = 100.0 - abs(aspect - target) * 70.0
            if aspect < 1.25:
                score -= 80.0
            if aspect >= 1.45:
                score += 35.0
            score -= _edge_darkness_score(im) * 0.9
            return score
    except Exception:
        return -999.0


def _extract_clip_cover_frame(clip: models.Clip) -> Path | None:
    if not clip.file_path:
        return None
    src = Path(str(clip.file_path))
    if not src.exists() or not src.is_file():
        return None
    out_dir = settings.storage_path / "thumbnails" / "cover_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"clip_{int(clip.id):06d}_cover.jpg"
    if dst.exists() and dst.is_file():
        return dst
    ss = max(0.15, float(clip.duration or 1.0) * 0.5)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{ss:.2f}",
                "-i", str(src),
                "-frames:v", "1",
                "-q:v", "3",
                str(dst),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return dst if dst.exists() and dst.is_file() else None
    except Exception as e:
        log.warning("[cover] 从原 clip 抽封面帧失败 clip=%s: %s", clip.id, e)
        return None


def _clip_thumbnail_paths(clip_ids: list[int] | None, game: str | None = None) -> tuple[list[Path], str]:
    paths: list[Path] = []
    notes: list[str] = []
    try:
        with db_session() as db:
            clips: list[models.Clip] = []
            if clip_ids:
                rows = db.query(models.Clip).filter(models.Clip.id.in_(clip_ids)).all()
                by_id = {int(c.id): c for c in rows}
                clips = [by_id[int(cid)] for cid in clip_ids if int(cid) in by_id]
                notes.append(f"clip_ids={clip_ids[:12]}, found={len(rows)}")

            # 旧任务可能没有 used_clip_ids；此时从同游戏素材库找原 clip 抽帧兜底，不从成片截。
            if not clips:
                q = db.query(models.Clip).join(models.Source)
                if game:
                    q = q.filter(models.Source.game == game)
                clips = (
                    q.filter(models.Clip.has_real_person == False)  # noqa: E712
                    .order_by(models.Clip.hook_score.desc(), models.Clip.id.desc())
                    .limit(16)
                    .all()
                )
                notes.append(f"fallback_pool={len(clips)}, game={game or '(all)'}")

            for clip in clips:
                p = Path(str(clip.thumbnail_path)) if clip.thumbnail_path else None
                if p and p.exists() and p.is_file():
                    paths.append(p)
                    continue
                extracted = _extract_clip_cover_frame(clip)
                if extracted:
                    paths.append(extracted)
                else:
                    file_ok = bool(clip.file_path and Path(str(clip.file_path)).exists())
                    thumb_ok = bool(p and p.exists())
                    notes.append(f"clip#{clip.id}: thumb={thumb_ok}, file={file_ok}")
    except Exception as e:
        log.warning("[cover] 查询/抽取 clip 封面帧失败: %s", e)
        notes.append(f"error={e}")
    return sorted(paths, key=_thumb_fill_score, reverse=True), "; ".join(notes[-8:])


def _cover_history(script_json: dict[str, Any] | None) -> list[str]:
    if not isinstance(script_json, dict):
        return []
    raw = script_json.get("cover_base_history")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    last = script_json.get("cover_base")
    return [str(last)] if last else []


def _cover_base_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def remember_cover_base(script_json: dict[str, Any] | None, base_id: str | None) -> dict[str, Any]:
    script = dict(script_json or {})
    if not base_id:
        return script
    history = _cover_history(script)
    history = [x for x in history if x != base_id]
    history.insert(0, base_id)
    script["cover_base"] = base_id
    script["cover_base_history"] = history[:8]
    return script


def _trim_dark_edges(im: Image.Image) -> Image.Image:
    """裁掉素材本身自带的上下/左右黑边，再进入 9:16 铺满流程。"""
    gray = im.convert("L")
    w, h = gray.size
    left, top, right, bottom = 0, 0, w, h
    min_bright = 18

    def row_mean(y: int) -> float:
        return ImageStat.Stat(gray.crop((0, y, w, y + 1))).mean[0]

    def col_mean(x: int) -> float:
        return ImageStat.Stat(gray.crop((x, 0, x + 1, h))).mean[0]

    while top < h * 0.22 and row_mean(top) < min_bright:
        top += 1
    while bottom > h * 0.78 and row_mean(bottom - 1) < min_bright:
        bottom -= 1
    while left < w * 0.18 and col_mean(left) < min_bright:
        left += 1
    while right > w * 0.82 and col_mean(right - 1) < min_bright:
        right -= 1

    if right - left < w * 0.55 or bottom - top < h * 0.55:
        return im
    if (left, top, right, bottom) == (0, 0, w, h):
        return im
    return im.crop((left, top, right, bottom))


def _make_keyframe_background(src: Path) -> Image.Image:
    im = _trim_dark_edges(Image.open(src).convert("RGB"))
    # 参考短视频封面：关键帧铺满，强模糊 + 暗角，文字成为唯一清晰主体。
    bg = ImageOps.fit(im, (TARGET_COVER_W, TARGET_COVER_H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    bg = bg.filter(ImageFilter.GaussianBlur(16))

    # 轻微叠回一层原图轮廓，避免全糊成色块。
    ghost = ImageOps.fit(im, (TARGET_COVER_W, TARGET_COVER_H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    bg = Image.blend(bg, ghost.filter(ImageFilter.GaussianBlur(4)), 0.22)

    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 46))
    bg_rgba = Image.alpha_composite(bg.convert("RGBA"), overlay)

    return bg_rgba.convert("RGB")


def _draw_text_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int],
    stroke_width: int = 7,
) -> None:
    x, y = xy
    w, _ = _text_size(draw, text, font)
    tx = x - w // 2
    # 黑色厚描边 + 轻微投影，接近参考图的高对比标题。
    draw.text((tx + 5, y + 6), text, font=font, fill=(0, 0, 0), stroke_width=stroke_width + 2, stroke_fill=(0, 0, 0))
    draw.text((tx, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=(0, 0, 0))


def _draw_viral_title(im: Image.Image, product_name: str, product_info: str | None, script_json: dict[str, Any] | None) -> Image.Image:
    overrides = _cover_text_overrides(script_json)
    if not overrides:
        title = _clean_product_title(product_name)
        offer = ""
        kicker = ""
    else:
        raw_title = overrides.get("title") or ""
        title = _clean_product_title(raw_title) if raw_title else ""
        offer = (overrides.get("line2") if overrides.get("line2") is not None else overrides.get("offer") or "").upper()
        kicker = (overrides.get("line3") if overrides.get("line3") is not None else overrides.get("kicker") or "").upper()

    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    safe_w = 900
    title_font = _title_font(150)
    offer_font = _body_font(118)
    small_font = _body_font(58)
    title_lines: list[str] = []
    offer_lines: list[str] = []
    kicker_lines: list[str] = []
    if title:
        title_lines, title_font = _fit_cover_lines(
            draw,
            title,
            font_factory=_title_font,
            max_w=safe_w,
            max_lines=3,
            max_chars_per_line=14,
            start_size=172,
            min_size=116,
        )
    if offer:
        offer_lines, offer_font = _fit_cover_lines(
            draw,
            offer,
            font_factory=_body_font,
            max_w=safe_w,
            max_lines=2,
            max_chars_per_line=18,
            start_size=132,
            min_size=58,
        )
    if kicker:
        kicker_lines, small_font = _fit_cover_lines(
            draw,
            kicker,
            font_factory=_body_font,
            max_w=safe_w,
            max_lines=1,
            max_chars_per_line=18,
            start_size=66,
            min_size=46,
        )
    title_gap = int(getattr(title_font, "size", 142) * 0.94)
    offer_gap = int(getattr(offer_font, "size", 110) * 1.05)
    y = 350

    # 第一行黄色、后续白色，模拟参考里的“黄白大字”信息层级。
    for i, line in enumerate(title_lines[:3]):
        color = (255, 231, 38) if i == 0 else (255, 255, 255)
        _draw_text_shadow(draw, (TARGET_COVER_W // 2, y), line, title_font, fill=color, stroke_width=12)
        y += title_gap

    for line in offer_lines:
        _draw_text_shadow(draw, (TARGET_COVER_W // 2, y + 8), line, offer_font, fill=(255, 231, 38), stroke_width=11)
        y += offer_gap

    # 辅助信息小一号，不用霓虹胶囊，保持短视频封面感。
    for line in kicker_lines:
        _draw_text_shadow(draw, (TARGET_COVER_W // 2, y), line, small_font, fill=(255, 255, 255), stroke_width=8)

    bbox = layer.getbbox()
    if bbox:
        group = layer.crop(bbox)
        gw, gh = group.size
        target_w, target_h = 920, 720
        scale = min(target_w / max(1, gw), target_h / max(1, gh))
        # 保证完整落在安全区，不再为“顶满”强行放大到出画。
        scale = min(1.12, scale)
        group = group.resize((int(gw * scale), int(gh * scale)), Image.Resampling.LANCZOS)
        im_rgba = im.convert("RGBA")
        x = (TARGET_COVER_W - group.width) // 2
        x = max(42, min(x, TARGET_COVER_W - group.width - 42))
        y = (TARGET_COVER_H - group.height) // 2 - 25
        y = max(250, min(y, TARGET_COVER_H - group.height - 240))
        im_rgba.alpha_composite(group, (x, y))
        im = im_rgba.convert("RGB")

    draw = ImageDraw.Draw(im)

    # 左上角加一个小黄标，进一步贴近参考封面语法。
    badge_font = _body_font(34)
    badge_text = "HOT"
    bw, bh = _text_size(draw, badge_text, badge_font)
    draw.rounded_rectangle((32, 30, 32 + bw + 28, 30 + bh + 20), radius=12, fill=(255, 231, 38))
    draw.text((46, 39), badge_text, font=badge_font, fill=(20, 20, 20))
    return im


def _generate_keyframe_cover(
    *,
    clip_ids: list[int] | None,
    game: str | None,
    product_name: str,
    product_info: str | None,
    script_json: dict[str, Any] | None,
    dst: Path,
    force_new_base: bool = False,
) -> tuple[bool, str, str | None]:
    thumbs, thumb_reason = _clip_thumbnail_paths(clip_ids, game=game)
    if not thumbs:
        return False, thumb_reason or "没有可用 clip 缩略图/原 clip 文件", None
    if force_new_base and len(thumbs) > 1:
        history = set(_cover_history(script_json))
        fresh = [p for p in thumbs if _cover_base_id(p) not in history]
        if not fresh:
            last = next(iter(history), "")
            fresh = [p for p in thumbs if _cover_base_id(p) != last]
        if fresh:
            thumbs = fresh

    failed: list[str] = []
    for thumb in thumbs:
        try:
            im = _make_keyframe_background(thumb)
            im = _draw_viral_title(im, product_name, product_info, script_json)
            im.save(dst, format="JPEG", quality=settings.COVER_JPEG_QUALITY, optimize=True)
            return True, "", _cover_base_id(thumb)
        except Exception as e:
            log.warning("[cover] 用缩略图生成封面失败 thumb=%s: %s", thumb, e)
            failed.append(f"{thumb.name}: {e}")
    detail = "; ".join(failed[-5:]) or thumb_reason
    return False, f"候选关键帧处理失败 ({len(thumbs)} 张): {detail}", None


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int],
    stroke_width: int,
) -> None:
    x, y = pos
    w, _ = _text_size(draw, text, font)
    draw.text((x - w // 2, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def _soften_literal_objects(im: Image.Image) -> Image.Image:
    """把下半部 AI 可能画出的实体物件降权，保留氛围感，降低“钥匙/卡片”误读风险。"""
    base = im.convert("RGBA")
    y0, y1 = 760, 1700
    region = base.crop((0, y0, TARGET_COVER_W, y1))
    blurred = region.filter(ImageFilter.GaussianBlur(9))
    dark = Image.new("RGBA", region.size, (0, 0, 0, 55))
    blurred = Image.alpha_composite(blurred, dark)

    mask = Image.new("L", region.size, 0)
    mp = mask.load()
    h = y1 - y0
    for y in range(h):
        # 中段强、上下弱，避免整张图像“糊掉”。
        t = 1 - abs((y / max(1, h - 1)) - 0.5) * 2
        a = int(115 * max(0, min(1, t)))
        for x in range(TARGET_COVER_W):
            mp[x, y] = a

    base.paste(blurred, (0, y0), mask)
    return base.convert("RGB")


def _decorate_cover(im: Image.Image, product_name: str, product_info: str | None, script_json: dict[str, Any] | None) -> Image.Image:
    """AI 只画背景，产品名/折扣由本地叠字，避免模型画错字或把 CD key 画成实体钥匙。"""
    im = _soften_literal_objects(im.convert("RGB"))
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 顶部加暗底，保证任何背景都能承载电商信息。
    grad = Image.new("L", (1, 780), 0)
    gp = grad.load()
    for y in range(780):
        gp[0, y] = max(0, int(215 * (1 - y / 780)))
    alpha = grad.resize((TARGET_COVER_W, 780))
    shade = Image.new("RGBA", (TARGET_COVER_W, 780), (0, 0, 0, 0))
    shade.putalpha(alpha)
    overlay.alpha_composite(shade, (0, 0))

    display_title = _clean_product_title(product_name)
    title_font = _title_font(124)
    title_lines = _wrap_title(draw, display_title, title_font, 910)
    offer, kicker = _extract_offer(product_info, script_json)
    offer_font = _body_font(74)
    kicker_font = _body_font(42)

    # 霓虹标题阴影/描边，模拟“艺术字包装”。
    y = 170 if len(title_lines) == 1 else 120
    for line in title_lines:
        for dx, dy, color in [(-5, 0, (0, 245, 255)), (5, 0, (255, 52, 196)), (0, 7, (0, 0, 0))]:
            _draw_centered_text(draw, (TARGET_COVER_W // 2 + dx, y + dy), line, title_font, fill=color, stroke_fill=(0, 0, 0), stroke_width=5)
        _draw_centered_text(
            draw,
            (TARGET_COVER_W // 2, y),
            line,
            title_font,
            fill=(255, 255, 246),
            stroke_fill=(8, 10, 18),
            stroke_width=8,
        )
        y += 122

    # 折扣/利益点胶囊，放在标题下方，用户第一眼知道这是商品封面。
    pill_y = y + 34
    offer_w, offer_h = _text_size(draw, offer, offer_font)
    pad_x, pad_y = 48, 24
    pill = (
        (TARGET_COVER_W - offer_w) // 2 - pad_x,
        pill_y - pad_y,
        (TARGET_COVER_W + offer_w) // 2 + pad_x,
        pill_y + offer_h + pad_y + 10,
    )
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(pill, radius=36, fill=(255, 44, 167, 160), outline=(0, 245, 255, 220), width=4)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    overlay.alpha_composite(glow)
    draw.rounded_rectangle(pill, radius=36, fill=(17, 20, 32, 235), outline=(0, 245, 255, 255), width=4)
    _draw_centered_text(
        draw,
        (TARGET_COVER_W // 2, pill_y),
        offer,
        offer_font,
        fill=(255, 232, 72),
        stroke_fill=(28, 10, 4),
        stroke_width=4,
    )

    kw, kh = _text_size(draw, kicker, kicker_font)
    badge = (TARGET_COVER_W // 2 - kw // 2 - 32, pill[3] + 22, TARGET_COVER_W // 2 + kw // 2 + 32, pill[3] + 22 + kh + 28)
    draw.rounded_rectangle(badge, radius=24, fill=(0, 0, 0, 180), outline=(255, 255, 255, 150), width=2)
    _draw_centered_text(draw, (TARGET_COVER_W // 2, badge[1] + 12), kicker, kicker_font, fill=(235, 250, 255), stroke_fill=(0, 0, 0), stroke_width=2)

    return Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")


def _save_bytes_to_cover_jpeg(
    raw_bytes: bytes,
    dst: Path,
    *,
    product_name: str = "",
    product_info: str | None = None,
    script_json: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    try:
        im = Image.open(BytesIO(raw_bytes)).convert("RGB")
        im = im.resize((TARGET_COVER_W, TARGET_COVER_H), Image.Resampling.LANCZOS)
        im = _decorate_cover(im, product_name, product_info, script_json)
        im.save(dst, format="JPEG", quality=settings.COVER_JPEG_QUALITY, optimize=True)
        return True, ""
    except Exception as e:
        return False, f"图片保存失败: {e}"


def _format_api_error(e: Exception) -> str:
    parts: list[str] = [str(e).strip() or repr(e)]
    body = getattr(e, "body", None)
    if body is not None and not isinstance(body, str):
        try:
            parts.append(json.dumps(body, ensure_ascii=False)[:400])
        except Exception:
            parts.append(str(body)[:400])
    elif isinstance(body, str) and body.strip():
        parts.append(body.strip()[:400])
    resp = getattr(e, "response", None)
    if resp is not None:
        txt = getattr(resp, "text", None) or ""
        if txt.strip():
            parts.append(txt.strip()[:400])
    msg = " | ".join(p for p in parts if p)
    return (msg[:900] + "…") if len(msg) > 900 else msg


# --------------------------------------------------------------------------- DashScope 万相
def _dashscope_endpoint() -> str:
    reg = (_env("DASHSCOPE_REGION", settings.DASHSCOPE_REGION) or "cn").lower().strip()
    if reg in ("intl", "sg", "singapore", "ap-southeast"):
        reg = "intl"
    elif reg in ("us", "virginia", "us-east"):
        reg = "us"
    else:
        reg = "cn"
    return DASHSCOPE_MULTIMODAL_URL.get(reg, DASHSCOPE_MULTIMODAL_URL["cn"])


def _wan_size_string() -> str:
    """万相 size 为 width*height；默认 1080*1920（9:16）。非法时回退官方推荐。"""
    s = (_env("IMAGE_SIZE", settings.IMAGE_SIZE) or "1080*1920").strip().replace("x", "*").replace("X", "*")
    if re.match(r"^\d+\*\d+$", s):
        return s
    return "1080*1920"


def _extract_wan_image_url(data: dict[str, Any]) -> str | None:
    if data.get("code") and str(data.get("code")).lower() not in ("", "null", "none"):
        return None
    try:
        choices = data.get("output", {}).get("choices") or []
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content") or []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image" and part.get("image"):
                return str(part["image"])
            if part.get("image"):
                return str(part["image"])
    except Exception:
        return None
    return None


def _generate_cover_dashscope(
    *,
    prompt: str,
    dst: Path,
    gen_id: int,
    product_name: str,
    product_info: str | None,
    script_json: dict[str, Any] | None,
) -> tuple[bool, str]:
    key = (
        _env("DASHSCOPE_API_KEY", settings.DASHSCOPE_API_KEY)
        or _env("VISION_API_KEY", settings.VISION_API_KEY)
        or _env("LLM_API_KEY", settings.LLM_API_KEY)
    )
    if not key:
        return (
            False,
            "未配置 DashScope API Key。请在 .env 设置 DASHSCOPE_API_KEY=（阿里云百炼/通义控制台）"
            "；若视觉与聊天已用千问，也可与 VISION_API_KEY / LLM_API_KEY 共用同一把 Key。"
            "DeepSeek 无文生图，不能替代。",
        )

    endpoint = _dashscope_endpoint()
    model = (_env("IMAGE_MODEL", settings.IMAGE_MODEL) or "wan2.6-t2i").strip()
    if model.lower().startswith(("dall-e", "gpt-image")):
        model = "wan2.6-t2i"
    size = _wan_size_string()

    body: dict[str, Any] = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt[:2100]}],
                }
            ]
        },
        "parameters": {
            "prompt_extend": False,
            "watermark": False,
            "n": 1,
            "size": size,
        },
    }

    try:
        r = httpx.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=_http_timeout(),
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        tail = ""
        try:
            tail = (e.response.text or "")[:600]
        except Exception:
            pass
        return False, f"DashScope HTTP {e.response.status_code}: {tail or str(e)}"
    except Exception as e:
        return False, f"DashScope 请求异常: {_format_api_error(e)}"

    if data.get("code"):
        return False, f"DashScope 业务错误: {data.get('code')} — {data.get('message', data)[:500]}"

    url = _extract_wan_image_url(data)
    if not url:
        return False, f"万相返回无图片 URL，原始 JSON 片段: {json.dumps(data, ensure_ascii=False)[:600]}"

    try:
        img_r = httpx.get(url, timeout=_http_timeout(120.0))
        img_r.raise_for_status()
        raw = img_r.content
    except Exception as e:
        return False, f"下载万相图片失败: {e}"

    ok, err = _save_bytes_to_cover_jpeg(
        raw,
        dst,
        product_name=product_name,
        product_info=product_info,
        script_json=script_json,
    )
    if ok:
        log.info("[cover] DashScope 已写入 gen=%s → %s", gen_id, dst.name)
    return ok, err


# --------------------------------------------------------------------------- OpenAI 兼容
def _resolved_openai_base_url() -> str:
    explicit_url = _env("IMAGE_BASE_URL", settings.IMAGE_BASE_URL).rstrip("/")
    if explicit_url:
        return explicit_url
    return _env("LLM_BASE_URL", settings.LLM_BASE_URL).rstrip("/")


def _image_client_openai() -> OpenAI:
    key = _env("IMAGE_API_KEY", settings.IMAGE_API_KEY) or _env("LLM_API_KEY", settings.LLM_API_KEY)
    base_url = _resolved_openai_base_url()
    if not key or not base_url:
        raise RuntimeError("IMAGE_PROVIDER=openai 但缺少 IMAGE_API_KEY/LLM_API_KEY 或 IMAGE_BASE_URL/LLM_BASE_URL")
    return OpenAI(api_key=key, base_url=base_url, timeout=_http_timeout(), max_retries=1)


def _dalle_size() -> str:
    s = (_env("IMAGE_SIZE", settings.IMAGE_SIZE) or "1024x1792").strip()
    allowed = {"1024x1024", "1792x1024", "1024x1792"}
    return s if s in allowed else "1024x1792"


def _images_openai_generate(cli: OpenAI, *, model: str, prompt: str, size: str, prefer_b64: bool):
    kwargs: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    if (model or "").lower().startswith("dall-e-3"):
        kwargs["quality"] = "standard"
    if prefer_b64:
        kwargs["response_format"] = "b64_json"
    return cli.images.generate(**kwargs)


def _generate_cover_openai(
    *,
    prompt: str,
    dst: Path,
    gen_id: int,
    product_name: str,
    product_info: str | None,
    script_json: dict[str, Any] | None,
) -> tuple[bool, str]:
    base = _resolved_openai_base_url()
    if not _env("IMAGE_API_KEY", settings.IMAGE_API_KEY):
        bl = base.lower()
        if "deepseek" in bl or "api.anthropic.com" in bl:
            return (
                False,
                "IMAGE_PROVIDER=openai 时不能使用纯对话网关作为 IMAGE_BASE_URL。"
                "请改用 IMAGE_PROVIDER=dashscope（通义万相），或配置 OpenAI 出图地址与 IMAGE_API_KEY。",
            )

    cli = _image_client_openai()
    model = (_env("IMAGE_MODEL", settings.IMAGE_MODEL) or "dall-e-3").strip()
    if model.lower().startswith("wan"):
        model = "dall-e-3"
    size = _dalle_size()
    resp = None
    last_err = ""
    for prefer_b64 in (True, False):
        try:
            resp = _images_openai_generate(cli, model=model, prompt=prompt, size=size, prefer_b64=prefer_b64)
            break
        except Exception as e:
            last_err = _format_api_error(e)
            log.warning("[cover] openai images.generate b64=%s: %s", prefer_b64, last_err)
            resp = None
    if resp is None:
        return False, f"OpenAI 文生图失败: {last_err} | base={base[:96]}"

    data = resp.data
    if not data:
        return False, "OpenAI 返回空 data"

    b64 = getattr(data[0], "b64_json", None)
    url = getattr(data[0], "url", None)
    raw_bytes: bytes | None = None
    if b64:
        try:
            raw_bytes = base64.b64decode(b64)
        except Exception as e:
            return False, f"Base64 解码失败: {e}"
    elif url:
        try:
            r = httpx.get(url, timeout=_http_timeout(120.0))
            r.raise_for_status()
            raw_bytes = r.content
        except Exception as e:
            return False, f"下载图片 URL 失败: {e}"
    if not raw_bytes:
        return False, "OpenAI 响应中既无 b64_json 也无 url"

    ok, err = _save_bytes_to_cover_jpeg(
        raw_bytes,
        dst,
        product_name=product_name,
        product_info=product_info,
        script_json=script_json,
    )
    if ok:
        log.info("[cover] OpenAI 已写入 gen=%s → %s", gen_id, dst.name)
    return ok, err


# --------------------------------------------------------------------------- 对外入口
def generate_cover_file(
    *,
    gen_id: int,
    product_name: str,
    product_info: str | None,
    game: str | None,
    style: str | None,
    script_json: dict[str, Any] | None,
    dst: Path,
    clip_ids: list[int] | None = None,
    force_new_base: bool = False,
) -> tuple[bool, str, str | None]:
    if not settings.COVER_IMAGE_ENABLED:
        return False, "COVER_IMAGE_ENABLED=false", None

    prov = (settings.IMAGE_PROVIDER or "dashscope").lower().strip()
    dst.parent.mkdir(parents=True, exist_ok=True)

    ok_keyframe, keyframe_err, base_id = _generate_keyframe_cover(
        clip_ids=clip_ids,
        game=game,
        product_name=product_name,
        product_info=product_info,
        script_json=script_json,
        dst=dst,
        force_new_base=force_new_base,
    )
    if ok_keyframe:
        log.info("[cover] 关键帧模糊封面已写入 gen=%s → %s", gen_id, dst.name)
        return True, "", base_id
    log.warning("[cover] 关键帧封面不可用，不再回退 AI 生图: %s", keyframe_err)
    return False, f"没有可用视频关键帧，未生成 AI 图片：{keyframe_err}", None
