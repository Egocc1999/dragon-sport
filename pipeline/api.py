"""5DollarFootballAPI client (API-Football v3 compatible host) with on-disk cache.
Never calls the live API without a key.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config, paths, timeutil


class ApiKeyMissing(RuntimeError):
    pass


def _cache_path(endpoint: str, params: dict[str, Any]) -> Path:
    items = "&".join(f"{k}={params[k]}" for k in sorted(params))
    digest = hashlib.sha256(f"{endpoint}?{items}".encode("utf-8")).hexdigest()[:20]
    safe_ep = endpoint.strip("/").replace("/", "_")
    return paths.CACHE_DIR / f"{safe_ep}_{digest}.json"


def _read_cache(path: Path, ttl: int, now: datetime) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    fetched = payload.get("fetched_at")
    if not fetched:
        return None
    fetched_dt = timeutil.parse_iso(fetched)
    if fetched_dt is None:
        return None
    if now - fetched_dt > timedelta(seconds=ttl):
        return None
    return payload


def key_missing_message() -> str:
    return (
        "找不到 5DollarFootballAPI 金鑰，已中止擷取，並沒有呼叫即時 API。\n"
        "請將金鑰放到環境變數 FIVE_DOLLAR_FOOTBALL_API_KEY，"
        "或寫入專案根目錄的 .env / .api_key"
        "（此檔已列入 .gitignore，切勿提交）。\n"
        "沒有金鑰時，網站仍可使用空白的 data/tips.json 預覽。"
    )


def request(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    ttl: int,
    force: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """GET a 5DollarFootballAPI endpoint, serving from cache when fresh."""
    paths.ensure_dirs()
    params = {k: v for k, v in (params or {}).items() if v is not None}
    params = {k: str(v) for k, v in params.items()}
    cache_path = _cache_path(endpoint, params)
    now = datetime.now(timezone.utc)

    if not force:
        cached = _read_cache(cache_path, ttl, now)
        if cached is not None:
            cached["_from_cache"] = True
            return cached

    key = api_key if api_key is not None else config.read_api_key()
    if not key:
        raise ApiKeyMissing(key_missing_message())

    url = config.API_BASE + "/" + endpoint.lstrip("/")
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            config.API_KEY_HEADER: key,
            "Accept": "application/json",
            "User-Agent": "DragonSportsV1/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"5DollarFootballAPI HTTP {exc.code}（{endpoint}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"無法連線 5DollarFootballAPI（{endpoint}）：{exc.reason}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"5DollarFootballAPI 回傳不是有效 JSON（{endpoint}）") from exc

    errors = data.get("errors")
    if errors:
        # Vendor sometimes returns {} or [] when OK
        if not (isinstance(errors, (list, dict)) and len(errors) == 0):
            raise RuntimeError(f"5DollarFootballAPI 回報錯誤（{endpoint}）：{errors}")

    payload = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "endpoint": endpoint,
        "params": params,
        "http_status": status,
        "rate_limit_remaining": headers.get("x-ratelimit-requests-remaining")
        or headers.get("x-ratelimit-remaining"),
        "rate_limit_limit": headers.get("x-ratelimit-requests-limit")
        or headers.get("x-ratelimit-limit"),
        "paging": data.get("paging"),
        "results": data.get("results"),
        "response": data.get("response", []),
        "_from_cache": False,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def remaining_message(payload: dict[str, Any]) -> str | None:
    rem = payload.get("rate_limit_remaining")
    if rem is None:
        return None
    limit = payload.get("rate_limit_limit")
    if limit is not None:
        return f"目前剩餘請求額度約 {rem} 次（本時段上限 {limit} 次）"
    return f"目前剩餘請求額度約 {rem} 次（按每小時時段計算，並非每日上限）"


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)
