"""Score upcoming matches and write proposed tips (not yet locked)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import config, model, paths, store, timeutil

USAGE_HINT = "用法：在專案根目錄執行  python3 -m pipeline.propose"


def load_bundle() -> dict[str, Any]:
    path = paths.RAW_DIR / "bundle.json"
    if not path.exists():
        raise FileNotFoundError(
            "找不到 data/raw/bundle.json。請先執行：python3 -m pipeline.fetch"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _standings_payload(raw_response: Any) -> dict[str, Any]:
    return {"response": raw_response or []}


def build_snapshots(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    season, leagues = config.season_and_leagues()
    league_by_id = {lg.id: lg for lg in leagues}
    fixtures = bundle.get("fixtures") or []
    standings_raw = bundle.get("standings") or {}
    odds_map = bundle.get("odds_by_fixture") or {}
    now = timeutil.now_hk()

    standings_parsed: dict[int, dict[int, dict[str, Any]]] = {}
    for key, payload in standings_raw.items():
        lid = int(key)
        standings_parsed[lid] = model.parse_standings(_standings_payload(payload), lid)

    upcoming: list[dict[str, Any]] = []
    for fx in fixtures:
        league_id = (fx.get("league") or {}).get("id")
        if league_id not in league_by_id:
            continue
        status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
        kickoff = timeutil.parse_iso((fx.get("fixture") or {}).get("date"))
        if status not in {"NS", "TBD"}:
            continue
        if kickoff and kickoff <= now:
            continue
        upcoming.append(fx)

    snapshots: list[dict[str, Any]] = []
    for fx in upcoming:
        league_id = int((fx.get("league") or {}).get("id"))
        fid = (fx.get("fixture") or {}).get("id")
        odds_entry = odds_map.get(str(fid)) or odds_map.get(fid)
        snap = model.build_match_snapshot(
            fx,
            standings_parsed.get(league_id) or {},
            fixtures,
            odds_entry,
            league_by_id[league_id],
        )
        snapshots.append(snap)
    return snapshots


def proposals_from_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    locked = store.load_locked_tips()
    locked_fixtures = {int(t["fixture_id"]) for t in locked if t.get("fixture_id") is not None}
    kept, summary = model.select_official_tips(snapshots, locked_fixtures)
    now = timeutil.now_hk()
    items: list[dict[str, Any]] = []
    for row in kept:
        snap = row["snapshot"]
        choice = row["choice"]
        fixture_id = snap["fixture_id"]
        kickoff = snap["kickoff"]
        date_key = ""
        dt = timeutil.parse_iso(kickoff)
        if dt:
            date_key = timeutil.to_hk(dt).strftime("%Y%m%d")
        tip_id = f"ds-{fixture_id}-{choice['pick_code']}-{date_key}"
        items.append({
            "id": tip_id,
            "status": "proposed",
            "league": snap["league"],
            "league_id": snap["league_id"],
            "match": snap["match"],
            "home_team": snap["home_team"],
            "away_team": snap["away_team"],
            "fixture_id": fixture_id,
            "kickoff": snap["kickoff"],
            "kickoff_label": snap["kickoff_label"],
            "market": choice["market"],
            "pick": choice["pick"],
            "pick_code": choice["pick_code"],
            "line": choice.get("line"),
            "reference_odds": choice.get("reference_odds"),
            "stake_units": choice["stake_units"],
            "rationale": model.rationale_zh(snap, choice),
            "model_prob": choice["model_prob"],
            "edge": choice.get("edge"),
            "confidence": choice["confidence"],
            "input_snapshot": {
                "home": snap["home"],
                "away": snap["away"],
                "league_avgs": snap.get("league_avgs"),
                "odds": snap.get("odds"),
                "standings_available": snap.get("standings_available"),
                "model_source": choice.get("model_source"),
                "model_meta": choice.get("model_meta"),
                "inputs_used": choice.get("inputs_used"),
                "model_prob": choice["model_prob"],
                "implied_prob": choice.get("implied_prob"),
                "edge": choice.get("edge"),
                "confidence": choice["confidence"],
                "xg": "不使用。V1 不估計、不填入 xG。",
            },
        })
    return {
        "generated_at": timeutil.iso_hk(now),
        "timezone": config.TIMEZONE,
        "disclaimer": store.DISCLAIMER,
        "summary": summary,
        "proposals": items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="根據已擷取資料提出尚未鎖定的提示。")
    parser.parse_args(argv)
    print(USAGE_HINT)
    try:
        bundle = load_bundle()
        snapshots = build_snapshots(bundle)
        doc = proposals_from_snapshots(snapshots)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"提案失敗：{exc}", file=sys.stderr)
        return 1

    store.save_proposals(doc)
    n = len(doc["proposals"])
    print(f"已寫入 data/proposed/proposals.json。本輪官方提案：{n} 條。")
    print(doc["summary"].get("note"))
    if n == 0:
        print("沒有提示通過高信心門檻，本輪不提案。這是正常情況。")
    for item in doc["proposals"]:
        odds = item["reference_odds"]
        odds_txt = f"，參考賠率 {odds}" if odds else "，無參考賠率"
        print(
            f"- {item['league']} {item['match']}｜{item['market']} {item['pick']}"
            f"{odds_txt}｜信心 {item['confidence']}｜{item['kickoff_label']}"
        )
    skipped = doc["summary"].get("skipped") or []
    if skipped:
        print(f"另有 {len(skipped)} 場未提案（詳見 proposals.json 的 summary.skipped）。")
    print("審閱後執行：python3 -m pipeline.lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
