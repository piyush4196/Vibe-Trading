"""Top-down market filter — VIX, index trends, breadth proxy."""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.watcher.indicators import candles_to_frame, compute_indicator_bundle
from src.watcher.models import MarketContext
from src.watcher.universe import index_keys

logger = logging.getLogger(__name__)


def _trend_from_ind(ind: dict[str, Any]) -> str:
    return str(ind.get("trend") or "neutral")


def build_market_context(
    load_bars: Callable[[str, str], list[dict]],
) -> MarketContext:
    """``load_bars(instrument_key, timeframe) -> candle rows``."""
    ctx = MarketContext()
    keys = index_keys()

    def _ind(symbol: str) -> dict[str, Any]:
        key = keys.get(symbol)
        if not key:
            return {"ok": False}
        rows = load_bars(key, "15m") or load_bars(key, "1D") or []
        return compute_indicator_bundle(candles_to_frame(rows))

    nifty = _ind("NIFTY")
    bank = _ind("BANKNIFTY")
    vix = _ind("INDIAVIX")

    ctx.nifty_trend = _trend_from_ind(nifty) if nifty.get("ok") else "neutral"
    ctx.banknifty_trend = _trend_from_ind(bank) if bank.get("ok") else "neutral"
    if vix.get("ok"):
        ctx.india_vix = float(vix.get("close") or 0)
        ctx.notes.append(f"India VIX {ctx.india_vix:.2f}")

    # Breadth proxy from midcap vs nifty momentum when available.
    mid = _ind("MIDCPNIFTY")
    small = _ind("NIFTYSMALLCAP500")
    breadth = 50.0
    if nifty.get("ok") and mid.get("ok"):
        nm = float(nifty.get("momentum_5") or 0)
        mm = float(mid.get("momentum_5") or 0)
        breadth += max(min((mm - nm) * 5, 20), -20)
        ctx.sector_rotation["midcap_vs_nifty"] = mm - nm
    if small.get("ok"):
        ctx.sector_rotation["smallcap_mom"] = float(small.get("momentum_5") or 0)
    if ctx.nifty_trend == "bullish":
        breadth += 10
    elif ctx.nifty_trend == "bearish":
        breadth -= 10
    ctx.breadth_score = max(0.0, min(100.0, breadth))
    ctx.notes.append(f"Nifty {ctx.nifty_trend}; BankNifty {ctx.banknifty_trend}")
    return ctx
