"""Technical indicator suite for the watcher (pure pandas / numpy)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def candles_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi", "vwap"])
    df = pd.DataFrame(rows)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df = df.set_index("ts").sort_index()
    for col in ("open", "high", "low", "close", "volume", "oi", "vwap"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    # Avoid div-by-zero / all-gain windows producing NaN.
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.fillna(100.0).where(avg_loss > 0, 100.0)
    out = out.mask((avg_gain <= 0) & (avg_loss > 0), 0.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": hist})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = atr(high, low, close, period=1)  # raw TR then smooth below
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    alpha = 1 / period
    atr_s = tr.ewm(alpha=alpha, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=period).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=period).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame(
        {
            "plus_di": plus_di,
            "minus_di": minus_di,
            "adx": dx.ewm(alpha=alpha, min_periods=period).mean(),
        }
    )


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": mid + num_std * std,
            "bb_lower": mid - num_std * std,
            "bb_width": (2 * num_std * std) / mid.replace(0, np.nan),
        }
    )


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    atr_v = atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr_v
    lower = hl2 - multiplier * atr_v
    st = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(1, index=close.index, dtype=int)
    for i in range(len(close)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            continue
        if close.iloc[i] > st.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < st.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
        if direction.iloc[i] == 1:
            st.iloc[i] = max(lower.iloc[i], st.iloc[i - 1]) if direction.iloc[i - 1] == 1 else lower.iloc[i]
        else:
            st.iloc[i] = min(upper.iloc[i], st.iloc[i - 1]) if direction.iloc[i - 1] == -1 else upper.iloc[i]
    return pd.DataFrame({"supertrend": st, "st_direction": direction})


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    chikou = close.shift(-26)
    return pd.DataFrame(
        {
            "tenkan": tenkan,
            "kijun": kijun,
            "span_a": span_a,
            "span_b": span_b,
            "chikou": chikou,
        }
    )


def pivots(high: float, low: float, close: float) -> dict[str, float]:
    pp = (high + low + close) / 3.0
    return {
        "pp": pp,
        "r1": 2 * pp - low,
        "s1": 2 * pp - high,
        "r2": pp + (high - low),
        "s2": pp - (high - low),
        "r3": high + 2 * (pp - low),
        "s3": low - 2 * (high - pp),
    }


def support_resistance(close: pd.Series, window: int = 20) -> dict[str, float]:
    if len(close) < window:
        last = float(close.iloc[-1]) if len(close) else 0.0
        return {"support": last * 0.98, "resistance": last * 1.02}
    recent = close.iloc[-window:]
    return {"support": float(recent.min()), "resistance": float(recent.max())}


def vwap_series(df: pd.DataFrame) -> pd.Series:
    if "vwap" in df.columns and df["vwap"].fillna(0).ne(0).any():
        return df["vwap"].replace(0, np.nan).ffill()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    cum_vp = (typical * vol).cumsum()
    cum_v = vol.cumsum()
    return cum_vp / cum_v


def compute_indicator_bundle(df: pd.DataFrame) -> dict[str, Any]:
    """Compute the full indicator snapshot used by the decision engine."""
    if df.empty or len(df) < 30:
        return {"ok": False, "reason": "insufficient_bars", "bars": len(df)}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"].fillna(0)

    ema9 = ema(close, 9)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200) if len(df) >= 200 else ema(close, min(200, len(df)))
    rsi_v = rsi(close)
    macd_df = macd(close)
    atr_v = atr(high, low, close)
    adx_df = adx(high, low, close)
    bb = bollinger(close)
    st = supertrend(high, low, close)
    ichi = ichimoku(high, low, close)
    vwap = vwap_series(df)
    sr = support_resistance(close)
    last_day = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    piv = pivots(float(last_day["high"]), float(last_day["low"]), float(last_day["close"]))

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    vol_avg = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean() or 1)
    vol_ratio = float(volume.iloc[-1] / vol_avg) if vol_avg else 1.0

    golden = bool(ema50.iloc[-1] > ema200.iloc[-1] and ema50.iloc[-2] <= ema200.iloc[-2]) if len(df) >= 2 else False
    death = bool(ema50.iloc[-1] < ema200.iloc[-1] and ema50.iloc[-2] >= ema200.iloc[-2]) if len(df) >= 2 else False
    ema_cross_up = bool(ema20.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-2] <= ema50.iloc[-2]) if len(df) >= 2 else False
    ema_cross_dn = bool(ema20.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-2] >= ema50.iloc[-2]) if len(df) >= 2 else False

    gap_pct = 0.0
    if float(prev["close"]):
        gap_pct = (float(last["open"]) - float(prev["close"])) / float(prev["close"]) * 100.0

    breakout = float(last["close"]) >= sr["resistance"] * 0.998
    breakdown = float(last["close"]) <= sr["support"] * 1.002
    mom = float(close.pct_change(5).iloc[-1] or 0) * 100.0

    oi_change = 0.0
    if "oi" in df.columns and len(df) >= 2 and float(prev.get("oi") or 0):
        oi_change = (float(last.get("oi") or 0) - float(prev.get("oi") or 0)) / float(prev["oi"]) * 100.0

    return {
        "ok": True,
        "bars": len(df),
        "close": float(last["close"]),
        "ema9": float(ema9.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "rsi": float(rsi_v.iloc[-1]),
        "macd": float(macd_df["macd"].iloc[-1]),
        "macd_signal": float(macd_df["macd_signal"].iloc[-1]),
        "macd_hist": float(macd_df["macd_hist"].iloc[-1]),
        "atr": float(atr_v.iloc[-1]),
        "adx": float(adx_df["adx"].iloc[-1]),
        "plus_di": float(adx_df["plus_di"].iloc[-1]),
        "minus_di": float(adx_df["minus_di"].iloc[-1]),
        "bb_upper": float(bb["bb_upper"].iloc[-1]),
        "bb_lower": float(bb["bb_lower"].iloc[-1]),
        "bb_mid": float(bb["bb_mid"].iloc[-1]),
        "supertrend": float(st["supertrend"].iloc[-1]),
        "st_direction": int(st["st_direction"].iloc[-1]),
        "tenkan": float(ichi["tenkan"].iloc[-1]) if pd.notna(ichi["tenkan"].iloc[-1]) else None,
        "kijun": float(ichi["kijun"].iloc[-1]) if pd.notna(ichi["kijun"].iloc[-1]) else None,
        "vwap": float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else float(last["close"]),
        "support": sr["support"],
        "resistance": sr["resistance"],
        "pivots": piv,
        "volume_ratio": vol_ratio,
        "golden_cross": golden,
        "death_cross": death,
        "ema20_cross_ema50_up": ema_cross_up,
        "ema20_cross_ema50_dn": ema_cross_dn,
        "breakout": breakout,
        "breakdown": breakdown,
        "gap_pct": gap_pct,
        "momentum_5": mom,
        "oi_change_pct": oi_change,
        "trend": (
            "bullish"
            if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) > float(ema200.iloc[-1])
            else "bearish"
            if float(ema20.iloc[-1]) < float(ema50.iloc[-1]) < float(ema200.iloc[-1])
            else "neutral"
        ),
    }
