"""Build the public fixtures file for the website, sorted by kickoff time."""

from __future__ import annotations

import json
from typing import Any

from . import paths, teams, timeutil

STATUS_LABELS = {
    "NS": "未開",
    "1H": "上半場",
    "HT": "中場",
    "2H": "下半場",
    "FT": "完場",
    "AET": "完場",
    "PEN": "完場",
    "LIVE": "進行中",
    "PST": "延期",
    "CANC": "取消",
    "ABD": "腰斬",
}

# Official or in-play scores may be shown. Never treat NS/PST 0-0 as a result.
SCORED_STATUSES = {"1H", "HT", "2H", "FT", "AET", "PEN", "LIVE"}
FINISHED_STATUSES = {"FT", "AET", "PEN"}

_WEEKDAYS = "一二三四五六日"


def _date_label_hk(dt) -> str:
    local = timeutil.to_hk(dt)
    return f"{local.year}年{local.month}月{local.day}日（週{_WEEKDAYS[local.weekday()]}）"


def _league_zh(item: dict[str, Any], leagues: list[dict[str, Any]]) -> str:
    lid = (item.get("league") or {}).get("id")
    for lg in leagues:
        if lg.get("id") == lid or str(lg.get("id")) == str(lid):
            return str(lg.get("name_zh") or "")
    name = ((item.get("league") or {}).get("name") or "").lower()
    aliases = {
        "premier league": "英超",
        "england premier league": "英超",
        "la liga": "西甲",
        "spain la liga": "西甲",
        "bundesliga": "德甲",
        "bundesliga i": "德甲",
        "germany bundesliga i": "德甲",
        "ligue 1": "法甲",
        "france ligue 1": "法甲",
    }
    return aliases.get(name, (item.get("league") or {}).get("name") or "未分類")


def official_score(item: dict[str, Any], status_short: str) -> tuple[int | None, int | None, str | None]:
    """Return home/away goals and a label only when a real score exists."""
    if status_short not in SCORED_STATUSES:
        return None, None, None
    goals = item.get("goals") or {}
    fulltime = ((item.get("score") or {}).get("fulltime") or {})
    if status_short in FINISHED_STATUSES:
        home = fulltime.get("home")
        away = fulltime.get("away")
        if home is None:
            home = goals.get("home")
        if away is None:
            away = goals.get("away")
    else:
        home = goals.get("home")
        away = goals.get("away")
    if home is None or away is None:
        return None, None, None
    try:
        home_i = int(home)
        away_i = int(away)
    except (TypeError, ValueError):
        return None, None, None
    return home_i, away_i, f"{home_i}-{away_i}"


def public_row(item: dict[str, Any], leagues: list[dict[str, Any]]) -> dict[str, Any] | None:
    fx = item.get("fixture") or {}
    status_short = str((fx.get("status") or {}).get("short") or "")
    dt = timeutil.parse_iso(fx.get("date"))
    if dt is None and fx.get("timestamp"):
        try:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(int(fx["timestamp"]), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            dt = None
    if not fx.get("id") or dt is None:
        return None
    home_en = ((item.get("teams") or {}).get("home") or {}).get("name")
    away_en = ((item.get("teams") or {}).get("away") or {}).get("name")
    home, away, title = teams.match_title(home_en, away_en)
    home_goals, away_goals, score_label = official_score(item, status_short)
    return {
        "id": fx.get("id"),
        "league": _league_zh(item, leagues),
        "league_id": (item.get("league") or {}).get("id"),
        "kickoff": timeutil.iso_hk(dt),
        "kickoff_label": timeutil.label_hk(dt, with_weekday=True),
        "kickoff_date": timeutil.date_iso_hk(dt),
        "kickoff_date_label": _date_label_hk(dt),
        "home": home,
        "away": away,
        "match": title,
        "status": status_short,
        "status_label": STATUS_LABELS.get(status_short, status_short or "狀態不明"),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "score_label": score_label,
    }


def build_public_doc(bundle: dict[str, Any]) -> dict[str, Any]:
    leagues = list(bundle.get("leagues") or [])
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for item in bundle.get("fixtures") or []:
        row = public_row(item, leagues)
        if not row:
            continue
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    rows.sort(key=lambda r: (str(r["kickoff"]), int(r["id"]) if str(r["id"]).isdigit() else 0))
    fetched = bundle.get("fetched_at")
    return {
        "version": 1,
        "product": "DRAGON SPORTS",
        "timezone": "Asia/Hong_Kong",
        "generated_at": fetched or timeutil.iso_hk(timeutil.now_hk()),
        "season": bundle.get("season"),
        "window": bundle.get("window"),
        "fixtures": rows,
    }


def publish_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Write data/fixtures.json for the public site. Does not call the live API."""
    paths.ensure_dirs()
    doc = build_public_doc(bundle)
    path = paths.FIXTURES_PATH
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def publish_from_raw_bundle() -> dict[str, Any]:
    raw = paths.RAW_DIR / "bundle.json"
    if not raw.exists():
        raise FileNotFoundError(f"找不到 {raw}")
    bundle = json.loads(raw.read_text(encoding="utf-8"))
    return publish_from_bundle(bundle)
