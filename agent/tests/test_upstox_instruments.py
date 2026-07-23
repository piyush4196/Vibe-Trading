"""Unit tests for Upstox instrument-key resolution (no network)."""

from __future__ import annotations

import time

import pytest

from src.trading.connectors.upstox import instruments as inst

pytestmark = pytest.mark.unit

NOW_MS = int(time.time() * 1000)
FUTURE_EXPIRY = NOW_MS + 30 * 24 * 3600 * 1000


SAMPLE_ROWS = [
    {
        "segment": "NSE_EQ",
        "name": "RELIANCE INDUSTRIES LTD",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE002A01018",
        "isin": "INE002A01018",
        "exchange_token": "2885",
        "trading_symbol": "RELIANCE",
    },
    {
        "segment": "BSE_EQ",
        "name": "RELIANCE INDUSTRIES LTD",
        "instrument_type": "EQ",
        "instrument_key": "BSE_EQ|INE002A01018",
        "isin": "INE002A01018",
        "exchange_token": "500325",
        "trading_symbol": "RELIANCE",
    },
    {
        "segment": "NSE_INDEX",
        "name": "Nifty 50",
        "instrument_type": "INDEX",
        "instrument_key": "NSE_INDEX|Nifty 50",
        "trading_symbol": "NIFTY",
    },
    {
        "segment": "NSE_FO",
        "name": "NIFTY",
        "instrument_type": "FUT",
        "asset_symbol": "NIFTY",
        "instrument_key": "NSE_FO|111",
        "trading_symbol": "NIFTY FUT 28 JUL 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "NSE_FO",
        "name": "RELIANCE INDUSTRIES LTD",
        "instrument_type": "FUT",
        "asset_symbol": "RELIANCE",
        "instrument_key": "NSE_FO|222",
        "trading_symbol": "RELIANCE FUT 28 JUL 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "NSE_FO",
        "name": "BANKNIFTY",
        "instrument_type": "CE",
        "asset_symbol": "BANKNIFTY",
        "instrument_key": "NSE_FO|333",
        "trading_symbol": "BANKNIFTY 50000 CE 28 JUL 26",
        "expiry": FUTURE_EXPIRY,
        "strike_price": 50000.0,
    },
    {
        "segment": "MCX_FO",
        "name": "GOLD",
        "instrument_type": "FUT",
        "asset_symbol": "GOLDGUINEA",
        "instrument_key": "MCX_FO|1",
        "trading_symbol": "GOLDGUINEA FUT 31 JUL 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "MCX_FO",
        "name": "GOLD",
        "instrument_type": "FUT",
        "asset_symbol": "GOLD",
        "instrument_key": "MCX_FO|2",
        "trading_symbol": "GOLD FUT 05 AUG 26",
        "expiry": FUTURE_EXPIRY + 1000,
    },
    {
        "segment": "MCX_FO",
        "name": "CRUDE OIL",
        "instrument_type": "FUT",
        "asset_symbol": "CRUDEOIL",
        "instrument_key": "MCX_FO|3",
        "trading_symbol": "CRUDEOIL FUT 19 AUG 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "MCX_FO",
        "name": "SILVER",
        "instrument_type": "FUT",
        "asset_symbol": "SILVER",
        "instrument_key": "MCX_FO|4",
        "trading_symbol": "SILVER FUT 04 SEP 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "NCD_FO",
        "name": "USDINR",
        "instrument_type": "FUT",
        "asset_symbol": "USDINR",
        "instrument_key": "NCD_FO|1038",
        "trading_symbol": "USDINR FUT 24 JUL 26",
        "expiry": FUTURE_EXPIRY,
    },
    {
        "segment": "NCD_FO",
        "name": "EURINR",
        "instrument_type": "FUT",
        "asset_symbol": "EURINR",
        "instrument_key": "NCD_FO|2000",
        "trading_symbol": "EURINR FUT 24 JUL 26",
        "expiry": FUTURE_EXPIRY,
    },
]


def test_index_aliases() -> None:
    nifty = inst.resolve_instrument("NIFTY", rows=SAMPLE_ROWS)
    assert nifty["instrument_key"] == "NSE_INDEX|Nifty 50"
    bank = inst.resolve_instrument("BANKNIFTY", rows=SAMPLE_ROWS)
    assert bank["instrument_key"] == "NSE_INDEX|Nifty Bank"
    fin = inst.resolve_instrument("FINNIFTY", rows=SAMPLE_ROWS)
    assert fin["instrument_key"] == "NSE_INDEX|Nifty Fin Service"
    mid = inst.resolve_instrument("MIDCPNIFTY", rows=SAMPLE_ROWS)
    assert mid["instrument_key"] == "NSE_INDEX|NIFTY MID SELECT"


def test_nse_bse_equity_suffixes() -> None:
    nse = inst.resolve_instrument("RELIANCE.NS", rows=SAMPLE_ROWS)
    assert nse["instrument_key"] == "NSE_EQ|INE002A01018"
    bse = inst.resolve_instrument("500325.BO", rows=SAMPLE_ROWS)
    assert bse["instrument_key"] == "BSE_EQ|INE002A01018"
    bare = inst.resolve_instrument("RELIANCE", rows=SAMPLE_ROWS)
    assert bare["segment"] == "NSE_EQ"


def test_commodity_and_currency_nearest_futures() -> None:
    gold = inst.resolve_instrument("GOLD", rows=SAMPLE_ROWS)
    assert gold["instrument_key"] == "MCX_FO|2"  # prefer asset_symbol GOLD over GOLDGUINEA
    crude = inst.resolve_instrument("CRUDEOIL", rows=SAMPLE_ROWS)
    assert crude["instrument_key"] == "MCX_FO|3"
    silver = inst.resolve_instrument("SILVER", rows=SAMPLE_ROWS)
    assert silver["instrument_key"] == "MCX_FO|4"
    usd = inst.resolve_instrument("USDINR", rows=SAMPLE_ROWS)
    assert usd["instrument_key"] == "NCD_FO|1038"
    eur = inst.resolve_instrument("EURINR", rows=SAMPLE_ROWS)
    assert eur["instrument_key"] == "NCD_FO|2000"


def test_stock_and_index_futures() -> None:
    nifty_fut = inst.resolve_instrument("NIFTY-FUT", rows=SAMPLE_ROWS)
    assert nifty_fut["instrument_key"] == "NSE_FO|111"
    rel_fut = inst.resolve_instrument("RELIANCE-FUT", rows=SAMPLE_ROWS)
    assert rel_fut["instrument_key"] == "NSE_FO|222"


def test_option_trading_symbol_and_instrument_key() -> None:
    opt = inst.resolve_instrument("BANKNIFTY 50000 CE 28 JUL 26", rows=SAMPLE_ROWS)
    assert opt["instrument_key"] == "NSE_FO|333"
    key = inst.resolve_instrument("NSE_FO|99999", rows=SAMPLE_ROWS)
    assert key["instrument_key"] == "NSE_FO|99999"


def test_unknown_symbol_raises() -> None:
    with pytest.raises(inst.UpstoxInstrumentError):
        inst.resolve_instrument("NOTAREALTICKERXYZ", rows=SAMPLE_ROWS)
