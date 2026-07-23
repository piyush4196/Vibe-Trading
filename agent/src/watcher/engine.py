"""Watcher engine — orchestrates the six background workers."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.trading.connectors.upstox import sdk as upstox_sdk
from src.watcher.antispam import should_emit
from src.watcher.auto_trade import entry_succeeded, place_entry_order, place_exit_order
from src.watcher.candles import CandleBuilder, TIMEFRAMES_MINUTES
from src.watcher.config import WatcherConfig
from src.watcher.decision import evaluate_instrument
from src.watcher.feed_rest import RestPollCollector
from src.watcher.feed_ws import UpstoxWebSocketCollector
from src.watcher.learning import LearningEngine
from src.watcher.market_filter import build_market_context
from src.watcher.market_hours import any_indian_market_open, seconds_until_next_open
from src.watcher.models import Candle, MarketContext, OpenPosition, Tick, WatchInstrument
from src.watcher.notify_telegram import TelegramNotifier
from src.watcher.positions import PositionMonitor
from src.watcher.storage import WatcherStore
from src.watcher.universe import build_universe

logger = logging.getLogger(__name__)

# Map watcher timeframes → Upstox history period tokens.
_SEED_PERIOD = {
    "1m": "1m",
    "3m": "5m",  # approximate seed; builder will refine from ticks
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1D": "1d",
}


class WatcherEngine:
    """Always-on Indian market desk analyst."""

    def __init__(self, config: WatcherConfig | None = None):
        self.config = config or WatcherConfig.load()
        self.state_dir = self.config.state_dir()
        self.store = WatcherStore(self.state_dir / "watcher.db")
        self.notifier = TelegramNotifier(self.config)
        self.learning = LearningEngine(self.store)
        self.upstox_cfg = upstox_sdk.load_config()
        self.rest = RestPollCollector(self.upstox_cfg, on_tick=self._on_tick)
        self.ws = UpstoxWebSocketCollector(
            self.upstox_cfg,
            mode=self.config.websocket_mode,
            on_tick=self._on_tick,
            on_market_info=self._on_market_info,
        )
        self.candles = CandleBuilder(on_candle_close=self._on_candle_close)
        self.universe: list[WatchInstrument] = []
        self.market_ctx = MarketContext()
        self._ltp: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._analysis_q: list[tuple[str, str]] = []
        self.stats: dict[str, Any] = {
            "ticks": 0,
            "candles_closed": 0,
            "signals": 0,
            "alerts_sent": 0,
            "alerts_suppressed": 0,
            "orders_placed": 0,
            "orders_failed": 0,
            "started_at": None,
        }
        self.positions = PositionMonitor(
            self.store,
            notify=lambda text: self.notifier.send_markdown(text),
            load_bars=self._load_bars,
            get_ltp=lambda key: self._ltp.get(key),
            on_exit=self._on_position_exit,
        )

    # ------------------------------------------------------------------ API

    def start(self, *, blocking: bool = True) -> None:
        if not upstox_sdk.upstox_configured(self.upstox_cfg):
            raise RuntimeError(
                "Upstox access_token missing. Configure ~/.vibe-trading/upstox.json first."
            )
        self.config.save()
        self._write_pid()
        self.stats["started_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Building watch universe…")
        self.universe = build_universe(self.config)
        logger.info("Universe size: %d instruments", len(self.universe))
        self._seed_history()
        self.market_ctx = build_market_context(self._load_bars)

        workers = [
            ("tick_collector", self._worker_tick_collector),
            ("indicator_scanner", self._worker_scanner),
            ("position_monitor", self._worker_positions),
            ("learning", self._worker_learning),
            ("market_filter", self._worker_market_filter),
        ]
        # WebSocket worker only in auto/websocket modes.
        if self.config.feed_mode in ("auto", "websocket"):
            workers.insert(0, ("websocket_feed", self._worker_websocket))

        for name, target in workers:
            t = threading.Thread(target=target, name=f"watcher-{name}", daemon=True)
            t.start()
            self._threads.append(t)
            logger.info("Started worker: %s", name)

        if blocking:
            try:
                while not self._stop.is_set():
                    time.sleep(1.0)
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.ws.stop()
        self._clear_pid()
        logger.info("Watcher stopped. stats=%s", self.stats)

    def status(self) -> dict[str, Any]:
        return {
            "running": self._pid_running(),
            "universe": len(self.universe) or None,
            "stats": self.stats,
            "learning": self.learning.summary(),
            "ws_connected": self.ws.connected,
            "ws_error": self.ws.last_error,
            "market_open": any_indian_market_open(segment_status=self.ws.segment_status or None),
            "config": {
                "min_confidence": self.config.min_confidence,
                "feed_mode": self.config.feed_mode,
                "dry_run": self.config.dry_run,
                "auto_trade_enabled": self.config.auto_trade_enabled,
                "auto_trade_quantity": self.config.auto_trade_quantity,
                "watch_only_symbols": self.config.watch_only_symbols,
            },
        }

    # -------------------------------------------------------------- workers

    def _worker_websocket(self) -> None:
        while not self._stop.is_set():
            if not any_indian_market_open(segment_status=self.ws.segment_status or None):
                time.sleep(min(seconds_until_next_open(), 60))
                continue
            try:
                asyncio.run(self.ws.run(self.universe))
            except Exception as exc:  # noqa: BLE001
                logger.warning("WS worker crash: %s", exc)
            time.sleep(5)

    def _worker_tick_collector(self) -> None:
        """Worker 1 — REST poll (always on as reliable backbone)."""
        while not self._stop.is_set():
            if self.config.feed_mode == "websocket" and self.ws.connected:
                time.sleep(self.config.poll_interval_seconds)
                continue
            if not any_indian_market_open(segment_status=self.ws.segment_status or None):
                time.sleep(min(seconds_until_next_open(), 60))
                continue
            try:
                self.rest.poll(self.universe)
            except Exception as exc:  # noqa: BLE001
                logger.warning("poll error: %s", exc)
            time.sleep(self.config.poll_interval_seconds)

    def _worker_scanner(self) -> None:
        """Workers 2+3 — indicator calc + AI decision on closed candles."""
        while not self._stop.is_set():
            item = None
            with self._lock:
                if self._analysis_q:
                    item = self._analysis_q.pop(0)
            if not item:
                time.sleep(0.25)
                continue
            key, tf = item
            if tf not in ("1m", "5m", "15m"):
                # Analyze on these triggers; still load full MTF stack.
                continue
            inst = next((i for i in self.universe if i.instrument_key == key), None)
            if not inst:
                continue
            try:
                self._analyze(inst)
            except Exception as exc:  # noqa: BLE001
                logger.debug("analyze %s failed: %s", inst.symbol, exc)

    def _worker_positions(self) -> None:
        """Worker 5 — position / exit monitor."""
        while not self._stop.is_set():
            try:
                self.positions.scan()
            except Exception as exc:  # noqa: BLE001
                logger.debug("position scan: %s", exc)
            time.sleep(max(5.0, self.config.poll_interval_seconds))

    def _worker_learning(self) -> None:
        """Worker 6 — periodic learning summary."""
        while not self._stop.is_set():
            try:
                summary = self.learning.summary()
                (self.state_dir / "learning_summary.json").write_text(
                    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("learning: %s", exc)
            self._stop.wait(300)

    def _worker_market_filter(self) -> None:
        while not self._stop.is_set():
            try:
                self.market_ctx = build_market_context(self._load_bars)
            except Exception as exc:  # noqa: BLE001
                logger.debug("market filter: %s", exc)
            self._stop.wait(60)

    # --------------------------------------------------------------- events

    def _on_tick(self, tick: Tick) -> None:
        self.stats["ticks"] = int(self.stats.get("ticks") or 0) + 1
        self._ltp[tick.instrument_key] = tick.ltp
        try:
            self.store.insert_tick(tick)
        except Exception:
            pass
        closed = self.candles.ingest(tick)
        for c in closed:
            self._persist_candle(c)

    def _on_candle_close(self, candle: Candle) -> None:
        self._persist_candle(candle)
        if not self.store.mark_candle_processed(candle.instrument_key, candle.timeframe, candle.ts):
            return  # duplicate
        self.stats["candles_closed"] = int(self.stats.get("candles_closed") or 0) + 1
        with self._lock:
            self._analysis_q.append((candle.instrument_key, candle.timeframe))
            # Bound queue
            if len(self._analysis_q) > 5000:
                self._analysis_q = self._analysis_q[-2000:]

    def _on_market_info(self, status: dict[str, str]) -> None:
        logger.info("Market segment status: %s", status)

    def _persist_candle(self, candle: Candle) -> None:
        try:
            self.store.upsert_candle(candle)
        except Exception as exc:  # noqa: BLE001
            logger.debug("candle persist: %s", exc)

    def _analyze(self, inst: WatchInstrument) -> None:
        bars_by_tf = {tf: self._load_bars(inst.instrument_key, tf) for tf in TIMEFRAMES_MINUTES}
        live = {"bid": 0.0, "ask": 0.0}
        signal = evaluate_instrument(
            inst,
            bars_by_tf,
            config=self.config,
            market_ctx=self.market_ctx,
            live_tick=live,
            confidence_calibration=self.learning.confidence_calibration(),
        )
        if signal is None:
            return
        self.stats["signals"] = int(self.stats.get("signals") or 0) + 1
        self.store.save_signal(signal)
        ok, why = should_emit(signal, self.store, self.config)
        if not ok:
            self.stats["alerts_suppressed"] = int(self.stats.get("alerts_suppressed") or 0) + 1
            logger.info("Suppressed alert %s (%s)", signal.instrument, why)
            return
        # Worker 4 — Telegram
        result = self.notifier.send_signal(signal)
        self.store.upsert_alert_state(
            signal.instrument_key,
            signal.side.value,
            confidence=signal.confidence,
            entry=signal.entry,
            stop=signal.stop_loss,
            target=signal.target_1,
        )
        trade = place_entry_order(signal, self.config)
        auto_traded = entry_succeeded(trade)
        if trade.get("status") == "error":
            self.stats["orders_failed"] = int(self.stats.get("orders_failed") or 0) + 1
        elif auto_traded:
            self.stats["orders_placed"] = int(self.stats.get("orders_placed") or 0) + 1
            self.notifier.send_markdown(
                f"🧾 *Auto-trade entry*\n`{signal.side.value}` `{signal.instrument}` "
                f"qty `{trade.get('quantity')}` conf `{signal.confidence:.0f}%`"
            )

        order_id = ""
        if isinstance(trade.get("result"), dict):
            order_id = str(trade["result"].get("order_id") or "")
        self.positions.track_signal(
            signal,
            auto_traded=auto_traded,
            quantity=float(trade.get("quantity") or self.config.auto_trade_quantity or 0),
            order_id=order_id,
        )
        self.stats["alerts_sent"] = int(self.stats.get("alerts_sent") or 0) + 1
        logger.info(
            "ALERT %s %s conf=%.1f rr=1:%.1f telegram=%s auto_trade=%s",
            signal.side.value,
            signal.instrument,
            signal.confidence,
            signal.risk_reward,
            result.get("status"),
            trade.get("status"),
        )

    def _on_position_exit(self, pos: OpenPosition, reason: str) -> None:
        if not self.config.auto_trade_on_exit:
            return
        trade = place_exit_order(pos, self.config, reason=reason)
        if trade.get("status") == "skipped":
            return
        status = str(trade.get("status") or "").lower()
        if status in ("error", "rejected", "unsupported"):
            self.stats["orders_failed"] = int(self.stats.get("orders_failed") or 0) + 1
            return
        self.stats["orders_placed"] = int(self.stats.get("orders_placed") or 0) + 1
        self.notifier.send_markdown(
            f"🧾 *Auto-trade exit* (`{reason}`)\n`{pos.instrument}` qty `{trade.get('quantity')}`"
        )

    # ---------------------------------------------------------------- seed

    def _seed_history(self) -> None:
        logger.info("Seeding historical candles…")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(self._seed_one, self.universe))

    def _seed_one(self, inst: WatchInstrument) -> None:
        for tf, period in (("5m", "5m"), ("15m", "15m"), ("1h", "1h"), ("1D", "1d")):
            try:
                bars = self.rest.fetch_history(inst, period=period, limit=self.config.lookback_bars)
                candles = self.candles.seed_from_bars(inst.instrument_key, tf, bars)
                for c in candles:
                    self.store.upsert_candle(c)
            except Exception as exc:  # noqa: BLE001
                logger.debug("seed %s %s: %s", inst.symbol, tf, exc)

    def _load_bars(self, instrument_key: str, timeframe: str) -> list[dict]:
        rows = self.store.load_candles(instrument_key, timeframe, limit=self.config.lookback_bars)
        if rows:
            return rows
        # Fallback empty — analysis will reject insufficient bars.
        return []

    # ------------------------------------------------------------------ pid

    def _pid_path(self) -> Path:
        return self.state_dir / "watcher.pid"

    def _write_pid(self) -> None:
        import os

        self._pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")

    def _clear_pid(self) -> None:
        try:
            self._pid_path().unlink(missing_ok=True)  # type: ignore[arg-type]
        except TypeError:
            p = self._pid_path()
            if p.exists():
                p.unlink()

    def _pid_running(self) -> bool:
        p = self._pid_path()
        if not p.exists():
            return False
        try:
            lines = p.read_text(encoding="utf-8").strip().splitlines()
            pid = int(lines[-1])
            import os

            os.kill(pid, 0)
            return True
        except Exception:
            return False
