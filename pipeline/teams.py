"""Map API English club names to Hong Kong Traditional Chinese names."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from . import paths

_PUNCT = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _PUNCT.sub("", text.lower())


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    if not paths.TEAM_NAMES_PATH.exists():
        return {}
    raw = json.loads(paths.TEAM_NAMES_PATH.read_text(encoding="utf-8"))
    aliases = raw.get("aliases", {})
    out: dict[str, str] = {}
    for src, zh in aliases.items():
        if src.startswith("_"):
            continue
        out[_norm(src)] = zh
    return out


def team_name_zh(english_name: str | None) -> str:
    """Return the HK Traditional name, or the original English name if unmapped."""
    if not english_name:
        return "未知名球隊"
    zh = _table().get(_norm(english_name))
    return zh or english_name


def match_title(home_en: str, away_en: str) -> tuple[str, str, str]:
    home = team_name_zh(home_en)
    away = team_name_zh(away_en)
    return home, away, f"{home} 對 {away}"
