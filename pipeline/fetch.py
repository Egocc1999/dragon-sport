"""Fetch upcoming fixtures, recent results, standings, and optional pre-match odds."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from typing import Any

from . import api, config, fixtures_pub, paths, timeutil

USAGE_HINT = "用法：在專案根目錄執行  python3 -m pipeline.fetch"


def _write_raw(name: str, payload: Any) -> None:
    paths.ensure_dirs()
    path = paths.RAW_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _flatten_fixtures(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for payload in payloads:
        for item in payload.get("response") or []:
            fid = (item.get("fixture") or {}).get("id")
            if fid in seen:
                continue
            if fid is not None:
                seen.add(fid)
            out.append(item)
    return out


def verify_leagues(season: int, leagues: list[config.League], force: bool) -> list[str]:
    notes: list[str] = []
    for lg in leagues:
        try:
            payload = api.request(
                "leagues",
                {"id": lg.id, "season": season},
                ttl=config.TTL_LEAGUES,
                force=force,
            )
        except Exception as exc:
            notes.append(f"無法核對 {lg.name_zh}（編號 {lg.id}）：{exc}")
            continue
        rows = payload.get("response") or []
        if not rows:
            notes.append(
                f"警告：編號 {lg.id} 在 {season} 賽季沒有回傳聯賽資料。"
                "此供應商的免費方案已核實涵蓋 2026-27 賽季；"
                "請檢查請求額度（每小時 60 次）或歷史資料窗口（約三個月），"
                "並核對 pipeline/leagues.json。"
            )
            continue
        name = ((rows[0] or {}).get("league") or {}).get("name") or ""
        expected = lg.name_en.lower()
        # La Liga is sometimes "La Liga", "Primera Division", "Primera División"
        aliases = {
            "premier league": {
                "premier league",
                "england premier league",
            },
            "la liga": {
                "la liga",
                "spain la liga",
                "primera division",
                "primera división",
            },
            "bundesliga": {
                "bundesliga",
                "bundesliga 1",
                "bundesliga i",
                "germany bundesliga i",
                "germany bundesliga 1",
            },
            "ligue 1": {
                "ligue 1",
                "france ligue 1",
                "ligue 1 uber eats",
            },
        }
        allowed = aliases.get(expected, {expected})
        if name.lower() not in allowed and expected not in name.lower():
            notes.append(
                f"警告：編號 {lg.id} 回傳名稱為「{name}」，與設定的 {lg.name_en}（{lg.name_zh}）不符，請檢查 leagues.json。"
            )
        else:
            notes.append(f"已核對：{lg.name_zh}（編號 {lg.id}）= {name}，賽季 {season}。")
        remaining = api.remaining_message(payload)
        if remaining:
            notes.append(remaining)
    return notes


def fetch_all(*, force: bool, include_odds: bool, verify: bool) -> dict[str, Any]:
    season, leagues = config.season_and_leagues()
    key = config.read_api_key()
    if not key:
        raise api.ApiKeyMissing(api.key_missing_message())

    notes: list[str] = []
    if verify:
        notes.extend(verify_leagues(season, leagues, force))

    now = timeutil.now_hk()
    past_from = (now - timedelta(days=config.FIXTURE_PAST_DAYS)).date().isoformat()
    future_to = (now + timedelta(days=config.FIXTURE_FUTURE_DAYS)).date().isoformat()
    today = now.date().isoformat()

    fixture_payloads: list[dict[str, Any]] = []
    standings_by_league: dict[int, dict[str, Any]] = {}

    for lg in leagues:
        fx = api.request(
            "fixtures",
            {
                "league": lg.id,
                "season": season,
                "from": past_from,
                "to": future_to,
                "timezone": config.TIMEZONE,
            },
            ttl=config.TTL_FIXTURES,
            force=force,
        )
        fixture_payloads.append(fx)
        src = "快取" if fx.get("_from_cache") else "即時"
        notes.append(f"{lg.name_zh} 賽程／賽果（{src}）：{fx.get('results')} 場。")
        rem = api.remaining_message(fx)
        if rem:
            notes.append(rem)

        st = api.request(
            "standings",
            {"league": lg.id, "season": season},
            ttl=config.TTL_STANDINGS,
            force=force,
        )
        standings_by_league[lg.id] = st
        src = "快取" if st.get("_from_cache") else "即時"
        notes.append(f"{lg.name_zh} 積分榜（{src}）已寫入。")

    fixtures = _flatten_fixtures(fixture_payloads)

    odds_by_fixture: dict[int, dict[str, Any]] = {}
    if include_odds:
        # Group upcoming (NS) fixtures by date to reduce requests. Paginate carefully.
        upcoming_dates: dict[str, list[int]] = defaultdict(list)
        for item in fixtures:
            status = ((item.get("fixture") or {}).get("status") or {}).get("short")
            if status not in {"NS", "TBD"}:
                continue
            date = (item.get("fixture") or {}).get("date")
            fid = (item.get("fixture") or {}).get("id")
            dt = timeutil.parse_iso(date)
            if not dt or not fid:
                continue
            # Odds typically appear 1–14 days before kickoff; skip far fixtures
            if dt.date() < now.date() or (dt.date() - now.date()).days > 7:
                continue
            upcoming_dates[timeutil.date_iso_hk(dt)].append(fid)

        for lg in leagues:
            for day, fids in sorted(upcoming_dates.items()):
                page = 1
                while page <= 5:
                    payload = api.request(
                        "odds",
                        {
                            "league": lg.id,
                            "season": season,
                            "date": day,
                            "page": page,
                            "timezone": config.TIMEZONE,
                        },
                        ttl=config.TTL_ODDS,
                        force=force,
                    )
                    rows = payload.get("response") or []
                    for row in rows:
                        fid = (row.get("fixture") or {}).get("id")
                        if fid:
                            odds_by_fixture[int(fid)] = row
                    paging = payload.get("paging") or {}
                    current = int(paging.get("current") or page)
                    total = int(paging.get("total") or 1)
                    src = "快取" if payload.get("_from_cache") else "即時"
                    notes.append(f"{lg.name_zh} {day} 參考賠率（{src}）第 {current}/{total} 頁。")
                    rem = api.remaining_message(payload)
                    if rem:
                        notes.append(rem)
                    if current >= total or not rows:
                        break
                    page += 1

    bundle = {
        "fetched_at": timeutil.iso_hk(now),
        "timezone": config.TIMEZONE,
        "season": season,
        "window": {"from": past_from, "to": future_to, "today": today},
        "leagues": [{"id": lg.id, "name_zh": lg.name_zh, "name_en": lg.name_en} for lg in leagues],
        "fixtures": fixtures,
        "standings": {str(k): v.get("response") for k, v in standings_by_league.items()},
        "odds_by_fixture": {str(k): v for k, v in odds_by_fixture.items()},
        "notes": notes,
    }
    _write_raw("bundle.json", bundle)
    # Also split lighter index files
    _write_raw("fixtures.json", fixtures)
    public = fixtures_pub.publish_from_bundle(bundle)
    bundle["_public_fixtures"] = len(public.get("fixtures") or [])
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="擷取四項聯賽的賽程、賽果與積分榜。免費方案預設不取聯賽／日期參考賠率。"
    )
    parser.add_argument("--force", action="store_true", help="忽略快取，強制向 API 重新請求")
    parser.add_argument(
        "--no-odds",
        action="store_true",
        help="略過參考賠率（免費方案的預設行為，可明確指定）",
    )
    parser.add_argument(
        "--odds",
        action="store_true",
        help="嘗試擷取聯賽／日期參考賠率（此供應商免費方案通常會拒絕，勿輕易使用）",
    )
    parser.add_argument("--verify-leagues", action="store_true", help="先以 /leagues 核對聯賽編號與名稱")
    args = parser.parse_args(argv)

    print(USAGE_HINT)
    # Free plan does not allow league/date odds listings. Default is off.
    include_odds = bool(args.odds) and not bool(args.no_odds)
    try:
        bundle = fetch_all(force=args.force, include_odds=include_odds, verify=args.verify_leagues)
    except api.ApiKeyMissing as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"擷取失敗：{exc}", file=sys.stderr)
        return 1

    n_fx = len(bundle["fixtures"])
    n_odds = len(bundle["odds_by_fixture"])
    print(f"已寫入 data/raw/bundle.json。賽事 {n_fx} 場，參考賠率 {n_odds} 場。")
    print(f"已寫入 data/fixtures.json 公開賽程 {bundle.get('_public_fixtures', n_fx)} 場，按開波時間排列。")
    for note in bundle["notes"]:
        print(f"- {note}")
    print("下一步：python3 -m pipeline.propose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
