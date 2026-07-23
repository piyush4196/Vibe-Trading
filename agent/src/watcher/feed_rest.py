"""REST poll collector — reliable Upstox market data path."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Iterable

from src.trading.connectors.upstox import sdk as upstox_sdk
from src.watcher.models import Tick, WatchInstrument

logger = logging.getLogger(__name__)


class RestPollCollector:
    """Fetch LTP/OHLC quotes and seed historical candles via Upstox REST."""

    def __init__(
        self,
        config: upstox_sdk.UpstoxConfig | None = None,
        on_tick: Callable[[Tick], None] | None = None,
    ):
        self.cfg = config or upstox_sdk.load_config()
        self.on_tick = on_tick

    def poll(self, instruments: Iterable[WatchInstrument]) -> list[Tick]:
        ticks: list[Tick] = []
        for inst in instruments:
            try:
                envelope = upstox_sdk.get_quote(
                    inst.symbol,
                    config=self.cfg,
                    instrument_key=inst.instrument_key,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("quote failed %s: %s", inst.symbol, exc)
                continue
            if str(envelope.get("status", "")).lower() != "ok":
                continue
            q = envelope.get("quote") or {}
            ltp = float(q.get("ltp") or q.get("close") or 0)
            if not ltp:
                continue
            tick = Tick(
                instrument_key=inst.instrument_key,
                symbol=inst.symbol,
                ts=datetime.now(timezone.utc),
                ltp=ltp,
                volume=float(q.get("volume") or 0),
                open=float(q.get("open") or 0),
                high=float(q.get("high") or 0),
                low=float(q.get("low") or 0),
                close=float(q.get("close") or ltp),
                prev_close=float(q.get("close") or 0),
                vwap=float(q.get("vwap") or ltp),
                raw=envelope,
            )
            ticks.append(tick)
            if self.on_tick:
                self.on_tick(tick)
        return ticks

    def fetch_history(
        self,
        instrument: WatchInstrument,
        *,
        period: str = "5m",
        limit: int = 250,
    ) -> list[dict]:
        envelope = upstox_sdk.get_historical_bars(
            instrument.symbol,
            config=self.cfg,
            instrument_key=instrument.instrument_key,
            period=period,
            limit=limit,
        )
        if str(envelope.get("status", "")).lower() != "ok":
            return []
        return list(envelope.get("bars") or [])
