"""Upstox instrument-master resolution for Indian cash + derivatives markets.

Downloads the BOD JSON master from Upstox assets (gzip) and resolves project
symbols / aliases into Upstox ``instrument_key`` values used by quote and
historical candle APIs.

Supported aliases
-----------------
* NSE cash: ``RELIANCE.NS`` / ``RELIANCE``
* BSE cash: ``500325.BO`` / ``SBIN.BO``
* Indices: ``NIFTY``, ``BANKNIFTY``, ``FINNIFTY``, ``MIDCPNIFTY``, midcap names
* Futures: ``NIFTY-FUT``, ``RELIANCE-FUT``, ``GOLD-FUT``, ``USDINR-FUT``
* Options / contracts: full Upstox ``instrument_key`` or trading symbol
* Commodities: ``GOLD``, ``SILVER``, ``CRUDEOIL`` (nearest MCX future)
* Currency: ``USDINR``, ``EURINR``, ``GBPINR``, ``JPYINR`` (nearest CDS future)
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)

#: Friendly aliases → Upstox index instrument_key.
INDEX_ALIASES: dict[str, str] = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "NIFTYBANK": "NSE_INDEX|Nifty Bank",
    "NIFTY BANK": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "NIFTYFINSERVICE": "NSE_INDEX|Nifty Fin Service",
    "NIFTY FIN SERVICE": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYMIDSELECT": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTY MID SELECT": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYMIDCAP50": "NSE_INDEX|Nifty Midcap 50",
    "NIFTY MIDCAP 50": "NSE_INDEX|Nifty Midcap 50",
    "NIFTYMIDCAP100": "NSE_INDEX|Nifty Midcap 100",
    "NIFTY MIDCAP 100": "NSE_INDEX|Nifty Midcap 100",
    "NIFTYMIDCAP150": "NSE_INDEX|Nifty Midcap 150",
    "NIFTY MIDCAP 150": "NSE_INDEX|Nifty Midcap 150",
    "INDIAVIX": "NSE_INDEX|India VIX",
    "INDIA VIX": "NSE_INDEX|India VIX",
}

#: Commodity aliases → preferred MCX ``asset_symbol`` for the continuous future.
COMMODITY_ALIASES: dict[str, str] = {
    "GOLD": "GOLD",
    "GOLDM": "GOLDM",
    "SILVER": "SILVER",
    "SILVERM": "SILVERM",
    "CRUDEOIL": "CRUDEOIL",
    "CRUDE OIL": "CRUDEOIL",
    "CRUDE": "CRUDEOIL",
    "CRUDEOILM": "CRUDEOILM",
    "NATURALGAS": "NATURALGAS",
    "NATGAS": "NATURALGAS",
}

#: Currency pair aliases → NCD_FO underlying name.
CURRENCY_ALIASES: dict[str, str] = {
    "USDINR": "USDINR",
    "EURINR": "EURINR",
    "GBPINR": "GBPINR",
    "JPYINR": "JPYINR",
}

_SEGMENT_FOR_EXCHANGE = {
    "NSE": "NSE_EQ",
    "BSE": "BSE_EQ",
    "NFO": "NSE_FO",
    "BFO": "BSE_FO",
    "MCX": "MCX_FO",
    "CDS": "NCD_FO",
    "NCD": "NCD_FO",
}

_CACHE_TTL_SECONDS = 6 * 60 * 60
_lock = threading.Lock()
_cache_rows: list[dict[str, Any]] | None = None
_cache_loaded_at = 0.0


class UpstoxInstrumentError(RuntimeError):
    """Raised when an instrument cannot be resolved."""


def clear_instrument_cache() -> None:
    """Drop the in-process instrument master cache (tests / force refresh)."""
    global _cache_rows, _cache_loaded_at
    with _lock:
        _cache_rows = None
        _cache_loaded_at = 0.0


def load_instruments(*, force: bool = False) -> list[dict[str, Any]]:
    """Return the BOD instrument master (cached for ``_CACHE_TTL_SECONDS``)."""
    global _cache_rows, _cache_loaded_at
    now = time.time()
    with _lock:
        if (
            not force
            and _cache_rows is not None
            and (now - _cache_loaded_at) < _CACHE_TTL_SECONDS
        ):
            return _cache_rows

    import httpx

    logger.info("Downloading Upstox instrument master from %s", INSTRUMENTS_URL)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(INSTRUMENTS_URL)
        response.raise_for_status()
        rows = json.loads(gzip.decompress(response.content))
    if not isinstance(rows, list):
        raise UpstoxInstrumentError("unexpected instrument master payload")

    with _lock:
        _cache_rows = rows
        _cache_loaded_at = time.time()
    return rows


def resolve_instrument(
    symbol: str,
    *,
    segment: str | None = None,
    exchange: str | None = None,
    instrument_type: str | None = None,
    prefer_future: bool = False,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve ``symbol`` to a single instrument row.

    Args:
        symbol: Project symbol, alias, trading symbol, or Upstox instrument_key.
        segment: Optional Upstox segment filter (``NSE_EQ``, ``NSE_FO``, …).
        exchange: Shorthand exchange (``NSE``/``BSE``/``MCX``/``CDS``/``NFO``).
        instrument_type: Optional type filter (``EQ``, ``INDEX``, ``FUT``, ``CE``, ``PE``).
        prefer_future: When True, resolve commodity/index/stock aliases to the
            nearest-expiry ``FUT`` contract instead of the cash/index instrument.
        rows: Optional preloaded instrument rows (tests).
    """
    raw = str(symbol or "").strip()
    if not raw:
        raise UpstoxInstrumentError("symbol is required")

    # Already an instrument_key.
    if "|" in raw:
        return {"instrument_key": raw, "trading_symbol": raw, "segment": raw.split("|", 1)[0]}

    clean = raw.upper().replace("_", " ").strip()
    clean_compact = clean.replace(" ", "")
    seg = (segment or "").strip().upper() or None
    if not seg and exchange:
        seg = _SEGMENT_FOR_EXCHANGE.get(str(exchange).strip().upper())

    # Yahoo-style suffixes select cash segment.
    if clean.endswith(".NS"):
        base = clean[:-3].strip()
        seg = seg or "NSE_EQ"
        return _resolve_equity(base, segment=seg, rows=rows)
    if clean.endswith(".BO"):
        base = clean[:-3].strip()
        seg = seg or "BSE_EQ"
        return _resolve_equity(base, segment=seg, rows=rows)

    # Explicit "-FUT" / " FUT" preference.
    fut_requested = prefer_future
    base_for_fut = clean
    for suffix in ("-FUTURE", " FUTURE", "-FUT", " FUT"):
        compact_suffix = suffix.replace(" ", "")
        if clean.endswith(suffix):
            fut_requested = True
            base_for_fut = clean[: -len(suffix)].strip()
            break
        if clean_compact.endswith(compact_suffix) and len(clean_compact) > len(compact_suffix):
            fut_requested = True
            base_for_fut = clean_compact[: -len(compact_suffix)].strip("-_ ")
            break
    if (
        not fut_requested
        and clean_compact.endswith("FUT")
        and len(clean_compact) > 3
        and clean_compact not in {a.replace(" ", "") for a in INDEX_ALIASES}
    ):
        # e.g. NIFTYFUT (no separator)
        maybe = clean_compact[:-3].strip("-_ ")
        if maybe:
            fut_requested = True
            base_for_fut = maybe

    # Index aliases (cash index unless future requested).
    index_key = INDEX_ALIASES.get(clean) or INDEX_ALIASES.get(clean_compact)
    if index_key and not fut_requested and (seg is None or seg.endswith("_INDEX")):
        return {
            "instrument_key": index_key,
            "trading_symbol": clean_compact,
            "segment": index_key.split("|", 1)[0],
            "instrument_type": "INDEX",
            "name": index_key.split("|", 1)[1],
        }

    # Commodity → nearest MCX future.
    commodity = COMMODITY_ALIASES.get(clean) or COMMODITY_ALIASES.get(clean_compact)
    if commodity and (seg is None or seg == "MCX_FO" or fut_requested):
        return _nearest_future(
            commodity,
            segment="MCX_FO",
            match_field="asset_symbol",
            rows=rows,
        )

    # Currency → nearest CDS future.
    currency = CURRENCY_ALIASES.get(clean) or CURRENCY_ALIASES.get(clean_compact)
    if currency and (seg is None or seg == "NCD_FO" or fut_requested):
        return _nearest_future(
            currency,
            segment="NCD_FO",
            match_field="name",
            rows=rows,
        )

    # Index / stock future aliases.
    if fut_requested:
        asset = base_for_fut.replace(" ", "")
        # Map index alias bases back to F&O underlying names.
        if asset in ("NIFTY50", "NIFTY"):
            asset = "NIFTY"
        elif asset in ("BANKNIFTY", "NIFTYBANK"):
            asset = "BANKNIFTY"
        elif asset in ("FINNIFTY",):
            asset = "FINNIFTY"
        elif asset in ("MIDCPNIFTY", "NIFTYMIDSELECT"):
            asset = "MIDCPNIFTY"
        target_seg = seg or (
            "MCX_FO"
            if asset in COMMODITY_ALIASES.values()
            else "NCD_FO"
            if asset in CURRENCY_ALIASES.values()
            else "NSE_FO"
        )
        match_field = "asset_symbol" if target_seg == "MCX_FO" else "asset_symbol"
        return _nearest_future(asset, segment=target_seg, match_field=match_field, rows=rows)

    # Default cash equity on NSE (or explicit segment).
    if seg in (None, "NSE_EQ", "BSE_EQ"):
        try:
            return _resolve_equity(clean_compact if " " not in clean else clean, segment=seg or "NSE_EQ", rows=rows)
        except UpstoxInstrumentError:
            if seg is not None:
                raise

    # Fall back: exact trading_symbol / name match across master (options etc.).
    return _lookup_trading_symbol(raw, segment=seg, instrument_type=instrument_type, rows=rows)


def _resolve_equity(
    symbol: str,
    *,
    segment: str,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    master = rows if rows is not None else load_instruments()
    needle = symbol.strip().upper()
    # BSE often uses numeric scrip codes as trading_symbol.
    candidates = [
        r
        for r in master
        if r.get("segment") == segment
        and str(r.get("instrument_type") or "").upper() in ("EQ", "BE", "SM", "ST", "SG", "IV", "")
        and (
            str(r.get("trading_symbol") or "").upper() == needle
            or str(r.get("exchange_token") or "") == needle
            or str(r.get("isin") or "").upper() == needle
        )
    ]
    if not candidates:
        # Broader EQ match without type filter (BSE has many subtypes).
        candidates = [
            r
            for r in master
            if r.get("segment") == segment
            and (
                str(r.get("trading_symbol") or "").upper() == needle
                or str(r.get("exchange_token") or "") == needle
            )
        ]
    if not candidates:
        raise UpstoxInstrumentError(f"no {segment} instrument for symbol '{symbol}'")
    # Prefer true EQ when multiple hit.
    candidates.sort(key=lambda r: 0 if str(r.get("instrument_type") or "").upper() == "EQ" else 1)
    return candidates[0]


def _nearest_future(
    asset: str,
    *,
    segment: str,
    match_field: str,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    master = rows if rows is not None else load_instruments()
    needle = asset.strip().upper()
    now_ms = int(time.time() * 1000)
    futures = []
    for r in master:
        if r.get("segment") != segment:
            continue
        if str(r.get("instrument_type") or "").upper() != "FUT":
            continue
        field_val = str(r.get(match_field) or r.get("underlying_symbol") or r.get("name") or "").upper()
        ts = str(r.get("trading_symbol") or "").upper()
        if field_val != needle and not ts.startswith(needle + " FUT"):
            continue
        # Prefer exact asset_symbol match over name-only GOLDGUINEA under name GOLD.
        asset_sym = str(r.get("asset_symbol") or "").upper()
        score = 0 if asset_sym == needle else 1
        expiry = int(r.get("expiry") or 0)
        if expiry and expiry < now_ms:
            continue
        futures.append((score, expiry or 2**63, r))
    if not futures:
        raise UpstoxInstrumentError(f"no active {segment} future for '{asset}'")
    futures.sort(key=lambda item: (item[0], item[1]))
    return futures[0][2]


def _lookup_trading_symbol(
    symbol: str,
    *,
    segment: str | None,
    instrument_type: str | None,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    master = rows if rows is not None else load_instruments()
    needle = symbol.strip().upper()
    needle_compact = "".join(ch for ch in needle if ch.isalnum())
    hits: list[dict[str, Any]] = []
    for r in master:
        if segment and r.get("segment") != segment:
            continue
        if instrument_type and str(r.get("instrument_type") or "").upper() != instrument_type.upper():
            continue
        ts = str(r.get("trading_symbol") or "").upper()
        if ts == needle or "".join(ch for ch in ts if ch.isalnum()) == needle_compact:
            hits.append(r)
    if not hits:
        raise UpstoxInstrumentError(f"unable to resolve Upstox instrument for '{symbol}'")
    # Prefer sooner expiry for derivatives.
    hits.sort(key=lambda r: int(r.get("expiry") or 0) or 2**63)
    return hits[0]


def nearest_atm_option(
    asset: str,
    *,
    option_type: str,
    spot: float | None = None,
    segment: str = "NSE_FO",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve nearest-expiry ATM CE/PE for an index or stock underlying.

    ``option_type`` must be ``CE`` or ``PE``. When ``spot`` is omitted, the
    median strike of the nearest expiry is used as a proxy ATM.
    """
    master = rows if rows is not None else load_instruments()
    needle = str(asset or "").strip().upper().replace(" ", "")
    if needle in ("NIFTY50",):
        needle = "NIFTY"
    elif needle in ("NIFTYBANK",):
        needle = "BANKNIFTY"
    opt = str(option_type or "").strip().upper()
    if opt not in ("CE", "PE"):
        raise UpstoxInstrumentError("option_type must be CE or PE")

    now_ms = int(time.time() * 1000)
    by_expiry: dict[int, list[dict[str, Any]]] = {}
    for r in master:
        if r.get("segment") != segment:
            continue
        if str(r.get("instrument_type") or "").upper() != opt:
            continue
        asset_sym = str(
            r.get("asset_symbol") or r.get("underlying_symbol") or r.get("name") or ""
        ).upper().replace(" ", "")
        ts = str(r.get("trading_symbol") or "").upper().replace(" ", "")
        if asset_sym != needle and not ts.startswith(needle):
            continue
        expiry = int(r.get("expiry") or 0)
        if expiry and expiry < now_ms:
            continue
        by_expiry.setdefault(expiry or 2**63, []).append(r)

    if not by_expiry:
        raise UpstoxInstrumentError(f"no active {opt} options for '{asset}'")

    nearest_exp = min(by_expiry)
    chain = by_expiry[nearest_exp]
    strikes: list[tuple[float, dict[str, Any]]] = []
    for r in chain:
        try:
            strike = float(r.get("strike_price") or r.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        strikes.append((strike, r))
    if not strikes:
        raise UpstoxInstrumentError(f"no strikes for '{asset}' {opt}")

    strikes.sort(key=lambda item: item[0])
    if spot is None or float(spot) <= 0:
        mid = strikes[len(strikes) // 2][0]
        target = mid
    else:
        target = float(spot)

    best = min(strikes, key=lambda item: abs(item[0] - target))
    return best[1]


def list_supported_aliases() -> dict[str, Iterable[str]]:
    """Return documented alias groups for CLI / docs surfaces."""
    return {
        "indices": sorted(INDEX_ALIASES),
        "commodities": sorted(COMMODITY_ALIASES),
        "currency": sorted(CURRENCY_ALIASES),
    }
