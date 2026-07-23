"""Position monitor + exit engine."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from src.watcher.indicators import candles_to_frame, compute_indicator_bundle
from src.watcher.models import OpenPosition, Side, Signal
from src.watcher.storage import WatcherStore

logger = logging.getLogger(__name__)


class PositionMonitor:
    def __init__(
        self,
        store: WatcherStore,
        notify: Callable[[str], None],
        load_bars: Callable[[str, str], list[dict]],
        get_ltp: Callable[[str], float | None],
    ):
        self.store = store
        self.notify = notify
        self.load_bars = load_bars
        self.get_ltp = get_ltp

    def track_signal(self, signal: Signal) -> OpenPosition:
        pos = OpenPosition(
            signal_id=signal.signal_id,
            instrument=signal.instrument,
            instrument_key=signal.instrument_key,
            side=signal.side,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            targets=[signal.target_1, signal.target_2, signal.target_3],
            peak_price=signal.entry,
            trough_price=signal.entry,
        )
        self.store.save_position(pos)
        return pos

    def scan(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for pos in self.store.list_open_positions():
            ltp = self.get_ltp(pos.instrument_key)
            if ltp is None:
                continue
            pos.peak_price = max(pos.peak_price or ltp, ltp)
            pos.trough_price = min(pos.trough_price or ltp, ltp) if pos.trough_price else ltp

            # Target / stop
            if pos.side is Side.BUY:
                if ltp <= pos.stop_loss:
                    events.append(self._close(pos, ltp, "stop_loss_hit"))
                    continue
                for i, tgt in enumerate(pos.targets, start=1):
                    if ltp >= tgt:
                        events.append(self._close(pos, ltp, f"target_{i}_hit"))
                        break
                else:
                    # Trailing stop: lock 50% of open profit once T1 approached
                    if pos.targets and ltp >= pos.targets[0]:
                        trail = max(pos.stop_loss, pos.entry + 0.5 * (ltp - pos.entry))
                        if pos.trailing_stop is None or trail > pos.trailing_stop:
                            pos.trailing_stop = trail
                            pos.stop_loss = trail
                            self.store.save_position(pos)
                            events.append(
                                {
                                    "type": "trailing_stop_update",
                                    "instrument": pos.instrument,
                                    "stop": trail,
                                }
                            )
                            self.notify(
                                f"🛡 *Trailing Stop Update*\n`{pos.instrument}` → `{trail:.2f}`"
                            )
            else:
                if ltp >= pos.stop_loss:
                    events.append(self._close(pos, ltp, "stop_loss_hit"))
                    continue
                for i, tgt in enumerate(pos.targets, start=1):
                    if ltp <= tgt:
                        events.append(self._close(pos, ltp, f"target_{i}_hit"))
                        break

            # Early exit recommendation
            exit_reason = self._early_exit(pos, ltp)
            if exit_reason:
                events.append({"type": "exit_recommended", "instrument": pos.instrument, "reason": exit_reason})
                self.notify(
                    f"⚠️ *Exit Recommended*\n`{pos.instrument}`\nReason: {exit_reason}"
                )
            else:
                self.store.save_position(pos)
        return events

    def _early_exit(self, pos: OpenPosition, ltp: float) -> str | None:
        rows = self.load_bars(pos.instrument_key, "5m")
        ind = compute_indicator_bundle(candles_to_frame(rows))
        if not ind.get("ok"):
            return None
        if pos.side is Side.BUY:
            if ind.get("macd_hist", 0) < 0 and ind.get("rsi", 50) < 45:
                return "MACD reversal + RSI weakening"
            if ltp < float(ind.get("vwap") or ltp) and ind.get("st_direction") == -1:
                return "VWAP breakdown + SuperTrend flip"
            if float(ind.get("volume_ratio") or 1) < 0.6 and ind.get("momentum_5", 0) < 0:
                return "Volume exhaustion"
        else:
            if ind.get("macd_hist", 0) > 0 and ind.get("rsi", 50) > 55:
                return "MACD reversal + RSI rising"
            if ltp > float(ind.get("vwap") or ltp) and ind.get("st_direction") == 1:
                return "VWAP reclaim + SuperTrend flip"
        return None

    def _close(self, pos: OpenPosition, ltp: float, reason: str) -> dict[str, Any]:
        if pos.side is Side.BUY:
            pnl = (ltp - pos.entry) / pos.entry * 100.0
        else:
            pnl = (pos.entry - ltp) / pos.entry * 100.0
        holding = (datetime.now(timezone.utc) - pos.opened_at).total_seconds()
        self.store.save_outcome(
            pos.signal_id,
            pnl_pct=pnl,
            holding_seconds=holding,
            exit_reason=reason,
            payload={
                "instrument": pos.instrument,
                "side": pos.side.value,
                "entry": pos.entry,
                "exit": ltp,
                "created_at": pos.opened_at.isoformat(),
            },
        )
        label = reason.replace("_", " ").title()
        self.notify(
            f"{'✅' if pnl >= 0 else '🛑'} *{label}*\n`{pos.instrument}`\nPnL `{pnl:+.2f}%` @ `{ltp:.2f}`"
        )
        return {"type": reason, "instrument": pos.instrument, "pnl_pct": pnl, "ltp": ltp}
