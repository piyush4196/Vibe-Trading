"""Confidence scoring (0-100) and multi-timeframe alignment."""

from __future__ import annotations

from typing import Any

from src.watcher.models import MarketContext, Side


MTF_ORDER = ("1m", "5m", "15m", "1h", "1D")


def score_side(ind: dict[str, Any], side: Side, ctx: MarketContext | None = None) -> tuple[float, list[str]]:
    """Return (confidence, reasons) for a candidate side on one timeframe."""
    if not ind.get("ok"):
        return 0.0, ["insufficient indicator data"]

    score = 50.0
    reasons: list[str] = []
    close = float(ind["close"])

    # Trend
    if side is Side.BUY:
        if ind["trend"] == "bullish":
            score += 12
            reasons.append("Bullish EMA stack (20>50>200)")
        elif ind["trend"] == "bearish":
            score -= 15
            reasons.append("Against bearish higher structure")
        if ind.get("ema20_cross_ema50_up"):
            score += 10
            reasons.append("EMA 20 crossed EMA 50")
        if ind.get("golden_cross"):
            score += 8
            reasons.append("Golden Cross (EMA50>EMA200)")
        if ind.get("st_direction", 0) == 1:
            score += 6
            reasons.append("SuperTrend bullish")
        if close > float(ind.get("vwap") or close):
            score += 5
            reasons.append("Price above VWAP")
        if ind.get("breakout"):
            score += 8
            reasons.append("Resistance breakout")
        if ind.get("macd_hist", 0) > 0 and ind.get("macd", 0) > ind.get("macd_signal", 0):
            score += 6
            reasons.append("MACD bullish")
    else:
        if ind["trend"] == "bearish":
            score += 12
            reasons.append("Bearish EMA stack (20<50<200)")
        elif ind["trend"] == "bullish":
            score -= 15
            reasons.append("Against bullish higher structure")
        if ind.get("ema20_cross_ema50_dn"):
            score += 10
            reasons.append("EMA 20 crossed below EMA 50")
        if ind.get("death_cross"):
            score += 8
            reasons.append("Death Cross (EMA50<EMA200)")
        if ind.get("st_direction", 0) == -1:
            score += 6
            reasons.append("SuperTrend bearish")
        if close < float(ind.get("vwap") or close):
            score += 5
            reasons.append("Price below VWAP")
        if ind.get("breakdown"):
            score += 8
            reasons.append("Support breakdown")
        if ind.get("macd_hist", 0) < 0 and ind.get("macd", 0) < ind.get("macd_signal", 0):
            score += 6
            reasons.append("MACD bearish")

    # Momentum / RSI
    rsi_v = float(ind.get("rsi") or 50)
    if side is Side.BUY and 52 <= rsi_v <= 68:
        score += 6
        reasons.append(f"RSI {rsi_v:.0f}")
    elif side is Side.BUY and rsi_v > 75:
        score -= 8
        reasons.append(f"RSI overbought {rsi_v:.0f}")
    elif side is Side.SELL and 32 <= rsi_v <= 48:
        score += 6
        reasons.append(f"RSI {rsi_v:.0f}")
    elif side is Side.SELL and rsi_v < 25:
        score -= 8
        reasons.append(f"RSI oversold {rsi_v:.0f}")

    # Volume
    vol_r = float(ind.get("volume_ratio") or 1)
    if vol_r >= 2.0:
        score += 8
        reasons.append(f"Volume {vol_r:.1f}x average")
    elif vol_r >= 1.4:
        score += 4
        reasons.append(f"Volume {vol_r:.1f}x average")
    elif vol_r < 0.7:
        score -= 6
        reasons.append("Weak volume")

    # ADX / momentum
    adx_v = float(ind.get("adx") or 0)
    if adx_v >= 25:
        score += 5
        reasons.append(f"ADX {adx_v:.0f} (trend strength)")
    mom = float(ind.get("momentum_5") or 0)
    if side is Side.BUY and mom > 1:
        score += 3
    if side is Side.SELL and mom < -1:
        score += 3

    # OI
    oi = float(ind.get("oi_change_pct") or 0)
    if abs(oi) >= 3:
        score += 3
        reasons.append(f"OI change {oi:+.1f}%")

    # Market filter adjustments
    if ctx is not None:
        if side is Side.BUY and ctx.nifty_trend == "bearish":
            score -= 10
            reasons.append("Market filter: Nifty bearish — buy confidence cut")
        if side is Side.SELL and ctx.nifty_trend == "bullish":
            score -= 10
            reasons.append("Market filter: Nifty bullish — sell confidence cut")
        if ctx.india_vix is not None and ctx.india_vix > 20 and side is Side.BUY:
            score -= 4
            reasons.append(f"Elevated India VIX {ctx.india_vix:.1f}")
        if ctx.breadth_score >= 65 and side is Side.BUY:
            score += 4
            reasons.append("Positive market breadth")
        if ctx.breadth_score <= 35 and side is Side.SELL:
            score += 4
            reasons.append("Weak market breadth")

    return max(0.0, min(100.0, score)), reasons


def mtf_alignment(
    per_tf: dict[str, dict[str, Any]],
    side: Side,
) -> tuple[bool, dict[str, str], list[str]]:
    """Require higher timeframes not to disagree with ``side``."""
    labels: dict[str, str] = {}
    notes: list[str] = []
    for tf in MTF_ORDER:
        ind = per_tf.get(tf) or {}
        if not ind.get("ok"):
            labels[tf] = "unknown"
            continue
        trend = str(ind.get("trend") or "neutral")
        labels[tf] = trend

    # Higher TF disagreement gate: 15m, 1h, 1D must not oppose.
    oppose = "bearish" if side is Side.BUY else "bullish"
    for tf in ("15m", "1h", "1D"):
        if labels.get(tf) == oppose:
            notes.append(f"Higher timeframe {tf} is {oppose} — reject")
            return False, labels, notes
    notes.append("Higher timeframe confirmation")
    return True, labels, notes


def blend_mtf_scores(scores: dict[str, float]) -> float:
    """Weight higher timeframes more heavily."""
    weights = {"1m": 0.1, "5m": 0.15, "15m": 0.25, "1h": 0.25, "1D": 0.25}
    total_w = 0.0
    acc = 0.0
    for tf, w in weights.items():
        if tf in scores:
            acc += scores[tf] * w
            total_w += w
    if total_w <= 0:
        return 0.0
    return acc / total_w
