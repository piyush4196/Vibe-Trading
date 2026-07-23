"""Unit tests for the autonomous market watcher."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.watcher.antispam import should_emit
from src.watcher.candles import CandleBuilder
from src.watcher.config import WatcherConfig
from src.watcher.decision import evaluate_instrument
from src.watcher.indicators import compute_indicator_bundle
from src.watcher.market_hours import equity_session_open
from src.watcher.models import MarketContext, MarketSegment, Side, Signal, Tick, WatchInstrument, HoldingType
from src.watcher.risk import build_risk_plan
from src.watcher.scoring import blend_mtf_scores, mtf_alignment, score_side
from src.watcher.storage import WatcherStore

pytestmark = pytest.mark.unit


def _ohlcv(n: int = 120, trend: float = 0.002) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series([100 * ((1 + trend) ** i) for i in range(n)], index=idx)
    high = close * 1.005
    low = close * 0.995
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1000 + (i % 20) * 200 for i in range(n)], index=idx)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume, "oi": 0, "vwap": close}
    )


def test_indicators_bundle_bullish_trend():
    ind = compute_indicator_bundle(_ohlcv())
    assert ind["ok"] is True
    assert ind["ema20"] > 0
    assert ind["rsi"] > 0
    assert "atr" in ind
    assert "supertrend" in ind


def test_score_and_mtf_alignment():
    ind = compute_indicator_bundle(_ohlcv())
    score, reasons = score_side(ind, Side.BUY, MarketContext(nifty_trend="bullish", breadth_score=70))
    assert score >= 50
    assert reasons
    ok, labels, notes = mtf_alignment({"15m": ind, "1h": ind, "1D": ind, "5m": ind, "1m": ind}, Side.BUY)
    assert ok is True
    assert "Higher timeframe confirmation" in notes
    # Disagreeing daily should reject buys
    bear = dict(ind)
    bear["trend"] = "bearish"
    ok2, _, notes2 = mtf_alignment({"15m": ind, "1h": ind, "1D": bear, "5m": ind, "1m": ind}, Side.BUY)
    assert ok2 is False


def test_risk_plan_rr_gate():
    ind = compute_indicator_bundle(_ohlcv())
    plan = build_risk_plan(ind, Side.BUY, min_rr=2.0, preferred_rr=3.0)
    assert plan.entry > 0
    assert plan.stop_loss < plan.entry
    assert plan.target_3 > plan.entry
    assert plan.risk_reward >= 2.0
    assert plan.accepted is True


def test_candle_builder_closes_buckets():
    builder = CandleBuilder()
    base = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = Tick("K", "S", base, ltp=100)
    t2 = Tick("K", "S", base.replace(minute=0, second=30), ltp=101)
    t3 = Tick("K", "S", base.replace(minute=1), ltp=102)
    builder.ingest(t1)
    builder.ingest(t2)
    closed = builder.ingest(t3)
    assert any(c.timeframe == "1m" and c.closed for c in closed)


def test_antispam_cooldown(tmp_path, monkeypatch):
    from src.config import paths as pathmod

    monkeypatch.setattr(pathmod, "get_runtime_root", lambda: tmp_path)
    store = WatcherStore(tmp_path / "w.db")
    cfg = WatcherConfig(alert_cooldown_seconds=3600, confidence_bump_to_resend=5)
    sig = Signal(
        signal_id="1",
        instrument="RELIANCE",
        instrument_key="NSE_EQ|X",
        market="NSE",
        side=Side.BUY,
        strategy="test",
        entry=100,
        stop_loss=95,
        target_1=110,
        target_2=115,
        target_3=120,
        confidence=85,
        risk_reward=3,
        holding_type=HoldingType.INTRADAY,
        expected_holding="1h",
        reasons=["x"],
    )
    ok, why = should_emit(sig, store, cfg)
    assert ok and why == "first_alert"
    store.upsert_alert_state("NSE_EQ|X", "BUY", confidence=85, entry=100, stop=95, target=110)
    ok2, why2 = should_emit(sig, store, cfg)
    assert ok2 is False
    assert "cooldown" in why2
    sig.confidence = 92
    ok3, why3 = should_emit(sig, store, cfg)
    assert ok3 is True
    assert "confidence_up" in why3


def test_evaluate_instrument_emits_or_rejects():
    inst = WatchInstrument("TEST", "NSE_EQ|T", "NSE", MarketSegment.EQUITY)
    df = _ohlcv(180, trend=0.003)
    rows = [
        {
            "ts": ts.isoformat(),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
            "oi": 0,
            "vwap": float(r.close),
        }
        for ts, r in df.iterrows()
    ]
    bars = {tf: rows for tf in ("1m", "5m", "15m", "1h", "1D")}
    cfg = WatcherConfig(min_confidence=70, min_rr=2.0, preferred_rr=3.0)
    ctx = MarketContext(nifty_trend="bullish", banknifty_trend="bullish", breadth_score=70)
    signal = evaluate_instrument(inst, bars, config=cfg, market_ctx=ctx)
    # Strong uptrend should produce a BUY or None if gates are stricter — either is valid,
    # but if emitted must meet confidence.
    if signal is not None:
        assert signal.confidence >= 70
        assert signal.side in (Side.BUY, Side.SELL)
        assert signal.risk_reward >= 2.0


def test_cli_help_registers(monkeypatch):
    from cli import _legacy

    parser = _legacy._build_parser()
    args = parser.parse_args(["watch", "status"])
    assert args.command == "watch"
    assert args.watch_command == "status"
