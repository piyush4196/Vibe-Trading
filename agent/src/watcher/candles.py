"""In-memory candle builder — aggregates ticks into multi-timeframe bars."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from src.watcher.models import Candle, Tick

TIMEFRAMES_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1D": 24 * 60,
}


def _floor_ts(ts: datetime, minutes: int) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch = int(ts.timestamp())
    bucket = epoch - (epoch % (minutes * 60))
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


class CandleBuilder:
    """Maintain live candles per instrument/timeframe; emit on close."""

    def __init__(self, on_candle_close: Callable[[Candle], None] | None = None):
        self._buckets: dict[tuple[str, str], Candle] = {}
        self._on_close = on_candle_close

    def ingest(self, tick: Tick) -> list[Candle]:
        """Update all timeframes for ``tick``; return newly closed candles."""
        closed: list[Candle] = []
        for tf, minutes in TIMEFRAMES_MINUTES.items():
            key = (tick.instrument_key, tf)
            bucket_ts = _floor_ts(tick.ts, minutes)
            current = self._buckets.get(key)
            if current is None or current.ts != bucket_ts:
                if current is not None and current.ts < bucket_ts:
                    current.closed = True
                    closed.append(current)
                    if self._on_close:
                        self._on_close(current)
                self._buckets[key] = Candle(
                    instrument_key=tick.instrument_key,
                    timeframe=tf,
                    ts=bucket_ts,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    volume=float(tick.volume or 0),
                    oi=float(tick.oi or 0),
                    vwap=float(tick.vwap or tick.ltp),
                    closed=False,
                )
            else:
                current.high = max(current.high, tick.ltp)
                current.low = min(current.low, tick.ltp)
                current.close = tick.ltp
                if tick.volume:
                    current.volume = max(current.volume, float(tick.volume))
                if tick.oi:
                    current.oi = float(tick.oi)
                if tick.vwap:
                    current.vwap = float(tick.vwap)
        return closed

    def snapshot(self, instrument_key: str, timeframe: str) -> Candle | None:
        return self._buckets.get((instrument_key, timeframe))

    def seed_from_bars(
        self,
        instrument_key: str,
        timeframe: str,
        bars: list[dict],
    ) -> list[Candle]:
        """Load historical OHLCV into the builder (all marked closed)."""
        candles: list[Candle] = []
        for bar in bars:
            ts = bar.get("ts") or bar.get("time")
            if isinstance(ts, str):
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif isinstance(ts, (int, float)):
                # epoch seconds or ms
                ts_f = float(ts)
                if ts_f > 1e12:
                    ts_f /= 1000.0
                ts_dt = datetime.fromtimestamp(ts_f, tz=timezone.utc)
            else:
                continue
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            c = Candle(
                instrument_key=instrument_key,
                timeframe=timeframe,
                ts=_floor_ts(ts_dt, TIMEFRAMES_MINUTES.get(timeframe, 1)),
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                close=float(bar["close"]),
                volume=float(bar.get("volume") or 0),
                oi=float(bar.get("oi") or 0),
                vwap=float(bar.get("vwap") or bar["close"]),
                closed=True,
            )
            candles.append(c)
            self._buckets[(instrument_key, timeframe)] = c
        return candles
