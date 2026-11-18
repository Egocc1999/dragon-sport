"""Read and write public tips, locked tip files, proposals, and aggregate stats.

Locked tip files are immutable except for official result / P/L fields after full time.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import paths, timeutil

DISCLAIMER = "V1 紙上往績，提示會在開波前鎖定，賽後不改歷史；這不保證獲勝。"

RESULT_FIELDS = {
    "result",
    "result_label",
    "score",
    "score_label",
    "unit_pl",
    "settled_at",
    "settled_at_label",
    "grade_status",
    "official_status",
}

# Fields written at lock time must not be changed later
IMMUTABLE_HINT = (
    "id",
    "fixture_id",
    "league",
    "league_id",
    "match",
    "home_team",
    "away_team",
    "kickoff",
    "kickoff_label",
    "market",
    "pick",
    "pick_code",
    "line",
    "reference_odds",
    "stake_units",
    "rationale",
    "lock_time",
    "lock_time_label",
    "input_snapshot",
    "status",  # status may move locked -> settled, allowed as settlement
)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def empty_tips_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "product": "DRAGON SPORTS",
        "timezone": "Asia/Hong_Kong",
        "generated_at": None,
        "disclaimer": DISCLAIMER,
        "tips": [],
    }


def empty_stats_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "product": "DRAGON SPORTS",
        "timezone": "Asia/Hong_Kong",
        "generated_at": None,
        "disclaimer": DISCLAIMER,
        "has_settled_record": False,
        "overall": None,
        "by_league": {},
    }


def load_tips() -> dict[str, Any]:
    doc = _load_json(paths.TIPS_PATH, empty_tips_doc())
    if "tips" not in doc:
        doc["tips"] = []
    return doc


def load_locked_tips() -> list[dict[str, Any]]:
    paths.ensure_dirs()
    tips: list[dict[str, Any]] = []
    for path in sorted(paths.LOCKED_DIR.glob("*.json")):
        if path.name.startswith("."):
            continue
        tips.append(_load_json(path, {}))
    return [t for t in tips if t.get("id")]


def locked_path(tip_id: str) -> Path:
    safe = "".join(ch for ch in tip_id if ch.isalnum() or ch in "-_")
    return paths.LOCKED_DIR / f"{safe}.json"


def write_new_lock(tip: dict[str, Any]) -> Path:
    """Write a lock file only if it does not already exist."""
    path = locked_path(tip["id"])
    if path.exists():
        raise FileExistsError(f"鎖定檔已存在，拒絕覆寫：{path.name}")
    _dump(path, tip)
    return path


def attach_settlement(tip_id: str, settlement: dict[str, Any]) -> dict[str, Any]:
    """Attach official result fields only. Any other change is rejected."""
    path = locked_path(tip_id)
    if not path.exists():
        raise FileNotFoundError(f"找不到鎖定提示 {tip_id}")
    current = _load_json(path, {})
    unexpected = set(settlement) - RESULT_FIELDS
    if unexpected:
        raise ValueError(f"結算只可寫入賽果欄位，發現不允許的欄位：{sorted(unexpected)}")
    updated = deepcopy(current)
    updated.update(settlement)
    # status may move to settled / void
    if settlement.get("grade_status") in {"won", "lost", "push"}:
        updated["status"] = "settled"
    elif settlement.get("grade_status") == "void":
        updated["status"] = "void"
    # Compare immutable payload
    for key in list(current):
        if key in RESULT_FIELDS or key == "status":
            continue
        if updated.get(key) != current.get(key):
            raise ValueError(f"拒絕改動已鎖定欄位：{key}")
    _dump(path, updated)
    return updated


def load_proposals() -> dict[str, Any]:
    return _load_json(
        paths.PROPOSALS_PATH,
        {"generated_at": None, "timezone": "Asia/Hong_Kong", "proposals": []},
    )


def save_proposals(doc: dict[str, Any]) -> None:
    _dump(paths.PROPOSALS_PATH, doc)


def compute_stats(tips: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate stats from settled tips only. Empty record is not presented as zeros."""
    now = timeutil.iso_hk(timeutil.now_hk())
    settled = [t for t in tips if t.get("status") == "settled"]
    voided = [t for t in tips if t.get("status") == "void"]
    if not settled and not voided:
        doc = empty_stats_doc()
        doc["generated_at"] = now
        return doc

    def bucket() -> dict[str, Any]:
        return {
            "tips": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "voids": 0,
            "win_rate": None,
            "unit_pl": 0.0,
            "unit_pl_counted": 0,
            "unit_pl_missing": 0,
        }

    def add(b: dict[str, Any], tip: dict[str, Any]) -> None:
        grade = tip.get("grade_status")
        if grade == "void" or tip.get("status") == "void":
            b["voids"] += 1
            return
        b["tips"] += 1
        if grade == "won":
            b["wins"] += 1
        elif grade == "lost":
            b["losses"] += 1
        elif grade == "push":
            b["pushes"] += 1
        pl = tip.get("unit_pl")
        if isinstance(pl, (int, float)):
            b["unit_pl"] = round(b["unit_pl"] + float(pl), 4)
            b["unit_pl_counted"] = b.get("unit_pl_counted", 0) + 1
        elif grade in {"won", "lost", "push"}:
            b["unit_pl_missing"] = b.get("unit_pl_missing", 0) + 1

    def finalize(b: dict[str, Any]) -> dict[str, Any]:
        decided = b["wins"] + b["losses"]
        b["win_rate"] = round(b["wins"] / decided, 4) if decided else None
        b["unit_pl"] = round(b["unit_pl"], 4)
        return b

    overall = bucket()
    by_league: dict[str, dict[str, Any]] = {}
    for tip in tips:
        if tip.get("status") not in {"settled", "void"}:
            continue
        add(overall, tip)
        league = tip.get("league") or "未分類"
        by_league.setdefault(league, bucket())
        add(by_league[league], tip)

    # If every graded tip is void and none are settled-as-decided, still no track record of picks
    has_record = overall["tips"] > 0

    return {
        "version": 1,
        "product": "DRAGON SPORTS",
        "timezone": "Asia/Hong_Kong",
        "generated_at": now,
        "disclaimer": DISCLAIMER,
        "has_settled_record": has_record,
        "overall": finalize(overall) if has_record else None,
        "by_league": {k: finalize(v) for k, v in by_league.items()} if has_record else {},
    }


def publish_public_files() -> dict[str, Any]:
    """Rebuild data/tips.json and data/stats.json from locked tip files."""
    tips = load_locked_tips()
    tips.sort(key=lambda t: t.get("kickoff") or "", reverse=True)
    now = timeutil.iso_hk(timeutil.now_hk())
    doc = empty_tips_doc()
    doc["generated_at"] = now
    doc["tips"] = tips
    _dump(paths.TIPS_PATH, doc)
    stats = compute_stats(tips)
    _dump(paths.STATS_PATH, stats)
    return doc
