"""NSE / BSE / MCX session helpers — watcher never sleeps while markets are open."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Approximate regular sessions (IST). Holidays are not fully enumerated —
# Upstox market_info overrides when available.
_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)
_MCX_OPEN = time(9, 0)
_MCX_CLOSE = time(23, 55)
_CDS_OPEN = time(9, 0)
_CDS_CLOSE = time(17, 0)


def now_ist() -> datetime:
    return datetime.now(IST)


def is_weekday(dt: datetime | None = None) -> bool:
    d = dt or now_ist()
    return d.weekday() < 5


def _in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def equity_session_open(dt: datetime | None = None) -> bool:
    d = dt or now_ist()
    if not is_weekday(d):
        return False
    return _in_window(d.time(), _NSE_OPEN, _NSE_CLOSE)


def mcx_session_open(dt: datetime | None = None) -> bool:
    d = dt or now_ist()
    return is_weekday(d) and _in_window(d.time(), _MCX_OPEN, _MCX_CLOSE)


def currency_session_open(dt: datetime | None = None) -> bool:
    d = dt or now_ist()
    return is_weekday(d) and _in_window(d.time(), _CDS_OPEN, _CDS_CLOSE)


def any_indian_market_open(
    *,
    segment_status: dict[str, str] | None = None,
    dt: datetime | None = None,
) -> bool:
    """Return True when at least one monitored segment should be active."""
    if segment_status:
        open_tokens = {"NORMAL_OPEN", "OPEN", "OPENING", "CLOSING"}
        return any(str(v).upper() in open_tokens for v in segment_status.values())
    return equity_session_open(dt) or mcx_session_open(dt) or currency_session_open(dt)


def seconds_until_next_open(dt: datetime | None = None) -> float:
    """Sleep hint when all sessions are closed."""
    d = dt or now_ist()
    if any_indian_market_open(dt=d):
        return 0.0
    # Next weekday 09:00 IST
    candidate = d.replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate <= d:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return max((candidate - d).total_seconds(), 60.0)
