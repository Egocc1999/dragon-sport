"""Hong Kong time helpers. All user-facing timestamps are Asia/Hong_Kong (UTC+8)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HK = ZoneInfo("Asia/Hong_Kong")
UTC = timezone.utc


def now_hk() -> datetime:
    return datetime.now(HK)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def to_hk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(HK)


def iso_hk(dt: datetime) -> str:
    return to_hk(dt).isoformat(timespec="seconds")


def label_hk(dt: datetime, with_weekday: bool = False) -> str:
    local = to_hk(dt)
    weekdays = "一二三四五六日"
    wd = f"（週{weekdays[local.weekday()]}）" if with_weekday else ""
    return f"{local.year}年{local.month}月{local.day}日{wd} {local.hour:02d}:{local.minute:02d}（香港時間）"


def date_iso_hk(dt: datetime) -> str:
    return to_hk(dt).date().isoformat()
