"""Learning engine — calibrate confidence from historical outcomes."""

from __future__ import annotations

from typing import Any

from src.watcher.storage import WatcherStore


class LearningEngine:
    def __init__(self, store: WatcherStore):
        self.store = store

    def confidence_calibration(self) -> float:
        """Return a small additive adjustment based on recent hit-rate."""
        rows = self.store.recent_outcomes(limit=100)
        if len(rows) < 10:
            return 0.0
        wins = sum(1 for r in rows if float(r.get("pnl_pct") or 0) > 0)
        rate = wins / len(rows)
        # If historical win-rate is poor, be stricter (negative calibration).
        if rate < 0.45:
            return -5.0
        if rate > 0.6:
            return 2.0
        return 0.0

    def summary(self) -> dict[str, Any]:
        rows = self.store.recent_outcomes(limit=200)
        if not rows:
            return {"trades": 0}
        pnls = [float(r.get("pnl_pct") or 0) for r in rows]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "trades": len(rows),
            "win_rate": round(wins / len(rows), 3),
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 3),
            "calibration": self.confidence_calibration(),
        }
