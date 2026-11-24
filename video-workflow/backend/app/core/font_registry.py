from __future__ import annotations

from pathlib import Path


FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
DEFAULT_FONT_KEY = "impact"

FONT_REGISTRY = {
    "tiktok_sans": {"label": "TikTok Sans", "fontname": "TikTok Sans", "file": "TikTokSans-Regular.ttf"},
    "bebas_neue": {"label": "Bebas Neue", "fontname": "Bebas Neue", "file": "BebasNeue-Regular.ttf"},
    "proxima_nova": {"label": "Proxima Nova (Montserrat 替代)", "fontname": "Montserrat", "file": "ProximaNova-Bold.ttf"},
    "impact": {"label": "Impact (Archivo Black 替代)", "fontname": "Archivo Black", "file": "Impact.ttf"},
    "liquid_drippy": {"label": "Liquid & Drippy (Nosifer 替代)", "fontname": "Nosifer", "file": "LiquidDrippy.ttf"},
    "retro_groovy": {"label": "Retro Groovy (Oi 替代)", "fontname": "Oi", "file": "RetroGroovy.ttf"},
    "sharpshooter": {"label": "Sharpshooter (Black Ops One 替代)", "fontname": "Black Ops One", "file": "Sharpshooter.ttf"},
    "bombcat": {"label": "Bombcat (Bangers 替代)", "fontname": "Bangers", "file": "Bombcat.ttf"},
    "topuira": {"label": "Topuira (Cinzel Decorative 替代)", "fontname": "Cinzel Decorative", "file": "Topuira.ttf"},
    "regsrok": {"label": "Regsrok (Metamorphous 替代)", "fontname": "Metamorphous", "file": "Regsrok.ttf"},
}

SYSTEM_FONTS: set[str] = set()
FALLBACK_FONT = "Arial Black"


def font_available(key: str) -> bool:
    info = FONT_REGISTRY.get(key)
    if not info:
        return False
    if key in SYSTEM_FONTS:
        return True
    file = info.get("file")
    return bool(file and (FONT_DIR / str(file)).exists())


def resolve_fontname(key: str | None) -> str:
    normalized = (key or DEFAULT_FONT_KEY).strip().lower()
    info = FONT_REGISTRY.get(normalized)
    if info and font_available(normalized):
        return str(info["fontname"])
    return FALLBACK_FONT


def list_fonts() -> list[dict]:
    return [
        {
            "key": key,
            "label": str(info["label"]),
            "fontname": str(info["fontname"]),
            "available": font_available(key),
            "file": info.get("file"),
        }
        for key, info in FONT_REGISTRY.items()
    ]
