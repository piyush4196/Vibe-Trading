"""Decision engine — assemble multi-TF analysis into a Signal or reject."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.watcher.config import WatcherConfig
from src.watcher.indicators import candles_to_frame, compute_indicator_bundle
from src.watcher.models import HoldingType, MarketContext, Side, Signal, WatchInstrument, utc_now
from src.watcher.risk import build_risk_plan
from src.watcher.scoring import blend_mtf_scores, mtf_alignment, score_side

logger = logging.getLogger(__name__)

ANALYSIS_TFS = ("1m", "5m", "15m", "1h", "1D")


def _strategy_name(ind: dict[str, Any], side: Side) -> str:
    if ind.get("breakout") and side is Side.BUY:
        return "Momentum Breakout"
    if ind.get("breakdown") and side is Side.SELL:
        return "Breakdown Short"
    if ind.get("ema20_cross_ema50_up") and side is Side.BUY:
        return "EMA Cross Momentum"
    if ind.get("ema20_cross_ema50_dn") and side is Side.SELL:
        return "EMA Cross Mean-Reversion Short"
    if ind.get("golden_cross"):
        return "Golden Cross Trend"
    return "Multi-Timeframe Trend Continuity"


def _holding(ind: dict[str, Any], side: Side) -> tuple[HoldingType, str]:
    adx_v = float(ind.get("adx") or 0)
    if adx_v >= 30 and ind.get("trend") in ("bullish", "bearish"):
        return HoldingType.SWING, "1-5 sessions"
    if float(ind.get("volume_ratio") or 1) >= 2:
        return HoldingType.INTRADAY, "same session"
    return HoldingType.INTRADAY, "few hours"


def evaluate_instrument(
    instrument: WatchInstrument,
    bars_by_tf: dict[str, list[dict[str, Any]]],
    *,
    config: WatcherConfig,
    market_ctx: MarketContext,
    live_tick: dict[str, Any] | None = None,
    confidence_calibration: float = 0.0,
) -> Signal | None:
    """Return a Signal only when confidence ≥ threshold and risk gates pass."""
    per_tf_ind: dict[str, dict[str, Any]] = {}
    for tf in ANALYSIS_TFS:
        rows = bars_by_tf.get(tf) or []
        per_tf_ind[tf] = compute_indicator_bundle(candles_to_frame(rows))

    primary = per_tf_ind.get("15m") or per_tf_ind.get("5m") or {}
    if not primary.get("ok"):
        return None

    best: Signal | None = None
    for side in (Side.BUY, Side.SELL):
        ok_mtf, labels, mtf_notes = mtf_alignment(per_tf_ind, side)
        if not ok_mtf:
            continue

        tf_scores: dict[str, float] = {}
        reason_pool: list[str] = []
        for tf, ind in per_tf_ind.items():
            if not ind.get("ok"):
                continue
            sc, reasons = score_side(ind, side, market_ctx)
            tf_scores[tf] = sc
            if tf in ("15m", "1h", "5m"):
                reason_pool.extend(reasons)

        confidence = blend_mtf_scores(tf_scores) + confidence_calibration
        confidence = max(0.0, min(100.0, confidence))
        if confidence < config.min_confidence:
            continue

        plan = build_risk_plan(
            primary,
            side,
            min_rr=config.min_rr,
            preferred_rr=config.preferred_rr,
            tick=live_tick,
        )
        if not plan.accepted:
            continue

        holding, expect = _holding(primary, side)
        reasons = []
        # Deduplicate while preserving order
        seen = set()
        for r in mtf_notes + reason_pool + plan.reasons:
            if r not in seen:
                seen.add(r)
                reasons.append(r)

        signal = Signal(
            signal_id=str(uuid.uuid4()),
            instrument=instrument.symbol,
            instrument_key=instrument.instrument_key,
            market=instrument.market,
            side=side,
            strategy=_strategy_name(primary, side),
            entry=plan.entry,
            stop_loss=plan.stop_loss,
            target_1=plan.target_1,
            target_2=plan.target_2,
            target_3=plan.target_3,
            confidence=round(confidence, 1),
            risk_reward=plan.risk_reward,
            holding_type=holding,
            expected_holding=expect,
            reasons=reasons[:14],
            timeframe_alignment=labels,
            indicators_snapshot={k: per_tf_ind[k] for k in ("5m", "15m", "1h", "1D") if k in per_tf_ind},
            market_context={
                "nifty_trend": market_ctx.nifty_trend,
                "banknifty_trend": market_ctx.banknifty_trend,
                "india_vix": market_ctx.india_vix,
                "breadth_score": market_ctx.breadth_score,
            },
            created_at=utc_now(),
        )
        if best is None or signal.confidence > best.confidence:
            best = signal
    return best
