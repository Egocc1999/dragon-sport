"""Grade locked tips from official full-time results. Never invent scores."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import config, paths, store, timeutil

USAGE_HINT = "用法：在專案根目錄執行  python3 -m pipeline.grade"


def load_bundle() -> dict[str, Any]:
    path = paths.RAW_DIR / "bundle.json"
    if not path.exists():
        raise FileNotFoundError(
            "找不到 data/raw/bundle.json。請先執行：python3 -m pipeline.fetch 以取得官方賽果。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def fixtures_by_id(bundle: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for fx in bundle.get("fixtures") or []:
        fid = (fx.get("fixture") or {}).get("id")
        if fid is not None:
            out[int(fid)] = fx
    return out


def official_score(fx: dict[str, Any]) -> tuple[str, tuple[int, int] | None]:
    status = ((fx.get("fixture") or {}).get("status") or {}).get("short") or ""
    if status in config.VOID_STATUS:
        return status, None
    if status not in config.GRADEABLE_STATUS:
        return status, None
    # League markets use the official 90-minute full-time score
    ft = ((fx.get("score") or {}).get("fulltime") or {})
    if ft.get("home") is not None and ft.get("away") is not None:
        return status, (int(ft["home"]), int(ft["away"]))
    goals = fx.get("goals") or {}
    if goals.get("home") is not None and goals.get("away") is not None:
        return status, (int(goals["home"]), int(goals["away"]))
    return status, None


def grade_pick(tip: dict[str, Any], home: int, away: int) -> tuple[str, float, str]:
    """Return (grade_status, unit_pl, result_label)."""
    stake = float(tip.get("stake_units") or 0)
    odds = tip.get("reference_odds")
    market = tip.get("market")
    code = tip.get("pick_code")

    def pl(win: bool, is_push: bool = False) -> float:
        if is_push:
            return 0.0
        if win:
            if isinstance(odds, (int, float)) and odds > 1:
                return round(stake * (float(odds) - 1.0), 4)
            # No odds: count as a win with unknown price — do not invent a price.
            # Record unit profit as None? The product asks for unit P/L.
            # Without a reference price we cannot compute decimal profit honestly.
            # Use stake * 0 and keep a note? Better: treat as +1 unit on a notional evens?
            # No — don't invent. Store unit_pl as None is awkward for aggregates.
            # Document: if no reference odds, winning P/L is recorded as +stake (notional 2.00).
            # That's inventing a price. Worse.
            # We'll record +0 with grade won and let stats use win rate;
            # but unit P/L would understate.
            # Decision: if no odds, unit_pl for a win is None and excluded from unit P/L
            # sum? That complicates compute_stats.
            # Clearer documented rule: 若無參考賠率，獲勝按 dec 2.00 記 +1 倍本金，並在 snapshot 已註明無賠率。
            # The task says "do not invent stats". Odds is a price not a stat of the match.
            # I'll use None for winning P/L without odds and treat it as missing in sums,
            # while still counting the win in the win rate.
            # Update store.compute_stats to skip None unit_pl.
            # For display, show 「勝（無參考賠率，不計單位盈虧）」
            return None  # type: ignore[return-value]
        return round(-stake, 4)

    if market == "勝負":
        if home > away:
            outcome = "home"
            score_winner = "主勝"
        elif home == away:
            outcome = "draw"
            score_winner = "和局"
        else:
            outcome = "away"
            score_winner = "客勝"
        won = code == outcome
        profit = pl(won)
        grade = "won" if won else "lost"
        label = "中" if won else "不中"
        return grade, profit, f"{label}（賽果 {score_winner}）"

    if market == "大小球":
        line = tip.get("line")
        if line is None:
            line = config.OU_LINE
        total = home + away
        if total == float(line):
            return "push", 0.0, "走水（總入球剛在盤口）"
        over = total > float(line)
        won = (code == "over" and over) or (code == "under" and not over)
        profit = pl(won)
        grade = "won" if won else "lost"
        label = "中" if won else "不中"
        side = "大" if over else "小"
        return grade, profit, f"{label}（總入球 {total}，結果偏{side}）"

    raise ValueError(f"不支援的盤口：{market}")


def grade_all(*, dry_run: bool = False) -> dict[str, Any]:
    bundle = load_bundle()
    index = fixtures_by_id(bundle)
    tips = store.load_locked_tips()
    graded: list[str] = []
    skipped: list[dict] = []

    for tip in tips:
        if tip.get("status") in {"settled", "void"}:
            skipped.append({"id": tip.get("id"), "reason": "已經結算或已標為無效，不改歷史"})
            continue
        fid = tip.get("fixture_id")
        fx = index.get(int(fid)) if fid is not None else None
        if fx is None:
            skipped.append({"id": tip.get("id"), "reason": "本批資料沒有此場官方賽果，略過"})
            continue
        status, score = official_score(fx)
        if status in config.VOID_STATUS:
            settlement = {
                "grade_status": "void",
                "official_status": status,
                "result": "void",
                "result_label": "無效（賽事取消、腰斬或延期）",
                "score": None,
                "score_label": None,
                "unit_pl": 0.0,
                "settled_at": timeutil.iso_hk(timeutil.now_hk()),
                "settled_at_label": timeutil.label_hk(timeutil.now_hk()),
            }
            if not dry_run:
                store.attach_settlement(tip["id"], settlement)
            graded.append(tip["id"])
            continue
        if score is None:
            skipped.append({
                "id": tip.get("id"),
                "reason": f"官方狀態為 {status or '未知'}，尚未有可用的全場賽果",
            })
            continue
        home, away = score
        try:
            grade, profit, label = grade_pick(tip, home, away)
        except ValueError as exc:
            skipped.append({"id": tip.get("id"), "reason": str(exc)})
            continue
        now = timeutil.now_hk()
        settlement = {
            "grade_status": grade,
            "official_status": status,
            "result": grade,
            "result_label": label,
            "score": {"home": home, "away": away},
            "score_label": f"{home}–{away}",
            "unit_pl": profit,
            "settled_at": timeutil.iso_hk(now),
            "settled_at_label": timeutil.label_hk(now),
        }
        if not dry_run:
            store.attach_settlement(tip["id"], settlement)
        graded.append(tip["id"])

    if not dry_run:
        store.publish_public_files()
    return {"graded": graded, "skipped": skipped, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="以官方全場賽果結算已鎖定提示。")
    parser.add_argument("--dry-run", action="store_true", help="只顯示將會結算的項目，不寫入檔案")
    args = parser.parse_args(argv)
    print(USAGE_HINT)
    try:
        result = grade_all(dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"結算失敗：{exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("這是預演，沒有改動鎖定檔。")
    print(f"本輪結算 {len(result['graded'])} 條。")
    for tip_id in result["graded"]:
        print(f"- 已結算：{tip_id}")
    for row in result["skipped"]:
        print(f"- 略過 {row.get('id')}：{row.get('reason')}")
    if result["graded"] and not args.dry_run:
        print("已由已結算提示重算 data/stats.json，並更新 data/tips.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
