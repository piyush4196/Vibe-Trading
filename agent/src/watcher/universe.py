"""Watch universe — indices, cash, F&O, MCX, currency."""

from __future__ import annotations

import logging
from typing import Iterable

from src.trading.connectors.upstox.instruments import (
    INDEX_ALIASES,
    resolve_instrument,
)
from src.watcher.config import WatcherConfig
from src.watcher.models import MarketSegment, WatchInstrument

logger = logging.getLogger(__name__)

# Core index aliases always monitored when enabled.
_CORE_INDICES = [
    ("NIFTY", "NSE_INDEX|Nifty 50"),
    ("NIFTYNXT50", "NSE_INDEX|Nifty Next 50"),
    ("BANKNIFTY", "NSE_INDEX|Nifty Bank"),
    ("FINNIFTY", "NSE_INDEX|Nifty Fin Service"),
    ("MIDCPNIFTY", "NSE_INDEX|NIFTY MID SELECT"),
    ("NIFTYMIDCAP50", "NSE_INDEX|Nifty Midcap 50"),
    ("NIFTYSMALLCAP500", "NSE_INDEX|Nifty Smallcap 500"),
    ("SENSEX", "BSE_INDEX|SENSEX"),
    ("INDIAVIX", "NSE_INDEX|India VIX"),
]

# Liquid F&O underlyings (cash + futures). Expandable via config.extra_symbols.
_DEFAULT_FNO_STOCKS = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "LT",
    "AXISBANK",
    "KOTAKBANK",
    "BAJFINANCE",
    "MARUTI",
    "SUNPHARMA",
    "TITAN",
    "NTPC",
    "POWERGRID",
    "ULTRACEMCO",
    "ASIANPAINT",
    "WIPRO",
    "HCLTECH",
    "ADANIENT",
    "ADANIPORTS",
    "TATAMOTORS",
    "TATASTEEL",
    "JSWSTEEL",
    "ONGC",
    "COALINDIA",
    "M&M",
    "NESTLEIND",
]

_MCX_ALIASES = ["GOLD", "SILVER", "CRUDEOIL"]
_CURRENCY_ALIASES = ["USDINR", "EURINR", "GBPINR", "JPYINR"]


def build_universe(config: WatcherConfig) -> list[WatchInstrument]:
    """Resolve the live watchlist (capped by ``max_instruments``)."""
    out: list[WatchInstrument] = []
    seen: set[str] = set()

    def _add(inst: WatchInstrument) -> None:
        if inst.instrument_key in seen:
            return
        seen.add(inst.instrument_key)
        out.append(inst)

    if config.include_indices:
        for symbol, key in _CORE_INDICES:
            if symbol in ("NIFTYNXT50",) and not config.include_nifty_next50:
                continue
            if symbol.startswith("NIFTYSML") and not config.include_indices:
                continue
            _add(
                WatchInstrument(
                    symbol=symbol,
                    instrument_key=key,
                    market="BSE" if key.startswith("BSE_") else "NSE",
                    segment=MarketSegment.INDEX,
                    name=key.split("|", 1)[-1],
                )
            )

    # Nifty 50 cash names — use F&O liquid list as proxy when full constituent
    # file is unavailable offline.
    cash_symbols: list[str] = []
    if config.include_nifty50 or config.include_fno_stocks:
        cash_symbols.extend(_DEFAULT_FNO_STOCKS)
    cash_symbols.extend(config.extra_symbols or [])

    for sym in cash_symbols:
        if len(out) >= config.max_instruments:
            break
        try:
            row = resolve_instrument(f"{sym}.NS" if not sym.upper().endswith((".NS", ".BO")) else sym)
            _add(
                WatchInstrument(
                    symbol=str(row.get("trading_symbol") or sym).upper(),
                    instrument_key=str(row["instrument_key"]),
                    market="NSE",
                    segment=MarketSegment.EQUITY,
                    name=str(row.get("name") or sym),
                    lot_size=int(row.get("lot_size") or 1),
                    tick_size=float(row.get("tick_size") or 0.05),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("skip equity %s: %s", sym, exc)

    if config.include_index_futures:
        for alias in ("NIFTY-FUT", "BANKNIFTY-FUT", "FINNIFTY-FUT", "MIDCPNIFTY-FUT"):
            if len(out) >= config.max_instruments:
                break
            try:
                row = resolve_instrument(alias)
                _add(
                    WatchInstrument(
                        symbol=str(row.get("trading_symbol") or alias),
                        instrument_key=str(row["instrument_key"]),
                        market="NSE",
                        segment=MarketSegment.FUTURE,
                        name=str(row.get("name") or alias),
                        lot_size=int(row.get("lot_size") or 1),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("skip index fut %s: %s", alias, exc)

    if config.include_stock_futures:
        for sym in _DEFAULT_FNO_STOCKS[:15]:
            if len(out) >= config.max_instruments:
                break
            try:
                row = resolve_instrument(f"{sym}-FUT")
                _add(
                    WatchInstrument(
                        symbol=str(row.get("trading_symbol") or f"{sym}-FUT"),
                        instrument_key=str(row["instrument_key"]),
                        market="NSE",
                        segment=MarketSegment.FUTURE,
                        name=str(row.get("name") or sym),
                        lot_size=int(row.get("lot_size") or 1),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("skip stock fut %s: %s", sym, exc)

    if config.include_mcx:
        for alias in _MCX_ALIASES:
            if len(out) >= config.max_instruments:
                break
            try:
                row = resolve_instrument(alias)
                _add(
                    WatchInstrument(
                        symbol=str(row.get("trading_symbol") or alias),
                        instrument_key=str(row["instrument_key"]),
                        market="MCX",
                        segment=MarketSegment.COMMODITY,
                        name=str(row.get("name") or alias),
                        lot_size=int(row.get("lot_size") or 1),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("skip mcx %s: %s", alias, exc)

    if config.include_currency:
        for alias in _CURRENCY_ALIASES:
            if len(out) >= config.max_instruments:
                break
            try:
                row = resolve_instrument(alias)
                _add(
                    WatchInstrument(
                        symbol=str(row.get("trading_symbol") or alias),
                        instrument_key=str(row["instrument_key"]),
                        market="CDS",
                        segment=MarketSegment.CURRENCY,
                        name=str(row.get("name") or alias),
                        lot_size=int(row.get("lot_size") or 1),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("skip currency %s: %s", alias, exc)

    return out[: config.max_instruments]


def index_keys() -> dict[str, str]:
    """Return core index instrument keys for market-filter workers."""
    return {symbol: key for symbol, key in _CORE_INDICES}


def expand_aliases(symbols: Iterable[str]) -> list[str]:
    out = []
    for s in symbols:
        key = INDEX_ALIASES.get(s.upper().replace("_", " "))
        out.append(key or s)
    return out
