"""用多模态 LLM 给每个 Clip 打结构化标签。

输入: 这个片段的若干抽帧图 + 对应的 ASR 文字
输出: { description, tags, content_type, mood, hook_score }

如果 LLM 调用失败或没配 API Key，会回退到一个保守的默认标签，保证流程不中断。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.llm.client import vision_describe
from app.core.logger import get_logger

log = get_logger(__name__)


TAGGING_PROMPT = """You are a video editor assistant for a TikTok shop that sells game CD-keys.
Analyze the given video frames (they are sequential samples from a single short shot)
and the spoken transcript, then return a JSON object with the following fields:

{{
  "description": "<1 short English sentence describing what visually happens in this shot>",
  "tags": ["<3-6 lowercase English keyword tags, e.g. unboxing, gameplay, reaction, ui, logo>"],
  "content_type": "<one of: hook | problem | product | proof | cta | broll | filler>",
  "mood": "<one of: exciting | calm | funny | urgent | curious | neutral>",
  "hook_score": <float 0~1, how likely this shot works as the FIRST 3 seconds of a TikTok ad>,
  "has_real_person": <true | false>,
  "real_person_note": "<short reason for the has_real_person value, <=80 chars>"
}}

Content-type guidance:
- "hook" shots have surprising visuals or bold motion in the first frame.
- "product" shots show packaging, screens of a game UI, or the actual CD-key / digital code.
- "proof" shots show fast gameplay, win moments, kill streaks.
- "cta" shots have on-screen text like "buy now", "link in bio", "limited offer".
- "broll" is generic supporting footage (hands, keyboards, ambient).
- Be honest. If unsure, use "filler" with low hook_score.

CRITICAL — has_real_person rule (we must avoid using real people's likeness):
Return TRUE if you can see ANY of:
  * A real human face filmed by a camera (streamer talking head, IRL vlog, podcast guest, webcam overlay even in a corner)
  * Real human hands clearly shown handling physical objects (unboxing on camera, real keyboard close-up of human hands)
  * Crowd footage / esports event audience / real-world people walking
Return FALSE if the shot only contains:
  * In-game 3D characters, NPCs, or the player's avatar (e.g. Diablo Lilith, COD operator, Genshin character) — these are CGI, NOT real people
  * Pre-rendered CG cutscenes (even if photoreal, they are computer-generated assets owned by the game studio)
  * Anime / cartoon / illustrated characters
  * Pure gameplay HUD, menus, loading screens, environment shots
  * Logos, key art, no humans at all
When in doubt (tiny blurry face cam, very brief flash of a real face), prefer TRUE — we err on the side of caution.
"real_person_note" must briefly explain WHICH frames and WHY, e.g.
  "Streamer webcam overlay in top-right of all frames" or "Only CGI Lilith from D4 cutscene, no real person".

Transcript for this shot (may be empty):
\"\"\"{transcript}\"\"\"
"""


def _fallback_tags(transcript: str, reason: str = "VLM unavailable") -> dict[str, Any]:
    """LLM 不可用时的兜底标签。

    设计取舍：以前为了"安全"在 VLM 挂掉时默认 has_real_person=True，
    实际效果是 VLM 一旦配错就把整个素材库全标真人, 生成视频时全部被过滤、什么都剩不下,
    远比"漏标一两个真人"更糟。改成 False + tag 里加 "needs-review" 标记,
    让用户在素材库里能一眼看出哪些是 VLM 没看上的, 自己复核。
    """
    return {
        "description": (transcript[:80] or "untitled shot").strip(),
        "tags": ["unclassified", "vlm-failed"],
        "content_type": "filler",
        "mood": "neutral",
        "hook_score": 0.2,
        "has_real_person": False,
        "real_person_note": f"{reason} — please review manually",
    }


def tag_clip(frame_paths: list[Path], transcript: str = "") -> dict[str, Any]:
    if not frame_paths:
        return _fallback_tags(transcript, reason="No frames extracted")

    prompt = TAGGING_PROMPT.format(transcript=(transcript or "").replace('"', "'"))
    try:
        result = vision_describe(frame_paths, prompt, json_mode=True)
    except Exception as e:
        # 把错误前 200 字记下来, 配错 API key/model 时一目了然
        msg = str(e)[:200]
        log.warning(
            "[tag] vision_describe 失败: %s | frames=%d | model=%s",
            msg, len(frame_paths), _vision_model_hint(),
        )
        return _fallback_tags(transcript, reason=f"VLM call failed: {msg}")

    if not isinstance(result, dict) or not result:
        log.warning(
            "[tag] vision_describe 返回空/非 dict, 视为 VLM 失败 | frames=%d",
            len(frame_paths),
        )
        return _fallback_tags(transcript, reason="VLM returned empty JSON")

    out = _fallback_tags(transcript, reason="defaults")
    out.update({
        "description": str(result.get("description") or out["description"])[:300],
        "tags": _normalize_tags(result.get("tags")),
        "content_type": str(result.get("content_type") or out["content_type"]).lower(),
        "mood": str(result.get("mood") or out["mood"]).lower(),
        "hook_score": _clamp(result.get("hook_score"), 0.0, 1.0, default=0.3),
        "has_real_person": _as_bool(result.get("has_real_person"), default=False),
        "real_person_note": str(result.get("real_person_note") or "")[:256] or None,
    })
    return out


def _vision_model_hint() -> str:
    """日志辅助: 在 VLM 失败时把当前在用的模型/base_url 打出来, 排错更快."""
    try:
        from app.core.config import settings as _s
        return f"{_s.LLM_VISION_MODEL} @ {_s.vision_base_url or '(default)'}"
    except Exception:
        return "?"


def _as_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "yes", "y", "1"}:
            return True
        if s in {"false", "no", "n", "0", ""}:
            return False
    return default


def _normalize_tags(raw: Any) -> list[str]:
    if not raw:
        return ["unclassified"]
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",")]
    if isinstance(raw, list):
        tags = [str(t).strip().lower() for t in raw if str(t).strip()]
        return tags[:8] or ["unclassified"]
    return ["unclassified"]


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))
