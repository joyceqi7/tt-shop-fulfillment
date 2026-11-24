"""英文脚本生成 + 素材片段选择。

输入:
  - 产品信息（游戏 CDKey 卖什么、卖点、价格）
  - 候选 clip 列表（带 description / tags / content_type / hook_score / duration）
  - 目标时长

输出:
  - script: 一份分镜化的英文脚本，明确每段对应哪个 clip_id、旁白、字幕、时长
"""
from __future__ import annotations

import os
import random
import re
from typing import Any

from app.llm.client import chat_json
from app.core.logger import get_logger

log = get_logger(__name__)


def _script_model() -> str | None:
    """脚本 JSON 生成优先用快模型；DeepSeek Pro/reasoning 模型容易在网页主流程里超时或截断。"""
    explicit = (os.getenv("LLM_SCRIPT_MODEL") or "").strip()
    if explicit:
        return explicit
    configured = (os.getenv("LLM_TEXT_MODEL") or "").strip().lower()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip().lower()
    if "deepseek" in base_url and ("v4" in configured or "pro" in configured or "reasoner" in configured):
        return "deepseek-chat"
    return None


SCRIPT_SYSTEM = """You are a senior TikTok ad copywriter for a shop selling digital game CD-keys.
Your output is a compact JSON ad spec that a video editor can render directly. NO commentary, JSON only.

OUTPUT SCHEMA:
{
  "title": "<short English title>",
  "total_duration": <int seconds>,
  "scenes": [
    {
      "clip_id": <int from candidate list>,
      "duration": <float seconds>,
      "voiceover": "<English voiceover line>",
      "caption": "<6-word ALL CAPS fallback caption>",
      "fx_hint": "<cold_open | zoom_punch | none>",
      "callout": "<optional 1-3 word overlay, see CALLOUT RULES>"
    }
  ]
}

############### THE 4-PART AD STRUCTURE (MEMORIZE) ###############
Every script MUST follow this arc:

  1. HOOK     (scene 0, ~2s, cold_open)        : grab attention, drop the offer
  2. SETUP    (1-2 scenes, ~3-5s total)         : introduce the game, build interest
  3. PROOF    (2-3 scenes, ~6-10s total)        : gameplay/feature highlights, social proof
  4. CTA      (1 scene, ~2s, zoom_punch)        : tell viewer where/how to buy

############### SCENE COUNT & DURATION (HARD LIMITS) ###############
- TOTAL scenes: 7 to 9 for 30s ads, 5 to 7 for shorter ads. ABSOLUTE MAX 9.
  Quality > quantity. Don't pad with filler scenes like "Look at the gameplay."
  If you find yourself writing "trust me", "no joke", "look at this" as a STANDALONE scene,
  delete that scene and merge its voiceover into the previous scene.
- Per-scene duration:
    * scene[0] (hook):           1.8 - 2.2 s
    * scene[1..n-2] (mid):       2.2 - 3.2 s   <- aim for THIS range, not the min
    * scene[last] (cta):         2.0 - 2.5 s
- HARD MIN: no scene shorter than 1.8 s. HARD MAX: no scene longer than 3.5 s.
- Sum of durations MUST equal target_duration ± 10% (so for target=30s, total ≥ 27s).
  If your scenes sum to 22s when target was 30s, INCREASE each scene by 0.3-0.5s
  (write a slightly longer voiceover) until you hit the budget.

############### VOICEOVER (THE STORY MUST FLOW) ###############
- Total words across all scenes ≈ target_duration * 2.2 (so 30s ad ≈ 66 words, NOT 100).
- Each scene: 6-14 words. NEVER under 5 words (no fragment-style "Epic battle.").
- The voiceover read end-to-end MUST sound like ONE PERSON talking, NOT a list of taglines.
  Use connector words: "and", "plus", "because", "look", "see", "trust me", "no joke", "right now".

✗ BAD (independent fragments — what we got last time):
  scene 0: "40 percent off Diablo 4."
  scene 1: "Epic battle awaits."
  scene 2: "Grab your key now."
  scene 3: "Fight demons in hell."
  scene 4: "Unleash fiery abilities."

✓ GOOD (one continuous monologue):
  scene 0: "Wait. Diablo 4 is forty percent off this week."
  scene 1: "Yeah, the full game plus the brand-new Lord of Hatred expansion."
  scene 2: "You get the new Paladin class, dark wings, and an insane open world."
  scene 3: "And every key activates instantly on Steam. No keys lost, no fakes."
  scene 4: "Tap the link, grab one before May 20, and stop paying full price."

############### HOOK FORMULAS (PICK ONE FOR scene[0], VARY EVERY TIME) ###############
Do NOT always start with "Wait." Rotate hooks. Pick one style:
- "Stop scrolling. <product> is <deal> today."
- "This <product> deal is actually crazy."
- "Before you pay full price, check this."
- "Nobody told me <product> dropped this low."
- "Quick heads up: <product> is on sale."
- "If <product> is on your list, move fast."
- "Here is the <product> deal people are missing."
Avoid using the same first word as the examples if possible. Max 10 words for scene[0]. scene[0].fx_hint = "cold_open".

############### CALLOUT (USE SPARINGLY) ###############
Callout is a HUGE animated text overlay drawn on top of the video. It DUPLICATES on-screen attention
to a price or CTA. Overusing it ruins the ad.

- Use callout on EXACTLY 2 scenes per video:
    1. The "deal reveal" scene (the one announcing the offer) — callout = the price/percent
    2. The CTA scene — callout = product-link style phrase ("SHOP NOW" / "CHECK DEAL" / "TAP PRODUCT")

🚨 CRITICAL ANTI-HALLUCINATION RULE 🚨
- NUMBERS, PRICES, PERCENTAGES in a callout MUST appear in the supplied `product_info` text.
- If `product_info` says "80 percent off, $5 key" → callouts can be "80% OFF" or "$5 KEY". OK.
- If `product_info` says NOTHING about price → callout 1 MUST be a generic phrase like
  "BIG SALE" / "REAL KEY" / "ON STEAM" (NO numbers, NO fake prices like "$9 KEY").
- INVENTING a price (e.g. writing "$9 KEY" when product_info never mentioned $9) is FORBIDDEN —
  it creates false advertising and will get the account banned.

- ALL OTHER scenes MUST set callout to "" (empty string).
- callout text MUST NOT just repeat what the on-screen video already says
  (e.g. if the clip is the Diablo 4 logo, do NOT set callout to "DIABLO 4").
- callout MUST be ≤ 3 words, ALL CAPS.

Same rule applies to VOICEOVER: do NOT mention specific prices/percentages unless they appear
in product_info. If product_info has no numbers, the voiceover should describe the OFFER
("a real Steam key, instant delivery, way cheaper than retail") instead of inventing percentages.

############### FX HINT ###############
- scene[0].fx_hint = "cold_open"
- The "deal reveal" scene + CTA scene → fx_hint = "zoom_punch"
- All other scenes → fx_hint = "none"

############### NUMBER PRONUNCIATION (TTS-FRIENDLY) ###############
- WRITE prices as words: "9 bucks", "5 dollars", "forty percent off". NEVER "$9" / "80%" inside voiceover.
- Callouts CAN use symbols ("$9 KEY", "80% OFF") — they're drawn, not spoken.

############### CLIP SELECTION ###############
- clip_id MUST come from the provided candidate list.
- duration MUST be ≤ that candidate's actual duration.
- Match content_type to slot: hook=cinematic / shocking; setup=gameplay / cinematic; proof=gameplay / feature;
  cta=ui / logo / product.
- `used` field = times this clip already appeared in past videos. STRONGLY prefer used=0 over used>2.
- DO NOT repeat the same clip_id within one script.
- Keep JSON compact: no pretty printing, no extra fields, no long prose.
- Prefer the first valid clips in the candidate list. Do not spend time optimizing clip choice.
"""

SCRIPT_USER_TEMPLATE = """Product: {product_name}
Product info / selling points:
{product_info}

Target duration: {target_duration} seconds
Style: {style}
Style direction:
{style_direction}

Candidate clips (you must reuse these clip_ids):
{candidates}

Hook variation seed: {hook_seed}
Avoid starting the first voiceover with: {avoid_hook_starts}
"""

_HOOK_STARTS = [
    "Stop scrolling.",
    "Quick heads up:",
    "Before you pay full price,",
    "Nobody told me",
    "This deal is",
    "If this game is on your list,",
    "Here is the deal:",
    "Do not miss this:",
]

_STYLE_DIRECTIONS = {
    "energetic": (
        "High-energy ad. Use short punchy sentences, stronger verbs, fast transitions, "
        "and a confident shopping tone. Avoid calm documentary wording."
    ),
    "urgent": (
        "Urgent limited-time sale. Mention deadline/limited stock early if supplied. "
        "Use pressure, scarcity, and direct CTA without sounding spammy."
    ),
    "chill": (
        "Relaxed recommendation. Softer voice, fewer hype words, still clear about the offer."
    ),
    "funny": (
        "Light playful tone. Add one mild joke or surprising line, but keep the offer clear."
    ),
}


def generate_script(
    product_name: str,
    product_info: str,
    target_duration: int,
    style: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """candidates 每项形如:
       {id, duration, description, tags, content_type, mood, hook_score}
    """
    cand_lines = []
    for c in candidates:
        cand_lines.append(
            f"- id={c['id']} | dur={c['duration']:.1f}s | type={c.get('content_type','?')} "
            f"| hook={c.get('hook_score',0):.2f} | used={c.get('used_count', 0)} "
            f"| mood={c.get('mood','?')} "
            f"| tags={','.join((c.get('tags') or [])[:5])} | desc=\"{(c.get('description') or '')[:120]}\""
        )

    user = SCRIPT_USER_TEMPLATE.format(
        product_name=product_name,
        product_info=product_info or "(none)",
        target_duration=target_duration,
        style=style,
        style_direction=_STYLE_DIRECTIONS.get(style, _STYLE_DIRECTIONS["energetic"]),
        candidates="\n".join(cand_lines) if cand_lines else "(no candidates)",
        hook_seed=random.randint(1000, 9999),
        avoid_hook_starts="Wait, Wait., wait",
    )

    try:
        script = chat_json(SCRIPT_SYSTEM, user, model=_script_model(), temperature=0.25, max_tokens=5000)
    except Exception as e:
        raise RuntimeError(f"LLM 脚本生成失败，请检查 LLM 配置：{e}") from e

    if not script.get("scenes"):
        log.warning("[script] LLM 未返回 scenes, 用更强约束重试一次: %s", str(script)[:500])
        retry_user = (
            user
            + "\n\nThe previous response was invalid. Return ONLY a JSON object, not markdown. "
            + "The JSON MUST include a non-empty scenes array using clip_id values from the candidate list."
        )
        try:
            script = chat_json(SCRIPT_SYSTEM, retry_user, model=_script_model(), temperature=0.15, max_tokens=5000)
        except Exception as e:
            raise RuntimeError(f"LLM 脚本重试失败：{e}") from e
        if not script.get("scenes"):
            raise RuntimeError(f"LLM 未返回有效脚本 scenes。原始返回: {str(script)[:800]}")

    script["scenes"] = _validate_scenes(
        script.get("scenes", []),
        candidates,
        product_info=product_info or "",
    )
    script["scenes"] = _stretch_scene_durations(script["scenes"], candidates, target_duration)
    # validate 可能把所有 scenes 全 drop 掉 (比如 LLM 返回了一堆无效 clip_id)。
    # 正式版本不再生成模板兜底脚本, 直接失败并暴露问题。
    if not script["scenes"]:
        raise RuntimeError("LLM 返回的所有场景都校验不通过：clip_id 无效、重复或字段不合法")

    _vary_hook_opening(script["scenes"])
    script.setdefault("title", product_name)
    script["total_duration"] = sum(s["duration"] for s in script["scenes"])
    return script


_ALLOWED_FX = {"cold_open", "zoom_punch", "whip", "speed_ramp", "none", ""}
_MAX_SCENES = 9                    # 硬上限, prompt 里建议 5-7
_MIN_SCENE_DURATION = 1.8          # 比 1.5 更保守, 防 LLM 踩下限
_MAX_SCENE_DURATION_HOOK = 2.2     # scene 0 上限
_MAX_SCENE_DURATION = 3.5          # 其他 scene 上限
_STRETCH_MAX_HOOK = 2.6
_STRETCH_MAX_MID = 4.2
_STRETCH_MAX_CTA = 3.2
_MIN_VOICEOVER_WORDS = 4           # 不允许 < 4 词的"碎句标签"
_MAX_CALLOUTS = 2                  # 整条 ad 最多 2 个 callout

# 兜底通用 callout - 当 LLM 写的 callout 含 product_info 没出现过的数字时, 用这些代替
_GENERIC_DEAL_CALLOUTS = ["BIG SALE", "REAL KEY", "INSTANT KEY", "ON STEAM"]
_GENERIC_CTA_CALLOUTS = ["SHOP NOW", "CHECK DEAL", "TAP PRODUCT", "VIEW DEAL"]

# 匹配 callout / voiceover 里的数字片段 (含 $9, 80%, 9 bucks, twenty 等)
_NUMBER_TOKEN_RE = re.compile(r"\$?\s*\d+(?:\.\d+)?\s*%?")
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "twentieth", "thirtieth", "fortieth", "fiftieth",
    "half", "quarter", "third",
}


def _extract_numbers(text: str) -> set[str]:
    """提取文本里的'数字字面值'集合, 用于校验 callout 里的数字是否真在 product_info 出现。"""
    text = (text or "").lower()
    out: set[str] = set()
    for m in _NUMBER_TOKEN_RE.finditer(text):
        digit = re.sub(r"\s+", "", m.group(0))
        # 标准化 $9 → 9, 80% → 80
        digit = digit.replace("$", "").replace("%", "")
        if digit:
            out.add(digit)
    # 英文数字词
    for word in re.findall(r"[a-z]+", text):
        if word in _NUMBER_WORDS:
            out.add(word)
    return out


def _callout_has_fabricated_number(callout: str, allowed_numbers: set[str]) -> bool:
    """callout 里如果有数字, 且数字不在 allowed_numbers (product_info 提取的) 中, 视为脑补。"""
    callout_numbers = _extract_numbers(callout)
    if not callout_numbers:
        return False
    return not callout_numbers.issubset(allowed_numbers)


def _first_text(scene: dict, keys: tuple[str, ...], max_len: int = 400) -> str:
    """LLM 偶尔会把 voiceover 写成 narration/script/text 等别名, 这里统一收口。"""
    for key in keys:
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:max_len]
    return ""


def _fallback_voiceover(index: int, scene: dict, *, product_info: str) -> str:
    """绝不把空配音文案交给前端；至少用字幕/卖点生成一句可渲染旁白。"""
    caption = _first_text(scene, ("caption", "subtitle", "on_screen_text", "text"), max_len=120)
    callout = _first_text(scene, ("callout",), max_len=32)
    info = re.sub(r"\s+", " ", product_info or "").strip()
    if caption:
        return caption.title()[:400]
    if callout:
        return f"{callout.title()} on this deal right now."[:400]
    if index == 0:
        return "Wait, this game deal is live right now."
    if info:
        return f"Here is the deal: {info[:120]}"
    return "Grab this digital game key while the deal is available."


def _stretch_scene_durations(
    scenes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    target_duration: int,
) -> list[dict[str, Any]]:
    """模型经常算不准总时长；后端按素材上限把短脚本拉到目标时长附近。"""
    if not scenes:
        return scenes
    min_total = float(target_duration) * 0.9
    total = sum(float(s.get("duration") or 0) for s in scenes)
    if total >= min_total:
        return scenes

    clip_durations = {int(c["id"]): float(c.get("duration") or 0) for c in candidates}
    gap = min_total - total
    for idx, scene in enumerate(scenes):
        if gap <= 0:
            break
        current = float(scene.get("duration") or 0)
        clip_cap = clip_durations.get(int(scene.get("clip_id") or 0), current)
        if idx == 0:
            scene_cap = _STRETCH_MAX_HOOK
        elif idx == len(scenes) - 1:
            scene_cap = _STRETCH_MAX_CTA
        else:
            scene_cap = _STRETCH_MAX_MID
        cap = max(current, min(clip_cap, scene_cap))
        add = min(gap, max(0.0, cap - current))
        if add > 0:
            scene["duration"] = round(current + add, 2)
            gap -= add

    if gap > 0.01:
        log.warning(
            "[script] 无法完全拉齐目标时长 target=%ss min=%.1fs current=%.1fs gap=%.1fs",
            target_duration,
            min_total,
            sum(float(s.get("duration") or 0) for s in scenes),
            gap,
        )
    return scenes


def _vary_hook_opening(scenes: list[dict[str, Any]]) -> None:
    """快模型容易反复用 Wait 开头；这里做轻量改写，提升多次生成差异。"""
    if not scenes:
        return
    first = scenes[0]
    vo = str(first.get("voiceover") or "").strip()
    if not vo:
        return
    replacement = random.choice(_HOOK_STARTS)

    repetitive_prefix = re.compile(
        r"^(?:wait|stop scrolling|quick heads up|before you pay full price|nobody told me|"
        r"this deal is|do not miss this|here is the deal)[\s,\.!:-]*",
        re.I,
    )
    if repetitive_prefix.match(vo):
        rest = repetitive_prefix.sub("", vo, count=1).strip()
        if rest:
            first["voiceover"] = f"{replacement} {rest[:1].lower() + rest[1:]}".strip()[:400]
        else:
            first["voiceover"] = replacement[:400]
        return


def _validate_scenes(
    scenes: list[dict],
    candidates: list[dict],
    product_info: str = "",
) -> list[dict]:
    """clip_id / duration / fx_hint / callout 合法化, 同时硬限 scene 数量和 callout 数量。

    product_info: 用户填写的产品信息原文, 用来校验 callout 里出现的数字/价格是否真实。
    """
    by_id = {c["id"]: c for c in candidates}
    allowed_numbers = _extract_numbers(product_info)
    out: list[dict] = []
    seen_clip_ids: set[int] = set()
    callout_count = 0
    for i, s in enumerate(scenes):
        if len(out) >= _MAX_SCENES:
            log.info("[script] 达到 scene 数上限 %d, 丢弃后续", _MAX_SCENES)
            break

        raw_cid = s.get("clip_id")
        try:
            cid = int(raw_cid)
        except (TypeError, ValueError):
            log.warning("[script] 丢弃无法解析的 clip_id=%s", raw_cid)
            continue
        if cid not in by_id:
            log.warning("[script] 丢弃无效 clip_id=%s", raw_cid)
            continue
        if cid in seen_clip_ids:
            # 同一 ad 不允许 clip 重复
            log.info("[script] 跳过重复 clip_id=%s", cid)
            continue
        seen_clip_ids.add(cid)

        clip = by_id[cid]
        dur = float(s.get("duration") or clip["duration"])
        max_cap = _MAX_SCENE_DURATION_HOOK if i == 0 else _MAX_SCENE_DURATION
        dur = max(_MIN_SCENE_DURATION, min(dur, clip["duration"], max_cap))

        fx_hint = str(s.get("fx_hint") or "").strip().lower()
        if fx_hint not in _ALLOWED_FX:
            fx_hint = "none"
        if i == 0 and fx_hint in ("none", ""):
            fx_hint = "cold_open"

        # callout 只在前 2 个出现的 callout-bearing scene 上保留, 防滥用
        callout = str(s.get("callout") or "").strip()[:32]
        # 仅在 cold_open / zoom_punch 的 scene 上 callout 才有意义
        if callout and fx_hint not in ("cold_open", "zoom_punch"):
            callout = ""

        # ⛔ 反"脑补价格": callout 含数字但 product_info 里没出现 → 用通用 callout
        if callout and _callout_has_fabricated_number(callout, allowed_numbers):
            is_cta = (i == len(scenes) - 1) or "SHOP" in callout.upper() or "CHECK" in callout.upper() or "TAP" in callout.upper()
            replacement = (_GENERIC_CTA_CALLOUTS if is_cta else _GENERIC_DEAL_CALLOUTS)[callout_count % 4]
            log.warning(
                "[script] scene %d callout=%r 含 product_info 未提及的数字, 替换为 %r (allowed=%s)",
                i, callout, replacement, sorted(allowed_numbers)[:6]
            )
            callout = replacement

        if callout:
            if callout_count >= _MAX_CALLOUTS:
                callout = ""
            else:
                callout_count += 1

        # 兜底: DeepSeek 偶尔会返回 narration/script/text 等别名, 不允许前端出现空配音文案。
        vo = _first_text(
            s,
            (
                "voiceover",
                "narration",
                "spoken_line",
                "spoken",
                "script",
                "line",
                "text",
                "copy",
                "dialogue",
                "audio",
            ),
        )
        if len(vo.split()) < _MIN_VOICEOVER_WORDS:
            log.warning("[script] scene %d voiceover 太短, 不足 %d 词: %r",
                        i, _MIN_VOICEOVER_WORDS, vo)
            vo = _fallback_voiceover(i, s, product_info=product_info)

        out.append({
            "clip_id": int(cid),
            "duration": round(dur, 2),
            "voiceover": vo,
            "caption": str(s.get("caption") or "").strip()[:80],
            "fx_hint": fx_hint,
            "callout": callout,
        })
    return out
