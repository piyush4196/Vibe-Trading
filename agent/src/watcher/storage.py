"""SQLite persistence for ticks, candles, signals, positions, outcomes."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.watcher.models import Candle, OpenPosition, Side, Signal, Tick, utc_now


class WatcherStore:
    """Thread-safe SQLite store under the watcher state directory."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_key TEXT NOT NULL,
                    symbol TEXT,
                    ts TEXT NOT NULL,
                    ltp REAL, volume REAL, oi REAL,
                    bid REAL, ask REAL, vwap REAL,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ticks_key_ts ON ticks(instrument_key, ts);

                CREATE TABLE IF NOT EXISTS candles (
                    instrument_key TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, oi REAL, vwap REAL,
                    closed INTEGER DEFAULT 1,
                    PRIMARY KEY (instrument_key, timeframe, ts)
                );

                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    instrument TEXT,
                    instrument_key TEXT,
                    side TEXT,
                    confidence REAL,
                    status TEXT,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_log (
                    instrument_key TEXT NOT NULL,
                    side TEXT NOT NULL,
                    last_alert_at TEXT NOT NULL,
                    last_confidence REAL,
                    last_entry REAL,
                    last_stop REAL,
                    last_target REAL,
                    PRIMARY KEY (instrument_key, side)
                );

                CREATE TABLE IF NOT EXISTS positions (
                    signal_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT
                );

                CREATE TABLE IF NOT EXISTS outcomes (
                    signal_id TEXT PRIMARY KEY,
                    created_at TEXT,
                    closed_at TEXT,
                    pnl_pct REAL,
                    holding_seconds REAL,
                    exit_reason TEXT,
                    payload TEXT
                );

                CREATE TABLE IF NOT EXISTS processed_candles (
                    instrument_key TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    PRIMARY KEY (instrument_key, timeframe, ts)
                );
                """
            )
            self._conn.commit()

    # ---- ticks / candles -------------------------------------------------

    def insert_tick(self, tick: Tick) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ticks(instrument_key, symbol, ts, ltp, volume, oi, bid, ask, vwap, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick.instrument_key,
                    tick.symbol,
                    tick.ts.isoformat(),
                    tick.ltp,
                    tick.volume,
                    tick.oi,
                    tick.bid,
                    tick.ask,
                    tick.vwap,
                    json.dumps(tick.raw)[:8000],
                ),
            )
            self._conn.commit()

    def upsert_candle(self, candle: Candle) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO candles(instrument_key, timeframe, ts, open, high, low, close, volume, oi, vwap, closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_key, timeframe, ts) DO UPDATE SET
                    high=MAX(high, excluded.high),
                    low=MIN(low, excluded.low),
                    close=excluded.close,
                    volume=excluded.volume,
                    oi=excluded.oi,
                    vwap=excluded.vwap,
                    closed=excluded.closed
                """,
                (
                    candle.instrument_key,
                    candle.timeframe,
                    candle.ts.isoformat(),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.oi,
                    candle.vwap,
                    1 if candle.closed else 0,
                ),
            )
            self._conn.commit()

    def load_candles(
        self,
        instrument_key: str,
        timeframe: str,
        *,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts, open, high, low, close, volume, oi, vwap
                FROM candles
                WHERE instrument_key=? AND timeframe=?
                ORDER BY ts DESC LIMIT ?
                """,
                (instrument_key, timeframe, limit),
            ).fetchall()
        out = [dict(r) for r in reversed(rows)]
        return out

    def mark_candle_processed(self, instrument_key: str, timeframe: str, ts: datetime) -> bool:
        """Return True if this candle is newly marked (not a duplicate)."""
        key_ts = ts.isoformat()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO processed_candles(instrument_key, timeframe, ts) VALUES (?, ?, ?)",
                    (instrument_key, timeframe, key_ts),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # ---- signals / anti-spam ---------------------------------------------

    def save_signal(self, signal: Signal) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO signals(signal_id, created_at, instrument, instrument_key, side, confidence, status, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.created_at.isoformat(),
                    signal.instrument,
                    signal.instrument_key,
                    signal.side.value,
                    signal.confidence,
                    signal.status,
                    json.dumps(signal.to_dict()),
                ),
            )
            self._conn.commit()

    def get_alert_state(self, instrument_key: str, side: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM alert_log WHERE instrument_key=? AND side=?",
                (instrument_key, side),
            ).fetchone()
        return dict(row) if row else None

    def upsert_alert_state(
        self,
        instrument_key: str,
        side: str,
        *,
        confidence: float,
        entry: float,
        stop: float,
        target: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO alert_log(instrument_key, side, last_alert_at, last_confidence, last_entry, last_stop, last_target)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_key, side) DO UPDATE SET
                    last_alert_at=excluded.last_alert_at,
                    last_confidence=excluded.last_confidence,
                    last_entry=excluded.last_entry,
                    last_stop=excluded.last_stop,
                    last_target=excluded.last_target
                """,
                (
                    instrument_key,
                    side,
                    utc_now().isoformat(),
                    confidence,
                    entry,
                    stop,
                    target,
                ),
            )
            self._conn.commit()

    # ---- positions / outcomes --------------------------------------------

    def save_position(self, pos: OpenPosition) -> None:
        payload = {
            "signal_id": pos.signal_id,
            "instrument": pos.instrument,
            "instrument_key": pos.instrument_key,
            "side": pos.side.value,
            "entry": pos.entry,
            "stop_loss": pos.stop_loss,
            "targets": pos.targets,
            "trailing_stop": pos.trailing_stop,
            "opened_at": pos.opened_at.isoformat(),
            "status": pos.status,
            "peak_price": pos.peak_price,
            "trough_price": pos.trough_price,
            "auto_traded": bool(getattr(pos, "auto_traded", False)),
            "quantity": float(getattr(pos, "quantity", 0) or 0),
            "order_id": str(getattr(pos, "order_id", "") or ""),
        }
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO positions(signal_id, payload, status) VALUES (?, ?, ?)",
                (pos.signal_id, json.dumps(payload), pos.status),
            )
            self._conn.commit()

    def list_open_positions(self) -> list[OpenPosition]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM positions WHERE status='open'"
            ).fetchall()
        out: list[OpenPosition] = []
        for row in rows:
            data = json.loads(row["payload"])
            out.append(
                OpenPosition(
                    signal_id=data["signal_id"],
                    instrument=data["instrument"],
                    instrument_key=data["instrument_key"],
                    side=Side(data["side"]),
                    entry=float(data["entry"]),
                    stop_loss=float(data["stop_loss"]),
                    targets=list(data.get("targets") or []),
                    trailing_stop=data.get("trailing_stop"),
                    opened_at=datetime.fromisoformat(data["opened_at"]),
                    status=data.get("status", "open"),
                    peak_price=float(data.get("peak_price") or 0),
                    trough_price=float(data.get("trough_price") or 0),
                    auto_traded=bool(data.get("auto_traded") or False),
                    quantity=float(data.get("quantity") or 0),
                    order_id=str(data.get("order_id") or ""),
                )
            )
        return out

    def save_outcome(
        self,
        signal_id: str,
        *,
        pnl_pct: float,
        holding_seconds: float,
        exit_reason: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO outcomes(signal_id, created_at, closed_at, pnl_pct, holding_seconds, exit_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    payload.get("created_at"),
                    utc_now().isoformat(),
                    pnl_pct,
                    holding_seconds,
                    exit_reason,
                    json.dumps(payload),
                ),
            )
            self._conn.execute(
                "UPDATE positions SET status=? WHERE signal_id=?",
                ("closed", signal_id),
            )
            self._conn.execute(
                "UPDATE signals SET status=? WHERE signal_id=?",
                (exit_reason, signal_id),
            )
            self._conn.commit()

    def recent_outcomes(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outcomes ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
