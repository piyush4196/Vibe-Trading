"""Risk engine — liquidity, RR, spread, market alignment gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.watcher.models import Side


@dataclass
class RiskPlan:
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_reward: float
    accepted: bool
    reasons: list[str]


def build_risk_plan(
    ind: dict[str, Any],
    side: Side,
    *,
    min_rr: float = 2.0,
    preferred_rr: float = 3.0,
    tick: dict[str, Any] | None = None,
) -> RiskPlan:
    """Derive SL/targets from ATR + structure; reject weak RR / liquidity."""
    reasons: list[str] = []
    close = float(ind.get("close") or 0)
    atr_v = float(ind.get("atr") or 0) or close * 0.01
    support = float(ind.get("support") or close * 0.98)
    resistance = float(ind.get("resistance") or close * 1.02)

    if side is Side.BUY:
        stop = min(support, close - 1.2 * atr_v)
        risk = max(close - stop, close * 0.004)
        t1 = close + preferred_rr * 0.45 * risk
        t2 = close + preferred_rr * 0.75 * risk
        t3 = close + preferred_rr * risk
        # Prefer structure target if further
        t1 = max(t1, min(resistance, close + risk))
        rr = (t3 - close) / risk if risk else 0
    else:
        stop = max(resistance, close + 1.2 * atr_v)
        risk = max(stop - close, close * 0.004)
        t1 = close - preferred_rr * 0.45 * risk
        t2 = close - preferred_rr * 0.75 * risk
        t3 = close - preferred_rr * risk
        t1 = min(t1, max(support, close - risk))
        rr = (close - t3) / risk if risk else 0

    accepted = True
    if rr < min_rr:
        accepted = False
        reasons.append(f"Risk-reward 1:{rr:.1f} below minimum 1:{min_rr:.0f}")
    else:
        reasons.append(f"Risk-reward 1:{rr:.1f}")

    # Spread / liquidity gates from live tick when present.
    if tick:
        bid = float(tick.get("bid") or 0)
        ask = float(tick.get("ask") or 0)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_bps = (ask - bid) / mid * 10000 if mid else 0
            if spread_bps > 25:
                accepted = False
                reasons.append(f"Spread too wide ({spread_bps:.0f} bps)")
        vol_r = float(ind.get("volume_ratio") or 1)
        if vol_r < 0.5:
            accepted = False
            reasons.append("Low liquidity (volume)")

    return RiskPlan(
        entry=round(close, 2),
        stop_loss=round(stop, 2),
        target_1=round(t1, 2),
        target_2=round(t2, 2),
        target_3=round(t3, 2),
        risk_reward=round(rr, 2),
        accepted=accepted,
        reasons=reasons,
    )
