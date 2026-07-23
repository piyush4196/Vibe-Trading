"""Curated read/write classification for Upstox REST operations.

Keys are the connector's own operation names. Order-mutating SDK calls are
pinned WRITE so the live gate never treats them as plain reads; anything
unlisted and not a known read is treated as WRITE (fail-closed) by the gate.
"""

from __future__ import annotations

from src.live.classification import ToolClass

#: Upstox SDK operation read/write catalog.
UPSTOX_TOOL_CLASS: dict[str, ToolClass] = {
    # READ
    "get_profile": ToolClass.READ,
    "get_user_profile": ToolClass.READ,
    "get_funds_and_margin": ToolClass.READ,
    "get_holdings": ToolClass.READ,
    "get_positions": ToolClass.READ,
    "get_short_term_positions": ToolClass.READ,
    "get_order_book": ToolClass.READ,
    "get_order_history": ToolClass.READ,
    "get_trade_history": ToolClass.READ,
    "get_historical_candle_data": ToolClass.READ,
    "get_intra_day_candle_data": ToolClass.READ,
    "get_market_quote_ohlc": ToolClass.READ,
    "get_market_quote_ltp": ToolClass.READ,
    "get_full_market_quote": ToolClass.READ,
    "get_account_snapshot": ToolClass.READ,
    "get_open_orders": ToolClass.READ,
    "get_quote": ToolClass.READ,
    "get_historical_bars": ToolClass.READ,
    "check_status": ToolClass.READ,
    # WRITE
    "place_order": ToolClass.WRITE,
    "modify_order": ToolClass.WRITE,
    "cancel_order": ToolClass.WRITE,
    "place_multi_order": ToolClass.WRITE,
}
