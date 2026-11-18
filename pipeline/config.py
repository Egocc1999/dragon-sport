"""Configurable V1 settings for DRAGON SPORTS."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths

# 5DollarFootballAPI league IDs (API-Football v3 compatible host).
# Live-verified on 2026-11-17 against 5DollarFootballAPI for the 2026-27 season.
# These are this provider's IDs, not API-Football's 39/140/78/61.
# Override via pipeline/leagues.json.
DEFAULT_LEAGUES = {
    4160026622: {"name_zh": "英超", "name_en": "Premier League"},
    4212821298: {"name_zh": "西甲", "name_en": "La Liga"},
    686337048: {"name_zh": "德甲", "name_en": "Bundesliga"},
    3614399544: {"name_zh": "法甲", "name_en": "Ligue 1"},
}

DEFAULT_SEASON = 2026  # 2026-27 European season

API_BASE = "https://api-football.5dollarfootballapi.com"
API_KEY_HEADER = "x-apisports-key"
API_KEY_ENV = "FIVE_DOLLAR_FOOTBALL_API_KEY"
API_KEY_ENV_LEGACY = "API_FOOTBALL_KEY"  # optional legacy fallback

TIMEZONE = "Asia/Hong_Kong"
TIMEZONE_LABEL = "香港時間"

# Fetch windows
FIXTURE_PAST_DAYS = 45
FIXTURE_FUTURE_DAYS = 8

# Cache TTL (seconds). Odds update about every 3 hours per vendor docs.
TTL_LEAGUES = 7 * 24 * 3600
TTL_STANDINGS = 6 * 3600
TTL_FIXTURES = 3 * 3600
TTL_ODDS = 3 * 3600

# Model / selection — also documented in MODEL.md
FORM_WINDOW = 5
MIN_MATCHES_FORM = 3
MIN_MATCHES_POISSON = 5
MIN_MATCHES_VENUE = 4
POISSON_MAX_GOALS = 10
HOME_ADVANTAGE_LAMBDA = 1.10
HOME_ADVANTAGE_STRENGTH = 0.12
PPG_SCALE = 2.0
DRAW_PEAK = 0.85
DRAW_DECAY = 2.5

OU_LINE = 2.5
MAX_TIPS_PER_RUN = 4
ONE_TIP_PER_MATCH = True

STAKE_STANDARD = 1.0
STAKE_SMALL = 0.5
CONF_HIGH_FOR_FULL_STAKE = 0.62

EDGE_MIN = 0.04
PROB_MIN_1X2_WITH_ODDS = 0.48
CONF_MIN_WITH_ODDS = 0.58

PROB_MIN_1X2_NO_ODDS = 0.60
PROB_MIN_DRAW_NO_ODDS = 0.38
DRAW_MARGIN_NO_ODDS = 0.06

PROB_MIN_OU_NO_ODDS = 0.60
PROB_MIN_OU_WITH_ODDS = 0.52
EDGE_MIN_OU = 0.04

# Official finished statuses that may be graded
GRADEABLE_STATUS = {"FT", "AET", "PEN"}
# Use 90-minute (full-time) score for 1X2 and O/U in league matches
VOID_STATUS = {"PST", "CANC", "ABD", "AWD", "WO"}

PREFERRED_BOOKMAKER = "Bet365"


@dataclass(frozen=True)
class League:
    id: int
    name_zh: str
    name_en: str


def load_leagues_config() -> tuple[int, list[League]]:
    cfg_path = paths.CONFIG_PATH
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        season = int(raw.get("season", DEFAULT_SEASON))
        leagues = [
            League(id=int(item["id"]), name_zh=item["name_zh"], name_en=item["name_en"])
            for item in raw.get("leagues", [])
        ]
        if leagues:
            return season, leagues
    leagues = [
        League(id=i, name_zh=v["name_zh"], name_en=v["name_en"])
        for i, v in DEFAULT_LEAGUES.items()
    ]
    return DEFAULT_SEASON, leagues


def season_and_leagues() -> tuple[int, list[League]]:
    season, leagues = load_leagues_config()
    env_season = os.environ.get("DRAGON_SPORTS_SEASON")
    if env_season:
        season = int(env_season)
    return season, leagues


def league_by_id(league_id: int, leagues: list[League] | None = None) -> League | None:
    if leagues is None:
        _, leagues = load_leagues_config()
    for lg in leagues:
        if lg.id == league_id:
            return lg
    return None


def _key_from_env_text(text: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def read_api_key() -> str | None:
    """Return the 5DollarFootballAPI key, or None if it is not configured.

    Preference: FIVE_DOLLAR_FOOTBALL_API_KEY in the environment, then the same
    name in .api_key / .env, then the legacy API_FOOTBALL_KEY name.
    """
    env = os.environ.get(API_KEY_ENV, "").strip()
    if env:
        return env
    names = (API_KEY_ENV, API_KEY_ENV_LEGACY)
    for candidate in (paths.API_KEY_FILE, paths.ROOT / ".env"):
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if candidate.name == ".env":
            value = _key_from_env_text(text, names)
            if value:
                return value
            continue
        # .api_key may be raw key or KEY=value
        if "=" in text.splitlines()[0]:
            value = _key_from_env_text(text, names)
            if value:
                return value
        else:
            return text or None
    legacy = os.environ.get(API_KEY_ENV_LEGACY, "").strip()
    return legacy or None
