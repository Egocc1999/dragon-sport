"""Lock proposed tips that are still before kickoff. After lock, only grade may attach results."""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy

from . import paths, store, timeutil

USAGE_HINT = "用法：在專案根目錄執行  python3 -m pipeline.lock"


def lock_proposals(*, dry_run: bool = False) -> dict:
    doc = store.load_proposals()
    proposals = doc.get("proposals") or []
    if not proposals:
        return {"locked": [], "skipped": [], "note": "沒有待鎖定的提案。"}

    now = timeutil.now_hk()
    existing = {t["id"]: t for t in store.load_locked_tips()}
    locked_fixtures = {t.get("fixture_id") for t in existing.values()}
    locked: list[str] = []
    skipped: list[dict] = []

    for raw in proposals:
        tip_id = raw.get("id")
        fixture_id = raw.get("fixture_id")
        kickoff = timeutil.parse_iso(raw.get("kickoff"))
        if not tip_id or not kickoff:
            skipped.append({"id": tip_id, "reason": "提案缺少編號或開波時間"})
            continue
        if tip_id in existing:
            skipped.append({"id": tip_id, "reason": "此提示已經鎖定，不會覆寫"})
            continue
        if fixture_id in locked_fixtures:
            skipped.append({"id": tip_id, "reason": "此場已有其他已鎖定提示，每場只保留一條"})
            continue
        if kickoff <= now:
            skipped.append({
                "id": tip_id,
                "reason": f"開波時間 {raw.get('kickoff_label')} 已到或已過，拒絕鎖定",
            })
            continue

        tip = deepcopy(raw)
        tip["status"] = "locked"
        tip["lock_time"] = timeutil.iso_hk(now)
        tip["lock_time_label"] = timeutil.label_hk(now)
        # Settlement fields present but empty
        tip.setdefault("result", None)
        tip.setdefault("result_label", None)
        tip.setdefault("score", None)
        tip.setdefault("score_label", None)
        tip.setdefault("unit_pl", None)
        tip.setdefault("settled_at", None)
        tip.setdefault("settled_at_label", None)
        tip.setdefault("grade_status", None)
        tip.setdefault("official_status", None)
        # Drop propose-only convenience duplicates if we want a stable public shape
        if not dry_run:
            store.write_new_lock(tip)
        locked.append(tip_id)
        locked_fixtures.add(fixture_id)

    if not dry_run:
        store.publish_public_files()

    note = (
        f"已鎖定 {len(locked)} 條提示。"
        if locked
        else "本輪沒有新的鎖定提示。"
    )
    return {"locked": locked, "skipped": skipped, "note": note, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="將仍未開波的提案鎖定為官方提示。")
    parser.add_argument("--dry-run", action="store_true", help="只顯示將會鎖定的項目，不寫入檔案")
    args = parser.parse_args(argv)
    print(USAGE_HINT)
    try:
        result = lock_proposals(dry_run=args.dry_run)
    except Exception as exc:
        print(f"鎖定失敗：{exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("這是預演，沒有寫入鎖定檔。")
    print(result["note"])
    for tip_id in result["locked"]:
        print(f"- 鎖定：{tip_id}")
    for row in result["skipped"]:
        print(f"- 略過 {row.get('id')}：{row.get('reason')}")
    if result["locked"] and not args.dry_run:
        print("已更新公開檔 data/tips.json。")
        print("賽後請執行：python3 -m pipeline.grade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
